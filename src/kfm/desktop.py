"""The fleet window (`kfm-desktop`): the fleet's agents, what passes between them, and the
person's part in it. A frontend on the Manager, like the terminal's Console: its events
in, its methods out. Built on Kayden's desktop code: its event loop, theme and Markdown
view."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Coroutine
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import kayden.frontends.desktop  # noqa: F401 - before any Qt import
from kayden.frontends.desktop import theme
from kayden.frontends.desktop.loop import QtEventLoop
from kayden.frontends.desktop.window import (
    DATE_ROLE,
    PREVIEW_ROLE,
    WINDOW_BUTTON,
    DragArea,
    ElidedLabel,
    MarkdownView,
    MessageBox,
    RowDelegate,
    WindowEdges,
)
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QDoubleValidator, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kfm import app_menu, log
from kfm.api import Harness, UnitError
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load
from kfm.events import Answered, Approval, Changed, Delivered, FleetEvent, Notice, Said, Told
from kfm.fleet import RECORD, Fleet, FleetError, Unit
from kfm.manager import Manager

SIDEBAR = 280  # px
FORM = 760  # px, the start screen's width
LOGO = 64  # px, on the start screen
AGENT_ROWS = 10  # agents shown before their list scrolls
AGENT_ROW_PX = 30
DEFAULT_WORK = "kfm-work"  # in the home folder: where a first fleet works
FOLDER_ROLE = Qt.ItemDataRole.UserRole  # a fleet row's folder
QUIET_STATES = ("starting", "working", "waiting", "done")  # shown by the agents list alone
COLUMN = 860  # px, the widest the timeline's entries get
REFRESH_MS = 1000  # spending and states, while a fleet runs
TITLE_CHARS = 90
UNIT_ROLE = Qt.ItemDataRole.UserRole  # a tree row's unit id


def label(text: str = "", *, role: str | None = None, tone: str | None = None,
          wrap: bool = True) -> QLabel:
    made = QLabel(text)
    made.setTextFormat(Qt.TextFormat.PlainText)
    made.setWordWrap(wrap)
    made.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    made.setProperty("role", role)
    made.setProperty("tone", tone)
    return made


def _when(started: str) -> str:
    """A fleet folder's name (20261112-210500) as its row's date: the time today, else
    the day."""
    try:
        at = time.strptime(started, "%Y%m%d-%H%M%S")
    except ValueError:
        return ""
    today = time.localtime()
    if (at.tm_year, at.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", at)
    return time.strftime("%-d %b", at)


def _clip(text: str, chars: int = TITLE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars] + "…"


# ── The timeline's entries. `concerns`: the units an entry is about; none: everyone. ──


class Line(QLabel):
    """One quiet line: an agent's state changed, or the fleet says something."""

    def __init__(self, text: str, concerns: set[str], tone: str = "muted") -> None:
        super().__init__(text)
        self.concerns = concerns
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setProperty("role", "small")
        self.setProperty("tone", tone)


class SaidEntry(QWidget):
    """What an agent wrote in its own conversation."""

    def __init__(self, unit: Unit, text: str) -> None:
        super().__init__()
        self.concerns = {unit.id}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        self.header = label(f"{unit.name}  K{unit.rank}", role="small", tone="muted")
        self.body = MarkdownView()
        self.body.setMarkdown(text)
        layout.addWidget(self.header)
        layout.addWidget(self.body)


class DeliveryCard(QFrame):
    """A report, question, update or failure on its way up, with its whole text."""

    def __init__(self, event: Delivered) -> None:
        super().__init__()
        self.setObjectName("tool")
        self.concerns = {event.unit.id} | ({event.to.id} if event.to is not None else set())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        target = event.to.name if event.to is not None else "you"
        self.header = label(f"{event.unit.name} → {target} · {event.kind}", role="strong")
        self.body = MarkdownView()
        self.body.setMarkdown(event.text)
        layout.addWidget(self.header)
        layout.addWidget(self.body)


class ApprovalCard(QFrame):
    """An agent wants to do something its permissions do not cover."""

    def __init__(self, event: Approval) -> None:
        super().__init__()
        self.setObjectName("prompt")
        self.concerns = {event.unit.id}
        self.asked = event
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        self.question = label(f"{event.unit.name} wants to run {event.tool} {event.arguments}")
        buttons = QHBoxLayout()
        self.allow = QPushButton("Allow")
        self.allow.setProperty("primary", True)
        self.deny = QPushButton("Deny")
        buttons.addWidget(self.allow)
        buttons.addWidget(self.deny)
        buttons.addStretch()
        layout.addWidget(self.question)
        layout.addLayout(buttons)

    def answered(self, allow: bool) -> None:
        self.allow.hide()
        self.deny.hide()
        asked = self.asked
        self.question.setText(
            f"Allowed: {asked.unit.name} ran {asked.tool} {asked.arguments}" if allow else
            f"Denied: {asked.unit.name} wanted to run {asked.tool} {asked.arguments}")


class YouEntry(QWidget):
    """What the person said to the lead: a bubble on the right."""

    def __init__(self, text: str, lead: str | None) -> None:
        super().__init__()
        self.concerns = {lead} if lead else set()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.addStretch(1)
        bubble = QFrame(objectName="user")
        inside = QVBoxLayout(bubble)
        inside.setContentsMargins(12, 8, 12, 8)
        self.text = label(text)
        inside.addWidget(self.text)
        row.addWidget(bubble)


class TaskBox(MessageBox):
    """Where the fleet's task is written: Enter starts, Shift+Enter starts a new line."""

    MIN_LINES = 3
    MAX_LINES = 12


class Budget(QFrame):
    """The most a fleet may spend, as a small field: "Up to $" and the amount."""

    LEAST = 0.1

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("budget")
        self.setToolTip("The most the whole fleet may spend")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 10, 0)
        row.setSpacing(2)
        self.amount = QLineEdit()
        self.amount.setFrame(False)
        self.amount.setValidator(QDoubleValidator(self.LEAST, 100_000.0, 2, self))
        self.amount.setFixedWidth(52)
        self.amount.setAccessibleName("The most the fleet may spend, in dollars")
        row.addWidget(label("Up to $", tone="muted", wrap=False))
        row.addWidget(self.amount)
        self.setFixedHeight(36)
        self.setValue(10.0)

    def setValue(self, usd: float) -> None:
        self.amount.setText(f"{usd:g}")

    def value(self) -> float:
        """What is written, or the least a fleet can be given when that is no amount."""
        try:
            return max(self.LEAST, float(self.amount.text().replace(",", ".")))
        except ValueError:
            return self.LEAST


class StartForm(QWidget):
    """The window's empty state: one box for what the fleet should do, with what else a
    fleet needs along its lower edge."""

    submitted = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMaximumWidth(FORM)
        self.setMinimumWidth(480)
        colors = theme.current()
        self.setStyleSheet(
            f"QPushButton#segment {{ border: none; background: transparent; "
            f"color: {colors.muted}; padding: 6px 14px; border-radius: 8px; }}"
            f"QPushButton#segment:hover {{ color: {colors.text}; }}"
            f"QPushButton#segment:checked {{ background: {colors.accent_soft}; "
            f"color: {colors.accent_text}; }}"
            f"QFrame#segments {{ border: 1px solid {colors.line}; border-radius: 10px; }}"
            f"QFrame#budget {{ border: 1px solid {colors.line}; border-radius: 10px; }}"
            f"QFrame#budget QLineEdit {{ background: transparent; border: none; "
            f"padding: 0; }}")
        self.folder = Path.home() / DEFAULT_WORK
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.addStretch(3)
        logo = QLabel()
        logo.setPixmap(QIcon(str(app_menu.ICON)).pixmap(LOGO, LOGO))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        heading = label("What should your fleet do?", wrap=False)
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(logo)
        outer.addSpacing(14)
        outer.addWidget(heading)
        outer.addSpacing(22)

        box = QFrame(objectName="composer")
        inside = QVBoxLayout(box)
        inside.setContentsMargins(16, 14, 12, 12)
        inside.setSpacing(12)
        self.task = TaskBox()
        self.task.setPlaceholderText("A task, or a role to hold")
        self.task.submitted.connect(self.submitted)
        inside.addWidget(self.task)
        row = QHBoxLayout()
        row.setSpacing(8)
        segments = QFrame(objectName="segments")
        kinds = QHBoxLayout(segments)
        kinds.setContentsMargins(3, 3, 3, 3)
        kinds.setSpacing(2)
        self.task_kind = QPushButton("Task", objectName="segment")
        self.duty = QPushButton("Duty", objectName="segment")
        self._kinds = QButtonGroup(self)
        for button in (self.task_kind, self.duty):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._kinds.addButton(button)
            kinds.addWidget(button)
        self.task_kind.setChecked(True)
        self.folder_button = QToolButton(objectName="folderPicker")
        self.folder_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.folder_button.setIcon(theme.icon("folder", colors.muted, 16))
        self.folder_button.setToolTip("The folder the agents work in")
        self.folder_button.clicked.connect(self._choose)
        self.budget = Budget()
        self.start = QToolButton(objectName="send")
        self.start.setProperty("active", True)
        self.start.setIcon(theme.icon("send", colors.on_accent, 18))
        self.start.setFixedSize(36, 36)
        self.start.setToolTip("Start the fleet")
        self.start.setAccessibleName("Start the fleet")
        self.start.clicked.connect(self.submitted)
        row.addWidget(segments)
        row.addWidget(self.folder_button)
        row.addWidget(self.budget)
        row.addStretch(1)
        row.addWidget(self.start)
        inside.addLayout(row)
        outer.addWidget(box)
        outer.addSpacing(10)
        self.hint = label("", role="small", tone="muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.warning = label("", role="small", tone="error")
        self.warning.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.warning.hide()
        outer.addWidget(self.hint)
        outer.addWidget(self.warning)
        outer.addStretch(5)
        self._kinds.buttonToggled.connect(lambda *_: self._describe())
        self._describe()
        self.set_folder(self.folder)

    def sizeHint(self) -> QSize:
        return QSize(FORM, super().sizeHint().height())

    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder).expanduser()
        self.folder_button.setText(f" {self.folder.name or self.folder}")
        self.folder_button.setToolTip(f"The agents work in {self.folder}")
        self.warn("")

    def warn(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def _describe(self) -> None:
        self.hint.setText(
            "Runs until you stop it." if self.duty.isChecked() else
            "Ends when the lead reports.")

    def _choose(self) -> None:
        dialog = QFileDialog(self, "The folder the fleet works in", str(self.folder.parent))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.fileSelected.connect(lambda chosen: self.set_folder(Path(chosen)))
        dialog.open()


@dataclass(frozen=True, slots=True)
class FleetRow:
    """An earlier fleet, as the sidebar lists it."""

    folder: Path
    task: str
    started: str  # its folder's name: the date and time it was started
    agents: int
    spent_usd: float
    work: str


# ── The window ────────────────────────────────────────────────────────────


class FleetWindow(QMainWindow):
    """`home`: where fleets and their settings are kept (~/.kfm). `harness` and `config`
    replace what the settings name, for tests."""

    def __init__(self, home: Path = HOME, *, harness: Harness | None = None,
                 config: FleetConfig | None = None) -> None:
        super().__init__()
        self.home, self._harness, self._config = home, harness, config
        self.manager: Manager | None = None  # while a fleet runs
        self.fleet: Fleet | None = None  # the fleet shown, running or not
        self.folder: Path | None = None
        self.entries: list[QWidget] = []
        self.closed = asyncio.Event()
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._approvals: dict[str, ApprovalCard] = {}
        self._shown: str | None = None  # the agent whose entries are shown; None: all
        self._stopping = False  # its processes are ending
        self._folder_set = False  # the start screen's folder has been taken from a fleet
        self._tasks: set[asyncio.Task[None]] = set()
        self.setWindowTitle("KFM")
        # No title bar: the bar on top is where the window is grabbed, and has its controls.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._edges = WindowEdges(self)
        self._build()
        self.show_all.hide()
        self._timer = QTimer(self, interval=REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._refresh()
        self._run(self._offer_app_menu())
        self._run(self.list_fleets())

    def _build(self) -> None:
        self.agents = QTreeWidget()
        self.agents.setColumnCount(3)
        self.agents.setHeaderHidden(True)
        self.agents.setRootIsDecorated(False)
        self.agents.setIndentation(16)
        self.agents.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.agents.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        self.agents.setFrameShape(QFrame.Shape.NoFrame)
        self.agents.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.agents.currentItemChanged.connect(self._agent_chosen)
        self.new_button = QPushButton(" New fleet", objectName="nav")
        self.new_button.setIcon(theme.icon("plus", theme.current().text, 16))
        self.new_button.clicked.connect(lambda: self._run(self.new_fleet()))
        side = QFrame(objectName="fleetSide")
        side.setFixedWidth(SIDEBAR)
        colors = theme.current()
        side.setStyleSheet(
            f"QFrame#fleetSide {{ background: {colors.side}; }}"
            f"QTreeWidget {{ background: transparent; border: none; }}"
            f"QTreeWidget::item {{ padding: 5px 2px; }}"
            f"QTreeWidget::item:selected {{ background: {colors.select}; "
            f"color: {colors.text}; }}"
            f"QListWidget {{ background: transparent; border: none; }}")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.addWidget(self.new_button)
        side_layout.addSpacing(6)

        # The open fleet's agents, above the list of every fleet.
        self.show_all = QPushButton("Show all", objectName="nav")
        self.show_all.clicked.connect(lambda: self.agents.setCurrentItem(None))
        self.agents_section = QWidget()
        section = QVBoxLayout(self.agents_section)
        section.setContentsMargins(0, 0, 0, 8)
        heading = QHBoxLayout()
        heading.addWidget(label("Agents", role="small", tone="muted"), 1)
        heading.addWidget(self.show_all)
        section.addLayout(heading)
        section.addWidget(self.agents)
        side_layout.addWidget(self.agents_section)

        self.fleets = QListWidget(objectName="conversations")
        self.fleets.setItemDelegate(RowDelegate(self.fleets))
        self.fleets.setFrameShape(QFrame.Shape.NoFrame)
        self.fleets.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fleets.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fleets.itemClicked.connect(self._fleet_chosen)
        self.fleets_label = label("Fleets", role="small", tone="muted")
        self.fleets_hint = label("Stop this fleet to open another.", role="small",
                                 tone="muted")
        side_layout.addWidget(self.fleets_label)
        side_layout.addWidget(self.fleets, 1)
        side_layout.addWidget(self.fleets_hint)
        self.app_menu_button = QPushButton("Add to App Menu", objectName="nav")
        self.app_menu_button.clicked.connect(lambda: self._run(self._add_to_app_menu()))
        self.app_menu_button.hide()
        side_layout.addWidget(self.app_menu_button)

        self.title = ElidedLabel()
        self.title.setProperty("role", "heading")
        self.status = label("", role="small", tone="muted", wrap=False)
        self.stop_button = QPushButton("Stop")
        self.resume_button = QPushButton("Resume")
        self.resume_button.setProperty("primary", True)
        self.stop_button.clicked.connect(lambda: self._run(self.stop()))
        self.resume_button.clicked.connect(lambda: self._run(self.resume()))
        self.bar = DragArea()
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(20, 0, 0, 0)  # the controls reach the window's corner
        bar.setSpacing(4)
        bar.addWidget(self.title, 1)
        self._bar_gap = QWidget()  # keeps the controls at the right while no fleet is named
        bar.addWidget(self._bar_gap, 1)
        bar.addWidget(self.status)
        bar.addWidget(self.stop_button)
        bar.addWidget(self.resume_button)
        bar.addSpacing(8)
        self.window_buttons: dict[str, QToolButton] = {}
        for name, tip, command in (("minimize", "Minimize", self.showMinimized),
                                   ("maximize", "Maximize", self._toggle_maximized),
                                   ("close", "Close", self.close)):
            button = QToolButton(objectName="titleClose" if name == "close"
                                 else "titleControl")
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setIconSize(QSize(14, 14))
            button.setFixedSize(WINDOW_BUTTON)
            button.clicked.connect(command)
            self.window_buttons[name] = button
            bar.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self._paint_controls()

        self.start_form = StartForm()
        self.start_form.submitted.connect(self._start_clicked)
        # The timeline is one column in the middle, as wide as text reads well.
        page = QWidget(objectName="transcript")
        across = QHBoxLayout(page)
        across.setContentsMargins(0, 0, 0, 0)
        self._column = QWidget()
        self._column.setMaximumWidth(COLUMN)
        self._timeline = QVBoxLayout(self._column)
        self._timeline.setContentsMargins(4, 16, 4, 12)
        self._timeline.setSpacing(10)
        self._timeline.addStretch(1)
        across.addStretch(1)
        across.addWidget(self._column, 100)
        across.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(page)

        self.input = MessageBox()
        self.input.setPlaceholderText("Message the lead")
        self.input.submitted.connect(self._send_clicked)
        self.send_button = QToolButton(objectName="send")
        self.send_button.setProperty("active", True)
        self.send_button.setIcon(theme.icon("send", theme.current().on_accent, 18))
        self.send_button.setFixedSize(34, 34)
        self.send_button.setToolTip("Send")
        self.send_button.setAccessibleName("Send to the lead")
        self.send_button.clicked.connect(self._send_clicked)
        box = QFrame(objectName="composer")
        box.setMaximumWidth(COLUMN)
        inside = QHBoxLayout(box)
        inside.setContentsMargins(16, 10, 10, 10)
        inside.addWidget(self.input, 1)
        inside.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        composer = QHBoxLayout()
        composer.addStretch(1)
        composer.addWidget(box, 100)
        composer.addStretch(1)

        main = QVBoxLayout()
        main.setContentsMargins(20, 0, 20, 14)
        main.addWidget(self.start_form, 1, Qt.AlignmentFlag.AlignHCenter)
        main.addWidget(self.scroll, 1)
        self.composer = QWidget()
        self.composer.setLayout(composer)
        composer.setContentsMargins(0, 0, 0, 0)
        main.addWidget(self.composer)
        whole = QWidget()
        layout = QHBoxLayout(whole)
        layout.setContentsMargins(0, 0, 0, 0)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.bar)
        right.addLayout(main, 1)
        layout.addWidget(side)
        layout.addLayout(right, 1)
        self.setCentralWidget(whole)
        if self._config is not None:
            self.start_form.budget.setValue(self._config.budget_usd)
        else:
            self.start_form.budget.setValue(FleetConfig().budget_usd)

    # ── What the person does ─────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.manager is not None

    async def start_fleet(self, task: str, *, ongoing: bool, work: Path, budget: float) -> None:
        """A new fleet on the task, in a new folder under `home`."""
        await self.stop()
        try:
            config = self._config or await asyncio.to_thread(load, self.home / CONFIG_FILE)
            config = replace(config, budget_usd=budget)
            folder = self.home / "fleets" / time.strftime("%Y%m%d-%H%M%S")
            while folder.exists():  # a second fleet within the same second
                await asyncio.sleep(1)
                folder = self.home / "fleets" / time.strftime("%Y%m%d-%H%M%S")
            work = work.expanduser().resolve()
            await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
            manager = Manager(config, folder, work, harness=self._harness,
                              on_event=self._on_event)
        except (ValueError, OSError) as e:
            self.start_form.warn(f"The fleet could not start: {e}")
            return
        self._clear()
        self.manager, self.fleet, self.folder = manager, manager.fleet, folder
        self.start_form.task.clear()
        await manager.start()
        self._timer.start()
        self._refresh()
        await manager.lead(task, ongoing=ongoing)
        await self.list_fleets()

    async def open_fleet(self, folder: Path) -> None:
        """Show a fleet that is not running, with what happened in it."""
        await self.stop()
        try:
            fleet = await asyncio.to_thread(Fleet.load, folder)
            history = await asyncio.to_thread(log.read, folder, fleet)
        except (ValueError, OSError, KeyError) as e:
            self._say(f"{folder} could not be opened: {e}", tone="error")
            return
        self._clear()
        self.fleet, self.folder = fleet, folder
        for _, event in history:
            self._show(event)
        await self.list_fleets()

    async def new_fleet(self) -> None:
        """Back to the empty state; a running fleet is stopped."""
        await self.stop()
        self._clear()
        self.fleet = self.folder = None
        await self.list_fleets()
        self.start_form.task.setFocus()

    async def resume(self) -> None:
        if self.running or self.folder is None:
            return
        try:
            manager = await asyncio.to_thread(
                lambda: Manager.resumed(self.folder, harness=self._harness,
                                        on_event=self._on_event))
        except (ValueError, OSError) as e:  # FleetError: it runs elsewhere, or is out of money
            self._say(str(e), tone="error")
            return
        self.manager, self.fleet = manager, manager.fleet
        self._rows.clear()
        self.agents.clear()
        await manager.start()
        self._timer.start()
        self._refresh()

    async def stop(self) -> None:
        """Stop the fleet's processes; it stays shown and can be resumed."""
        if (manager := self.manager) is None:
            return
        self.manager = None
        self._timer.stop()
        self._stopping = True
        self._refresh()
        try:
            await manager.aclose()
        finally:
            self._stopping = False
            await self.list_fleets()

    async def shut(self) -> None:
        """Stop the fleet and close the window."""
        await self.stop()
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        self.closed.set()
        self.close()

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _paint_controls(self) -> None:
        maximized = self.isMaximized()
        for name, button in self.window_buttons.items():
            kind = "restore" if name == "maximize" and maximized else name
            button.setIcon(theme.icon(kind, theme.current().muted))
        self.window_buttons["maximize"].setToolTip("Restore" if maximized else "Maximize")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (event.type() in (QEvent.Type.PaletteChange, QEvent.Type.WindowStateChange)
                and hasattr(self, "window_buttons")):  # not while it is being built
            self._paint_controls()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.closed.is_set():
            self._run(self.shut())
        event.accept()

    def earlier_fleets(self) -> list[FleetRow]:
        """Every fleet under `home`, newest first. Blocking."""
        found = []
        for record in sorted((self.home / "fleets").glob(f"*/{RECORD}"), reverse=True):
            try:
                data = json.loads(record.read_text())
                units = data["units"]
                task = next(u["task"] for u in units if u["supervisor"] is None)
                spent = sum(u["spent_usd"] for u in units)
            except (ValueError, KeyError, TypeError, StopIteration, OSError):
                continue
            found.append(FleetRow(record.parent, task, record.parent.name, len(units), spent,
                                  data.get("work", "")))
        return found

    async def list_fleets(self) -> None:
        """Fill the sidebar's list of fleets, and the start screen's folder from the
        newest one."""
        rows = await asyncio.to_thread(self.earlier_fleets)
        fleet, folder = self.fleet, self.folder
        if fleet is not None and folder is not None and fleet.lead is not None:
            # The open fleet as it is now: its record may not be written yet.
            shown = FleetRow(folder, fleet.lead.task, folder.name, len(fleet.units),
                             fleet.spent_usd, fleet.work)
            rows = sorted([shown, *(row for row in rows if row.folder != folder)],
                          key=lambda row: row.started, reverse=True)
        self.fleets.clear()
        for row in rows:
            item = QListWidgetItem(_clip(row.task, 80))
            agents = "1 agent" if row.agents == 1 else f"{row.agents} agents"
            item.setData(PREVIEW_ROLE, f"{agents} · ${row.spent_usd:.2f}")
            item.setData(DATE_ROLE, _when(row.started))
            item.setData(FOLDER_ROLE, str(row.folder))
            item.setToolTip(row.task)
            self.fleets.addItem(item)
            if row.folder == self.folder:
                self.fleets.setCurrentItem(item)
        if rows and rows[0].work and not self._folder_set:
            self.start_form.set_folder(Path(rows[0].work))
        self._folder_set = True
        self._refresh()

    def _fleet_chosen(self, item: QListWidgetItem) -> None:
        folder = Path(item.data(FOLDER_ROLE))
        if not self.running and folder != self.folder:
            self._run(self.open_fleet(folder))

    async def _offer_app_menu(self) -> None:
        """The button is there until the app menu starts this install."""
        if app_menu.supported():
            self.app_menu_button.setVisible(not await asyncio.to_thread(app_menu.is_current))

    async def _add_to_app_menu(self) -> None:
        try:
            await asyncio.to_thread(app_menu.install)
        except OSError as e:
            self._say(f"KFM could not be added to the app menu: {e}", tone="error")
            return
        self.app_menu_button.hide()

    def _start_clicked(self) -> None:
        form = self.start_form
        task = form.task.toPlainText().strip()
        if not task:
            form.task.setFocus()
            return
        work = form.folder.expanduser().resolve()
        if work == Path.home().resolve():
            form.warn("Not your home folder: agents can change anything in the folder "
                      "they work in.")
            return
        self._run(self.start_fleet(task, ongoing=form.duty.isChecked(), work=work,
                                   budget=form.budget.value()))

    def _send_clicked(self) -> None:
        text = self.input.toPlainText().strip()
        if text and self.manager is not None:
            self.input.clear()
            self._run(self._tell(self.manager, text))

    async def _tell(self, manager: Manager, text: str) -> None:
        try:
            await manager.say(text)
        except (FleetError, UnitError) as e:
            self._say(str(e), tone="error")

    async def _answer(self, event: Approval, allow: bool) -> None:
        if self.manager is not None:
            try:
                await self.manager.approve(event.unit, event.request_id, allow)
            except UnitError as e:
                self._say(str(e), tone="error")

    # ── What the fleet says ──────────────────────────────────────────────

    def _on_event(self, event: FleetEvent) -> None:
        self._show(event)
        # After the manager has finished with the event: it may end the fleet's task.
        asyncio.get_running_loop().call_soon(self._refresh)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _show(self, event: FleetEvent) -> None:
        match event:
            case Changed(unit=unit, state=state):
                # A report or a question says the rest; a line only for what nothing else
                # shows: it failed, was stopped, or ended without a report.
                if state not in QUIET_STATES:
                    self._add(Line(f"{unit.name} · {state}", {unit.id}, tone="warning"))
            case Said(unit=unit, text=text):
                self._add(SaidEntry(unit, text))
            case Delivered():
                self._add(DeliveryCard(event))
            case Approval():
                card = ApprovalCard(event)
                card.allow.clicked.connect(lambda: self._run(self._answer(event, True)))
                card.deny.clicked.connect(lambda: self._run(self._answer(event, False)))
                self._approvals[event.request_id] = card
                self._add(card)
            case Answered(request_id=request_id, allow=allow):
                if (card := self._approvals.pop(request_id, None)) is not None:
                    card.answered(allow)
            case Told(text=text):
                lead = self.fleet.lead if self.fleet is not None else None
                self._add(YouEntry(text, lead.id if lead is not None else None))
            case Notice(text=text):
                self._say(text, tone="warning")

    def _say(self, text: str, tone: str) -> None:
        self._add(Line(text, set(), tone))

    def _add(self, entry: QWidget) -> None:
        entry.setMaximumWidth(COLUMN)
        self.entries.append(entry)
        self._timeline.insertWidget(self._timeline.count() - 1, entry)
        entry.setVisible(self._concerns(entry))

    def _concerns(self, entry: QWidget) -> bool:
        concerns: set[str] = entry.concerns  # type: ignore[attr-defined]
        return self._shown is None or not concerns or self._shown in concerns

    def _clear(self) -> None:
        for entry in self.entries:
            entry.setParent(None)
            entry.deleteLater()
        self.entries.clear()
        self._approvals.clear()
        self._rows.clear()
        self.agents.clear()
        self._shown = None
        self.show_all.hide()

    def _agent_chosen(self, item: QTreeWidgetItem | None, _: object = None) -> None:
        self._shown = item.data(0, UNIT_ROLE) if item is not None else None
        self.show_all.setVisible(self._shown is not None)
        for entry in self.entries:
            entry.setVisible(self._concerns(entry))

    def _refresh(self) -> None:
        """The bar and the agents, from the fleet as it is now."""
        fleet = self.fleet
        self.start_form.setVisible(fleet is None)
        self.scroll.setVisible(fleet is not None)
        self.composer.setVisible(fleet is not None)
        self.input.setEnabled(self.running)
        self.send_button.setEnabled(self.running)
        self.stop_button.setVisible(self.running)
        can_resume = (not self.running and not self._stopping and fleet is not None
                      and any(unit.active for unit in fleet.units.values()))
        self.resume_button.setVisible(can_resume)
        self.title.setVisible(fleet is not None)
        self._bar_gap.setVisible(fleet is None)
        self.status.setVisible(fleet is not None)
        self.agents_section.setVisible(fleet is not None)
        self.fleets.setEnabled(not self.running and not self._stopping)
        self.fleets_hint.setVisible(self.running or self._stopping)
        self.fleets_label.setVisible(self.fleets.count() > 0)
        if fleet is None:
            self.fleets.setCurrentItem(None)
            return
        lead = fleet.lead
        self.title.setText(_clip(lead.task) if lead is not None else "Starting…")
        budget = (self.manager.config.budget_usd if self.manager is not None
                  else fleet.settings.get("budget_usd", 0.0))
        state = "running" if self.running else "stopping" if self._stopping else "stopped"
        if (self.manager is not None and self.manager.finished.is_set()
                and not any(unit.active for unit in fleet.units.values())):
            state = "finished"  # its task is done; the lead still takes messages
        self.status.setText(f"{state} · ${fleet.spent_usd:.2f} of ${budget:.2f}")
        for unit in fleet.units.values():
            row = self._rows.get(unit.id)
            if row is None:
                parent = self._rows.get(unit.supervisor or "")
                row = QTreeWidgetItem(parent) if parent is not None else QTreeWidgetItem(
                    self.agents)
                row.setData(0, UNIT_ROLE, unit.id)
                row.setExpanded(True)
                if parent is not None:
                    parent.setExpanded(True)
                self._rows[unit.id] = row
            row.setText(0, f"{unit.name}  K{unit.rank}")
            row.setText(1, unit.state)
            row.setText(2, f"${unit.spent_usd:.2f}")
            colors = theme.current()
            tone = {"working": colors.accent, "starting": colors.accent,
                    "waiting": colors.warning, "failed": colors.error,
                    "done": colors.ok}.get(unit.state, colors.muted)
            row.setForeground(1, QColor(tone))
            row.setForeground(2, QColor(colors.muted))
            row.setToolTip(0, unit.task)
        # As tall as its agents, up to a part of the sidebar: the fleets list has the rest.
        rows = min(len(fleet.units), AGENT_ROWS)
        self.agents.setFixedHeight(max(1, rows) * AGENT_ROW_PX + 6)

    def _run(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("KFM")
    app.setDesktopFileName(app_menu.APP_ID)  # the app menu's entry: its icon and its name
    app.setWindowIcon(QIcon(str(app_menu.ICON)))
    app.setQuitOnLastWindowClosed(False)  # the fleet is stopped first
    theme.apply(app)
    loop = QtEventLoop(app)
    asyncio.set_event_loop(loop)
    window = FleetWindow()
    window.resize(1180, 760)
    window.show()
    with loop:
        loop.run_until_complete(window.closed.wait())


if __name__ == "__main__":
    main()
