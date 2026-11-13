"""The window (`kfm-desktop`): projects, each one's fleets as a chart, and for a fleet its
agents, what passes between them and the person's part in it. A frontend on Managers, like
the terminal's Console: their events in, their methods out. Built on Kayden's desktop code: its event loop, theme and Markdown
view."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import sys
import tempfile
import time
from collections.abc import Coroutine, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import kayden.frontends.desktop  # noqa: F401 - before any Qt import
from kayden.frontends.desktop import theme
from kayden.frontends.desktop.loop import QtEventLoop
from kayden.frontends.desktop.window import (
    DATE_ROLE,
    PREVIEW_ROLE,
    WINDOW_BUTTON,
    AttachmentStrip,
    DragArea,
    ElidedLabel,
    MarkdownView,
    MessageBox,
    RoundedCorners,
    RowDelegate,
    WindowEdges,
)
from PySide6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDoubleValidator,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kfm import app_menu, draft, harnesses, log, project
from kfm import fleet as fleet_record
from kfm.api import Harness, UnitError
from kfm.chart import Chart, ChartFleet
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load, save
from kfm.events import (
    Answered,
    Approval,
    Changed,
    Delivered,
    FleetEvent,
    Instructed,
    Notice,
    Said,
    Told,
)
from kfm.fleet import Fleet, FleetError, Unit
from kfm.manager import Manager, lay_out, place
from kfm.project import Project

SIDEBAR = 280  # px
FORM = 760  # px, the start screen's width
LOGO = 64  # px, on the start screen
AGENT_ROWS = 10  # agents shown before their list scrolls
AGENT_ROW_PX = 30
FOLDER_ROLE = Qt.ItemDataRole.UserRole  # a project row's folder
FLEETS_ROLE = Qt.ItemDataRole.UserRole + 10  # how many fleets it has
QUIET_STATES = ("starting", "working", "waiting", "done")  # shown by the agents list alone
BAR = 48  # px, the bar on top
COLUMN = 720  # px, the widest the timeline's entries get
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
    """A project folder's name (20261112-210500) as its row's date: the time today, else
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
        layout.setContentsMargins(15, 4, 15, 4)  # in line with a card's text
        layout.setSpacing(3)
        self.unit = unit.id
        self.header = QWidget()  # hidden where the page is this agent's conversation
        who = QHBoxLayout(self.header)
        who.setContentsMargins(0, 0, 0, 0)
        who.setSpacing(6)
        who.addWidget(label(unit.name or f"K{unit.rank}", role="strong", wrap=False))
        who.addWidget(label(f"K{unit.rank}" if unit.name else unit.id, role="small",
                            tone="muted", wrap=False))
        who.addStretch(1)
        self.body = MarkdownView()
        self.body.setMarkdown(text)
        layout.addWidget(self.header)
        layout.addWidget(self.body)


class DeliveryCard(QFrame):
    """A report, question, update or failure on its way up, with its whole text."""

    def __init__(self, event: Delivered) -> None:
        super().__init__()
        self.setObjectName("delivery")
        self.setProperty("you", event.to is None)  # it is the person's to read or answer
        self.concerns = {event.unit.id} | ({event.to.id} if event.to is not None else set())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        target = event.to.called if event.to is not None else "you"
        self.header = label(f"{event.unit.called} → {target} · {event.kind}", role="strong")
        self.body = MarkdownView()
        self.body.setMarkdown(event.text)
        layout.addWidget(self.header)
        layout.addWidget(self.body)


class InstructionCard(QFrame):
    """What an agent was given by its supervisor: its task, or a message."""

    def __init__(self, event: Instructed) -> None:
        super().__init__()
        assert event.by is not None
        self.setObjectName("delivery")
        self.concerns = {event.unit.id, event.by.id}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self.header = label(f"{event.by.called} → {event.unit.called} · {event.kind}",
                            role="strong")
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
        self.question = label(f"{event.unit.called} wants to run {event.tool} {event.arguments}")
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
            f"Allowed: {asked.unit.called} ran {asked.tool} {asked.arguments}" if allow else
            f"Denied: {asked.unit.called} wanted to run {asked.tool} {asked.arguments}")


class YouEntry(QWidget):
    """What the person said to an agent: a bubble on the right."""

    def __init__(self, text: str, to: str | None, files: Sequence[str] = ()) -> None:
        super().__init__()
        self.concerns = {to} if to else set()
        self.files = [Path(file).name for file in files]
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.addStretch(1)
        bubble = QFrame(objectName="user")
        inside = QVBoxLayout(bubble)
        inside.setContentsMargins(12, 8, 12, 8)
        inside.setSpacing(4)
        for file, name in zip(files, self.files, strict=True):
            chip = label(name, role="small", tone="muted", wrap=False)
            chip.setToolTip(file)
            inside.addWidget(chip)
        self.text = label(text)
        self.text.setVisible(bool(text))
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


class KindSwitch(QFrame):
    """Task or Duty, as two segments."""

    def __init__(self) -> None:
        super().__init__(objectName="segments")
        colors = theme.current()
        self.setStyleSheet(
            f"QPushButton#segment {{ border: none; background: transparent; "
            f"color: {colors.muted}; padding: 6px 14px; border-radius: 8px; }}"
            f"QPushButton#segment:hover {{ color: {colors.text}; }}"
            f"QPushButton#segment:checked {{ background: {colors.accent_soft}; "
            f"color: {colors.accent_text}; }}"
            f"QFrame#segments {{ border: 1px solid {colors.line}; border-radius: 10px; }}")
        kinds = QHBoxLayout(self)
        kinds.setContentsMargins(3, 3, 3, 3)
        kinds.setSpacing(2)
        self.task_kind = QPushButton("Task", objectName="segment")
        self.task_kind.setToolTip("Ends when the lead reports")
        self.duty = QPushButton("Duty", objectName="segment")
        self.duty.setToolTip("Runs until you stop it")
        self.group = QButtonGroup(self)
        for button in (self.task_kind, self.duty):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button)
            kinds.addWidget(button)
        self.duty.setChecked(True)  # a fleet in a project holds a role
        self.setFixedHeight(36)


BOX_STYLE = ("QFrame#budget {{ border: 1px solid {line}; border-radius: 10px; }}"
             "QFrame#budget QLineEdit {{ background: transparent; border: none; padding: 0; }}")


class StartForm(QWidget):
    """A new fleet: its name, one box for what it should do, and what else a fleet needs
    along the box's lower edge."""

    submitted = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMaximumWidth(FORM)
        self.setMinimumWidth(480)
        colors = theme.current()
        self.setStyleSheet(BOX_STYLE.format(line=colors.line))
        self.folder: Path | None = None  # the project's, unless another is chosen
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.addStretch(3)
        logo = QLabel()
        logo.setPixmap(QIcon(str(app_menu.ICON)).pixmap(LOGO, LOGO))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.heading = label("New fleet", wrap=False)
        self.heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(logo)
        outer.addSpacing(14)
        outer.addWidget(self.heading)
        outer.addSpacing(22)

        box = QFrame(objectName="composer")
        inside = QVBoxLayout(box)
        inside.setContentsMargins(16, 14, 12, 12)
        inside.setSpacing(12)
        self.name = _name_field("Fleet name")
        self.name.returnPressed.connect(lambda: self.task.setFocus())
        inside.addWidget(self.name)
        self.task = TaskBox()
        self.task.setPlaceholderText("What it does, to have a lead build it")
        self.task.submitted.connect(self.submitted)
        inside.addWidget(self.task)
        row = QHBoxLayout()
        row.setSpacing(8)
        segments = KindSwitch()
        self.task_kind, self.duty, self._kinds = (segments.task_kind, segments.duty,
                                                  segments.group)
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
        self.set_folder(None)

    def sizeHint(self) -> QSize:
        return QSize(FORM, super().sizeHint().height())

    def set_folder(self, folder: Path | None) -> None:
        """The folder the fleet will work in; None: not chosen yet."""
        self.folder = Path(folder).expanduser() if folder is not None else None
        chosen = self.folder
        self.folder_button.setText(f" {chosen.name or chosen}" if chosen else " Choose a folder")
        self.folder_button.setToolTip(f"The agents work in {chosen}" if chosen
                                      else "The folder the agents work in")
        self.warn("")

    def warn(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def _describe(self) -> None:
        self.hint.setText(
            "Runs until you stop it." if self.duty.isChecked() else
            "Ends when the lead reports.")

    def _choose(self) -> None:
        start = self.folder.parent if self.folder is not None else Path.home()
        dialog = QFileDialog(self, "The folder the fleet works in", str(start))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.fileSelected.connect(lambda chosen: self.set_folder(Path(chosen)))
        dialog.open()


class ProjectForm(QWidget):
    """The window with no project: a name, the folder the project's fleets work in, and
    what it should do when its fleets are to be drafted."""

    submitted = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMaximumWidth(FORM)
        self.setMinimumWidth(480)
        colors = theme.current()
        self.folder: Path | None = None
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.addStretch(3)
        logo = QLabel()
        logo.setPixmap(QIcon(str(app_menu.ICON)).pixmap(LOGO, LOGO))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        heading = label("New project", wrap=False)
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
        self.name = _name_field("Project name")
        self.name.returnPressed.connect(lambda: self.does.setFocus())
        inside.addWidget(self.name)
        self.does = TaskBox()
        self.does.setPlaceholderText("What it should do, to have its fleets drafted")
        self.does.submitted.connect(self.submitted)
        inside.addWidget(self.does)
        row = QHBoxLayout()
        self.folder_button = QToolButton(objectName="folderPicker")
        self.folder_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.folder_button.setIcon(theme.icon("folder", colors.muted, 16))
        self.folder_button.clicked.connect(self._choose)
        self.create = QPushButton("Create")
        self.create.setProperty("primary", True)
        self.create.clicked.connect(self.submitted)
        row.addWidget(self.folder_button)
        row.addStretch(1)
        row.addWidget(self.create)
        inside.addLayout(row)
        outer.addWidget(box)
        outer.addSpacing(10)
        self.warning = label("", role="small", tone="error")
        self.warning.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.warning)
        outer.addStretch(5)
        self.set_folder(None)

    def sizeHint(self) -> QSize:
        return QSize(FORM, super().sizeHint().height())

    def set_folder(self, folder: Path | None) -> None:
        self.folder = Path(folder).expanduser() if folder is not None else None
        chosen = self.folder
        self.folder_button.setText(f" {chosen.name or chosen}" if chosen else " Choose a folder")
        self.folder_button.setToolTip(f"Its fleets work in {chosen}" if chosen
                                      else "The folder its fleets work in")
        self.warn("")

    def warn(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def _choose(self) -> None:
        start = self.folder.parent if self.folder is not None else Path.home()
        dialog = QFileDialog(self, "The folder the project's fleets work in", str(start))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.fileSelected.connect(lambda chosen: self.set_folder(Path(chosen)))
        dialog.open()


def _name_field(placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setFrame(False)
    field.setPlaceholderText(placeholder)
    field.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0; "
                        "font-weight: 600; }")
    return field


def _work_folder(folder: Path | None) -> tuple[Path | None, str]:
    """The folder agents may work in, or why they may not."""
    if folder is None:
        return None, "Choose a folder first."
    work = folder.resolve()
    if work == Path.home().resolve():
        return None, ("Not your home folder: agents can change anything in the folder they "
                      "work in.")
    if not work.is_dir():
        return None, f"There is no folder {work}."
    return work, ""


def _centred(form: QWidget) -> QWidget:
    page = QWidget()
    across = QHBoxLayout(page)
    across.setContentsMargins(0, 0, 0, 0)
    across.addWidget(form, 0, Qt.AlignmentFlag.AlignHCenter)
    return page


class DraftRow(QFrame):
    """A fleet of a draft: its name, what it does, Task or Duty, and a way out of it."""

    def __init__(self, name: str = "", does: str = "", ongoing: bool = True) -> None:
        super().__init__(objectName="composer")
        inside = QVBoxLayout(self)
        inside.setContentsMargins(16, 12, 12, 10)
        inside.setSpacing(8)
        top = QHBoxLayout()
        self.name = _name_field("Name")
        self.name.setText(name)
        self.remove = QToolButton(objectName="titleControl")
        self.remove.setIcon(theme.icon("close", theme.current().muted))
        self.remove.setIconSize(QSize(12, 12))
        self.remove.setToolTip("Leave this fleet out")
        self.remove.setAccessibleName("Leave this fleet out")
        top.addWidget(self.name, 1)
        top.addWidget(self.remove)
        inside.addLayout(top)
        self.does = MessageBox()
        self.does.setPlaceholderText("What it does")
        self.does.setPlainText(does)
        inside.addWidget(self.does)
        self.kind = KindSwitch()
        (self.kind.duty if ongoing else self.kind.task_kind).setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.kind)
        row.addStretch(1)
        inside.addLayout(row)


class DraftPage(QWidget):
    """A project's fleets as an agent drafted them, for the person to change. Nothing runs
    until `start` is clicked. `over`: started or discarded."""

    start_asked = Signal()
    over = Signal()

    def __init__(self, opened: Project, description: str) -> None:
        super().__init__()
        self.project, self.description = opened, description
        self.rows: list[DraftRow] = []
        self.busy = True  # the agent is drafting
        self._work: asyncio.Task[None] | None = None
        self.setStyleSheet(BOX_STYLE.format(line=theme.current().line))
        column = QWidget()
        column.setMaximumWidth(FORM)
        self._column = QVBoxLayout(column)
        self._column.setContentsMargins(0, 24, 0, 16)
        self._column.setSpacing(12)
        self.heading = label("", wrap=False)
        self.heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.note = label("", role="small", tone="muted")
        self._column.addWidget(self.heading)
        self._column.addWidget(self.note)
        self.add_button = QPushButton(" Add fleet", objectName="nav")
        self.add_button.setIcon(theme.icon("plus", theme.current().text, 16))
        self.add_button.clicked.connect(lambda: self.add_row().name.setFocus())
        self.budget = Budget()
        self.budget.setToolTip("The most each fleet may spend")
        self.discard = QPushButton("Discard")
        self.discard.clicked.connect(self.cancel)
        self.start = QPushButton("Start")
        self.start.setProperty("primary", True)
        self.start.clicked.connect(self.start_asked)
        foot = QHBoxLayout()
        foot.addWidget(self.add_button)
        foot.addStretch(1)
        foot.addWidget(self.budget)
        foot.addWidget(self.discard)
        foot.addWidget(self.start)
        self._column.addLayout(foot)
        self._column.addStretch(1)
        across = QHBoxLayout()
        across.setContentsMargins(20, 0, 20, 0)
        across.addStretch(1)
        across.addWidget(column, 100)
        across.addStretch(1)
        page = QWidget(objectName="transcript")
        page.setLayout(across)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._show()

    def run(self, config: FleetConfig, harness: Harness | None) -> None:
        """Have an agent draft the fleets."""
        self._work = asyncio.ensure_future(self._draft(config, harness))

    def cancel(self) -> None:
        """Drop the draft; an agent still at it is stopped."""
        if self._work is not None:
            self._work.cancel()
        self.over.emit()

    def add_row(self, name: str = "", does: str = "", ongoing: bool = True) -> DraftRow:
        row = DraftRow(name, does, ongoing)
        row.remove.clicked.connect(lambda: self._remove(row))
        self.rows.append(row)
        self._column.insertWidget(self._column.count() - 2, row)  # above the foot
        return row

    def warn(self, text: str) -> None:
        self.note.setText(text)
        theme.mark(self.note, "tone", "error")

    async def _draft(self, config: FleetConfig, harness: Harness | None) -> None:
        try:
            harness = harness or await asyncio.to_thread(
                harnesses.load, config.harness, config.harness_options)
            made = await draft.draft(self.description, config=config, harness=harness,
                                     folder=self.project.folder / "planner",
                                     work=self.project.work)
        except (draft.DraftError, UnitError, ValueError, OSError) as e:
            self.busy = False
            self._show()
            self.warn(f"No draft: {e}")
            return
        for team in made.teams:
            self.add_row(team.name, team.does, team.ongoing)
        self.busy = False
        self._show()
        self.note.setText(f"Drafted for ${made.spent_usd:.2f}. Nothing runs until you start it.")

    def _remove(self, row: DraftRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _show(self) -> None:
        self.heading.setText("Drafting…" if self.busy else "Draft")
        for part in (self.add_button, self.budget, self.start):
            part.setVisible(not self.busy)
        self.discard.setText("Cancel" if self.busy else "Discard")


class AgentDialog(QDialog):
    """An agent the person puts under another, or at the top of a fleet that has none: its
    name, its task, and a model of its own if it should not run on its rank's. `add_asked`
    when the person wants it made."""

    add_asked = Signal()

    def __init__(self, parent: QWidget, supervisor: Unit | None, rank: int) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add agent under {supervisor.called}" if supervisor is not None
                            else "Add the lead")
        self.setMinimumWidth(460)
        rows = QVBoxLayout(self)
        rows.setSpacing(10)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name, if it should have one")
        self.task = TaskBox()
        self.task.setPlaceholderText(
            "Its task. Empty: its supervisor gives it one" if supervisor is not None
            else "Its task. Empty: your first message is its task")
        self.task.submitted.connect(self.add_asked)
        self.kind = KindSwitch()
        self.kind.task_kind.setChecked(True)
        self.model = QLineEdit()
        self.model.setPlaceholderText(f"Model (K{rank}'s, if empty)")
        self.warning = label("", role="small", tone="error")
        self.warning.hide()
        self.add = QPushButton("Add")
        self.add.setProperty("primary", True)
        self.add.clicked.connect(self.add_asked)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addWidget(self.kind)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.add)
        for row in (self.name, self.task, self.model, self.warning):
            rows.addWidget(row)
        rows.addLayout(foot)

    def warn(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))


class DeleteDialog(QDialog):
    """Whether to delete a project or a fleet (`what`): what goes, and what stays."""

    def __init__(self, parent: QWidget, title: str, what: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        rows = QVBoxLayout(self)
        rows.setSpacing(14)
        self.question = label(f"Delete {what}? This cannot be undone. The folder the agents "
                              "worked in is kept.")
        self.delete = QPushButton("Delete")
        self.delete.setProperty("primary", True)
        self.delete.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.delete)
        rows.addWidget(self.question)
        rows.addLayout(foot)


class SettingsDialog(QDialog):
    """What new fleets get: the model of each rank and the limits. `saved` carries the
    settings once they are written."""

    saved = Signal(object)

    def __init__(self, parent: QWidget, path: Path, config: FleetConfig) -> None:
        super().__init__(parent)
        self._path = path
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        rows = QVBoxLayout(self)
        rows.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(8)
        self.models: dict[str, QLineEdit] = {}
        for rank in range(config.top_rank, 0, -1):
            field = self.models[f"K{rank}"] = QLineEdit(config.models.get(f"K{rank}", ""))
            field.setPlaceholderText("The agent program's own")
            form.addRow(f"K{rank} model", field)
        self.budget = self._money(config.budget_usd)
        self.each = self._money(config.unit_budget_usd)
        self.agents = QSpinBox()
        self.agents.setRange(0, 999)
        self.agents.setValue(config.max_units)
        form.addRow("A fleet may spend", self.budget)
        form.addRow("An agent may spend", self.each)
        form.addRow("Agents in a fleet, at most", self.agents)
        self.warning = label("", role="small", tone="error")
        self.warning.hide()
        self.save = QPushButton("Save")
        self.save.setProperty("primary", True)
        self.save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.save)
        rows.addLayout(form)
        rows.addWidget(self.warning)
        rows.addLayout(foot)

    @staticmethod
    def _money(value: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(0.0, 100_000.0)
        field.setDecimals(2)
        field.setPrefix("$ ")
        field.setValue(value)
        return field

    def _save(self) -> None:
        try:
            made = save(self._path, budget_usd=self.budget.value(),
                        unit_budget_usd=self.each.value(), max_units=self.agents.value(),
                        models={rank: field.text().strip()
                                for rank, field in self.models.items()})
        except (ValueError, OSError) as e:
            self.warning.setText(str(e))
            self.warning.show()
            return
        self.saved.emit(made)
        self.accept()


class LineDialog(QDialog):
    """One line to change: a name, a model."""

    def __init__(self, parent: QWidget, title: str, text: str, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        rows = QVBoxLayout(self)
        self.line = QLineEdit(text)
        self.line.setPlaceholderText(placeholder)
        self.line.returnPressed.connect(self.accept)
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(save)
        rows.addWidget(self.line)
        rows.addLayout(foot)


class ModelDialog(LineDialog):
    """The model an agent runs on; empty: its rank's."""

    def __init__(self, parent: QWidget, unit: Unit) -> None:
        super().__init__(parent, f"{unit.called}'s model", unit.model or "",
                         f"K{unit.rank}'s, if empty")
        self.model = self.line


def _dropped_files(source: QMimeData) -> list[str]:
    """The files among what was dropped."""
    return [url.toLocalFile() for url in source.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()]


# ── A fleet ───────────────────────────────────────────────────────────────


class FleetView(QWidget):
    """One fleet: its timeline, the box for messages to its lead, and its manager while it
    runs. While it does not run, agents the person adds go into its record and start with it. `changed` after anything that alters how it stands."""

    changed = Signal()

    def __init__(self, folder: Path, harness: Harness | None) -> None:
        super().__init__()
        self.folder, self._harness = folder, harness
        self.manager: Manager | None = None  # while it runs
        self.fleet: Fleet | None = None
        self.entries: list[QWidget] = []
        self.shown: str | None = None  # the agent whose entries are shown; None: all
        self.stopping = False  # its processes are ending
        self._approvals: dict[str, ApprovalCard] = {}
        self._tasks: set[asyncio.Task[None]] = set()

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
        # Kayden's message box: what is attached, then add files, the text and send.
        self.attachments = AttachmentStrip()
        self.input.attached.connect(self.attachments.add)
        self.input.pasted.connect(self._paste_image)
        self._pasted: Path | None = None  # the folder pasted pictures are saved in
        self.setAcceptDrops(True)  # files dropped anywhere on the page are attached
        self.attach_button = QToolButton(objectName="send")
        self.attach_button.setFixedSize(32, 32)
        self.attach_button.setIcon(theme.icon("plus", theme.current().muted, 16))
        self.attach_button.setIconSize(QSize(16, 16))
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_button.setToolTip("Add files")
        self.attach_button.setAccessibleName("Add files")
        self.attach_button.clicked.connect(self._choose_files)
        box = QFrame(objectName="composer")
        box.setMaximumWidth(COLUMN)
        stacked = QVBoxLayout(box)
        stacked.setContentsMargins(8, 8, 8, 8)
        stacked.setSpacing(6)
        stacked.addWidget(self.attachments)
        inside = QHBoxLayout()
        inside.setSpacing(6)
        inside.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignBottom)
        inside.addWidget(self.input, 1)
        inside.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        stacked.addLayout(inside)
        composer = QHBoxLayout()
        composer.setContentsMargins(0, 0, 0, 0)
        composer.addStretch(1)
        composer.addWidget(box, 100)
        composer.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 14)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(composer)
        colors = theme.current()
        page.setStyleSheet(
            f"QFrame#delivery {{ background: {colors.card}; border: 1px solid transparent; "
            f"border-radius: 12px; }}"
            f"QFrame#delivery[you=\"true\"] {{ border-color: {colors.accent}; }}")
        self._enable()

    @property
    def running(self) -> bool:
        return self.manager is not None

    @property
    def name(self) -> str:
        return self.fleet.name if self.fleet is not None else ""

    @property
    def can_resume(self) -> bool:
        return (not self.running and not self.stopping and self.fleet is not None
                and any(unit.active or unit.state == "planned"
                        for unit in self.fleet.units.values()))

    @property
    def started(self) -> bool:
        """Whether any of its agents has ever run."""
        return self.fleet is not None and any(
            unit.session is not None for unit in self.fleet.units.values())

    @property
    def status(self) -> str:
        """How it stands and what it has spent of its limit."""
        fleet = self.fleet
        if fleet is None:
            return ""
        budget = (self.manager.config.budget_usd if self.manager is not None
                  else fleet.settings.get("budget_usd", 0.0))
        state = ("running" if self.running else "stopping" if self.stopping
                 else "stopped" if self.started else "not started")
        if (self.manager is not None and self.manager.finished.is_set()
                and not any(unit.active for unit in fleet.units.values())):
            state = "finished"  # its task is done; the lead still takes messages
        return f"{state} · ${fleet.spent_usd:.2f} of ${budget:.2f}"

    async def start(self, config: FleetConfig, name: str, task: str, *, ongoing: bool,
                    work: Path) -> None:
        """A new fleet on the task. ValueError or OSError when it cannot be made."""
        manager = Manager(config, self.folder, work, harness=self._harness,
                          on_event=self._on_event, name=name)
        self.manager, self.fleet = manager, manager.fleet
        self._enable()
        await manager.start()
        self.changed.emit()
        await manager.lead(task, ongoing=ongoing)

    async def load(self) -> None:
        """The fleet as it was left, with what happened in it. ValueError, OSError or
        KeyError when its record cannot be read."""
        fleet = await asyncio.to_thread(Fleet.load, self.folder)
        history = await asyncio.to_thread(log.read, self.folder, fleet)
        self.fleet = fleet
        for _, event in history:
            self._show(event)
        self._enable()

    async def resume(self) -> None:
        if self.running or self.fleet is None:
            return
        try:
            manager = await asyncio.to_thread(
                lambda: Manager.resumed(self.folder, harness=self._harness,
                                        on_event=self._on_event))
        except (ValueError, OSError) as e:  # FleetError: it runs elsewhere, or is out of money
            self.say(str(e), tone="error")
            return
        self.manager, self.fleet = manager, manager.fleet
        self._enable()
        await manager.start()
        self.changed.emit()

    async def add_agent(self, supervisor: Unit | None, name: str, task: str, *,
                        ongoing: bool, model: str | None) -> None:
        """An agent under `supervisor`, or the lead of a fleet with none. FleetError or
        UnitError when it cannot be."""
        if self.manager is not None and supervisor is not None:
            await self.manager.hire(supervisor, name, task, ongoing=ongoing, model=model)
        elif self.fleet is not None and not self.running:
            await asyncio.to_thread(place, self.fleet, self.folder, supervisor, name, task,
                                    ongoing=ongoing, model=model)
        self._enable()
        self.changed.emit()

    async def rename(self, name: str, unit: Unit | None = None) -> None:
        """Another name for the agent, or for the fleet."""
        if self.manager is not None:
            await self.manager.rename(name, unit)
        elif self.fleet is not None:
            if unit is not None:
                unit.name = name
            else:
                self.fleet.name = name
            await asyncio.to_thread(self.fleet.save, self.folder)
        self.changed.emit()

    async def set_model(self, unit: Unit, model: str) -> None:
        if self.manager is not None:
            await self.manager.set_model(unit, model)
        elif self.fleet is not None:
            unit.model = model or None
            await asyncio.to_thread(self.fleet.save, self.folder)
        self.changed.emit()

    async def stop(self) -> None:
        """Stop the fleet's processes; it can be resumed."""
        if (manager := self.manager) is None:
            return
        self.manager = None
        self.stopping = True
        self._enable()
        self.changed.emit()
        try:
            await manager.aclose()
        finally:
            self.stopping = False
            self._enable()
            self.changed.emit()

    def choose(self, unit_id: str | None) -> None:
        """Show only what concerns the agent; None: everything."""
        self.shown = unit_id
        for entry in self.entries:
            self._fit(entry)
        to = self.chosen
        self.input.setPlaceholderText(
            f"Message {to.called}" if to is not None and to.supervisor is not None
            else "Message the lead")
        self.changed.emit()

    @property
    def chosen(self) -> Unit | None:
        """The agent whose conversation is shown, which is the one a message goes to."""
        return self.fleet.units.get(self.shown or "") if self.fleet is not None else None

    def _fit(self, entry: QWidget) -> None:
        """Show the entry if it concerns the agent chosen; in an agent's own conversation
        what it wrote needs no name over it."""
        entry.setVisible(self._concerns(entry))
        if isinstance(entry, SaidEntry):
            entry.header.setVisible(entry.unit != self.shown)

    def say(self, text: str, tone: str) -> None:
        self._add(Line(text, set(), tone))

    def _enable(self) -> None:
        """A message can be sent to a fleet that has a lead, unless it is stopping: it
        starts a fleet that is not running."""
        lead = self.fleet.lead if self.fleet is not None else None
        can = not self.stopping and (self.running or lead is not None)
        for part in (self.input, self.send_button, self.attach_button):
            part.setEnabled(can)

    def _paste_image(self, image: QImage) -> None:
        """A picture pasted from the clipboard: saved as a file, then attached like one."""
        if self._pasted is None:
            self._pasted = Path(tempfile.mkdtemp(prefix="kfm-pasted-"))
        path = self._pasted / f"pasted-{len(list(self._pasted.iterdir())) + 1}.png"
        if image.save(str(path), "PNG"):
            self.attachments.add([str(path)])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.input.isEnabled() and _dropped_files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if files := _dropped_files(event.mimeData()):
            self.attachments.add(files)
            event.acceptProposedAction()

    def _choose_files(self) -> None:
        """Add files picks files to go with the message, as dropping them does."""
        start = self.fleet.work if self.fleet is not None and self.fleet.work else str(Path.home())
        self.file_dialog = QFileDialog(self, "Add files", start)
        self.file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        self.file_dialog.filesSelected.connect(self.attachments.add)
        self.file_dialog.open()

    def _send_clicked(self) -> None:
        text, files = self.input.toPlainText().strip(), self.attachments.paths()
        if text or files:
            self.input.clear()
            self.attachments.clear()
            self._run(self._tell(text, files))

    async def _tell(self, text: str, files: list[str]) -> None:
        if self.manager is None:
            await self.resume()
        if self.manager is None:  # it could not start, and has said why
            self.input.setPlainText(text)
            self.attachments.add(files)
            return
        try:
            await self.manager.say(text, self.chosen, files=files)
        except (FleetError, UnitError) as e:
            self.say(str(e), tone="error")

    async def _answer(self, event: Approval, allow: bool) -> None:
        if self.manager is not None:
            try:
                await self.manager.approve(event.unit, event.request_id, allow)
            except UnitError as e:
                self.say(str(e), tone="error")

    def _on_event(self, event: FleetEvent) -> None:
        self._show(event)
        # After the manager has finished with the event: it may end the fleet's task.
        asyncio.get_running_loop().call_soon(self.changed.emit)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _show(self, event: FleetEvent) -> None:
        match event:
            case Changed(unit=unit, state=state):
                # A report or a question says the rest; a line only for what nothing else
                # shows: it failed, was stopped, or ended without a report.
                if state not in QUIET_STATES:
                    self._add(Line(f"{unit.called} · {state}", {unit.id}, tone="warning"))
            case Said(unit=unit, text=text):
                self._add(SaidEntry(unit, text))
            case Delivered():
                self._add(DeliveryCard(event))
            case Instructed(unit=unit, by=by, text=text):
                if text:  # the person's own is what they said; a supervisor's is a card
                    self._add(YouEntry(text, unit.id) if by is None else InstructionCard(event))
            case Approval():
                card = ApprovalCard(event)
                card.allow.clicked.connect(lambda: self._run(self._answer(event, True)))
                card.deny.clicked.connect(lambda: self._run(self._answer(event, False)))
                self._approvals[event.request_id] = card
                self._add(card)
            case Answered(request_id=request_id, allow=allow):
                if (card := self._approvals.pop(request_id, None)) is not None:
                    card.answered(allow)
            case Told(text=text, to=to, files=files):
                to = to or (self.fleet.lead if self.fleet is not None else None)
                self._add(YouEntry(text, to.id if to is not None else None, files))
            case Notice(text=text):
                self.say(text, tone="warning")

    def _add(self, entry: QWidget) -> None:
        entry.setMaximumWidth(COLUMN)
        self.entries.append(entry)
        self._timeline.insertWidget(self._timeline.count() - 1, entry)
        self._fit(entry)

    def _concerns(self, entry: QWidget) -> bool:
        concerns: set[str] = entry.concerns  # type: ignore[attr-defined]
        return self.shown is None or not concerns or self.shown in concerns

    def _run(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


# ── The window ────────────────────────────────────────────────────────────


class FleetWindow(QMainWindow):
    """`home`: where projects and the settings are kept (~/.kfm). `harness` and `config`
    replace what the settings name, for tests."""

    def __init__(self, home: Path = HOME, *, harness: Harness | None = None,
                 config: FleetConfig | None = None) -> None:
        super().__init__()
        self.home, self._harness, self._config = home, harness, config
        self.project: Project | None = None  # the one shown
        self.views: dict[Path, FleetView] = {}  # every fleet read so far, by its folder
        self.view: FleetView | None = None  # the fleet opened; None: the chart
        self.adding = False  # the form for a new fleet is shown
        self.dialog: QDialog | None = None  # the one open about an agent
        self.drafts: dict[Path, DraftPage] = {}  # by project folder, until started
        self.closed = asyncio.Event()
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._rows_of: FleetView | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.setWindowTitle("KFM")
        # No title bar: the bar on top is where the window is grabbed, and has its controls.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._edges = WindowEdges(self)
        self._build()
        self._corners = RoundedCorners(self)
        self._timer = QTimer(self, interval=REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()
        self._run(self._offer_app_menu())
        self._run(self._open_newest())

    def _build(self) -> None:
        self.agents = QTreeWidget()
        self.agents.setColumnCount(3)
        self.agents.setHeaderHidden(True)
        self.agents.setRootIsDecorated(False)
        self.agents.setIndentation(16)
        self.agents.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.agents.header().setStretchLastSection(False)  # the name has the room
        self.agents.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.agents.setColumnWidth(2, 0)
        for column in (1, 2):
            self.agents.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        self.agents.setFrameShape(QFrame.Shape.NoFrame)
        self.agents.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.agents.currentItemChanged.connect(self._agent_chosen)
        self.new_button = QPushButton(" New project", objectName="nav")
        self.new_button.setIcon(theme.icon("plus", theme.current().text, 16))
        self.new_button.clicked.connect(self.new_project)
        side = QFrame(objectName="fleetSide")
        side.setFixedWidth(SIDEBAR)
        colors = theme.current()
        side.setStyleSheet(
            f"QFrame#fleetSide {{ background: {colors.side}; }}"
            f"QTreeWidget {{ background: transparent; border: none; "
            f"selection-background-color: transparent; show-decoration-selected: 0; }}"
            f"QTreeWidget::item {{ padding: 5px 2px; }}"
            f"QTreeWidget::item:selected {{ background: {colors.select}; "
            f"color: {colors.text}; }}"
            f"QTreeWidget::item:selected:first {{ border-top-left-radius: 8px; "
            f"border-bottom-left-radius: 8px; }}"
            f"QTreeWidget::item:selected:last {{ border-top-right-radius: 8px; "
            f"border-bottom-right-radius: 8px; }}"
            f"QTreeWidget::branch:selected {{ background: transparent; }}"
            f"QListWidget {{ background: transparent; border: none; }}")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.addWidget(self.new_button)
        side_layout.addSpacing(6)

        # The open fleet's agents, above the list of projects.
        self.show_all = QPushButton("Show all", objectName="nav")
        self.show_all.clicked.connect(lambda: self.agents.setCurrentItem(None))
        self.show_all.hide()
        self.agents_section = QWidget()
        section = QVBoxLayout(self.agents_section)
        section.setContentsMargins(0, 0, 0, 8)
        heading = QHBoxLayout()
        heading.addWidget(label("Agents", role="small", tone="muted"), 1)
        heading.addWidget(self.show_all)
        section.addLayout(heading)
        section.addWidget(self.agents)
        side_layout.addWidget(self.agents_section)

        self.projects = QListWidget(objectName="conversations")
        self.projects.setItemDelegate(RowDelegate(self.projects))
        self.projects.setFrameShape(QFrame.Shape.NoFrame)
        self.projects.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.projects.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.projects.itemClicked.connect(self._project_chosen)
        self.projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects.customContextMenuRequested.connect(self._project_menu)
        self.projects_label = label("Projects", role="small", tone="muted")
        side_layout.addWidget(self.projects_label)
        side_layout.addWidget(self.projects, 1)
        # The corner at the bottom, as in Kayden: who you are, with the app's menu, and
        # the settings.
        user = getpass.getuser()
        self.profile_button = QToolButton(objectName="profile", text=f" {user}")
        self.profile_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.profile_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.profile_button.setIcon(theme.avatar(user))
        self.profile_button.setIconSize(QSize(22, 22))
        self.profile_button.setAccessibleName("Profile and settings")
        self.profile_button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Fixed)
        self.gear_button = QToolButton(objectName="gear")
        self.gear_button.setToolTip("Settings")
        self.gear_button.setAccessibleName("Settings")
        self.gear_button.setIcon(theme.icon("gear", colors.muted, 18))
        self.gear_button.setIconSize(QSize(18, 18))
        self.gear_button.clicked.connect(self.open_settings)
        menu = QMenu(self)
        menu.addAction(user).setEnabled(False)
        menu.addSeparator()
        menu.addAction("Settings").triggered.connect(self.open_settings)
        self.app_menu_action = menu.addAction("Add to App Menu")
        self.app_menu_action.triggered.connect(lambda: self._run(self._add_to_app_menu()))
        self.app_menu_action.setVisible(False)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.close)
        self.profile_button.setMenu(menu)
        corner = QHBoxLayout()
        corner.setContentsMargins(0, 6, 0, 0)
        corner.addWidget(self.profile_button, 1)
        corner.addWidget(self.gear_button)
        side_layout.addLayout(corner)

        self.back_button = QToolButton(objectName="titleControl")
        self.back_button.setText("←")
        self.back_button.setToolTip("The project")
        self.back_button.setAccessibleName("Back to the project")
        self.back_button.setFixedSize(WINDOW_BUTTON)
        self.back_button.clicked.connect(self.show_chart)
        self.title = ElidedLabel()
        self.title.setProperty("role", "heading")
        self.status = label("", role="small", tone="muted", wrap=False)
        self.stop_button = QPushButton("Stop")
        self.resume_button = QPushButton("Resume")
        self.resume_button.setProperty("primary", True)
        self.stop_button.clicked.connect(lambda: self._run(self.stop()))
        self.resume_button.clicked.connect(lambda: self._run(self.resume()))
        self.bar = DragArea()
        self.bar.setFixedHeight(BAR)
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(20, 0, 0, 0)  # the controls reach the window's corner
        bar.setSpacing(4)
        bar.addWidget(self.back_button)
        bar.addWidget(self.title, 1)
        self._bar_gap = QWidget()  # keeps the controls at the right while nothing is named
        bar.addWidget(self._bar_gap, 1)
        bar.addWidget(self.status)
        bar.addSpacing(8)
        bar.addWidget(self.stop_button)
        bar.addWidget(self.resume_button)
        bar.addSpacing(16)
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

        self.project_form = ProjectForm()
        self.project_form.submitted.connect(self._create_clicked)
        self.start_form = StartForm()
        self.start_form.submitted.connect(self._start_clicked)
        self.start_form.budget.setValue((self._config or FleetConfig()).budget_usd)
        self.chart = Chart()
        self.chart.opened.connect(self._chart_clicked)
        self.chart.add_button.clicked.connect(self.add_fleet)
        self.chart.add_asked.connect(self._add_agent)
        self.chart.model_asked.connect(self._choose_model)
        self.chart.start_asked.connect(self._start_asked)
        self.chart.delete_asked.connect(self._ask_delete_fleet)
        self.chart.rename_asked.connect(self._ask_rename)
        self.chart_scroll = QScrollArea()
        self.chart_scroll.setWidgetResizable(True)
        self.chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chart_scroll.setWidget(self.chart)
        self.fleet_pages = QStackedWidget()  # a page for each fleet read
        self.pages = QStackedWidget()
        self._project_page, self._start_page = (_centred(self.project_form),
                                                _centred(self.start_form))
        self.draft_pages = QStackedWidget()  # a page for each draft
        for page in (self._project_page, self._start_page, self.chart_scroll,
                     self.fleet_pages, self.draft_pages):
            self.pages.addWidget(page)

        whole = QWidget()
        layout = QHBoxLayout(whole)
        layout.setContentsMargins(0, 0, 0, 0)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.bar)
        right.addWidget(self.pages, 1)
        layout.addWidget(side)
        layout.addLayout(right, 1)
        self.setCentralWidget(whole)

    # ── What the person does ─────────────────────────────────────────────

    def project_views(self) -> list[FleetView]:
        """The open project's fleets, oldest first."""
        if self.project is None:
            return []
        fleets = self.project.folder / "fleets"
        return [view for folder, view in sorted(self.views.items())
                if folder.parent == fleets]

    async def create_project(self, name: str, work: Path, description: str = "") -> None:
        """A new project; with a description, an agent drafts its fleets."""
        try:
            made = await asyncio.to_thread(project.create, self.home, name, work)
            config = self._config or await asyncio.to_thread(load, self.home / CONFIG_FILE)
        except (ValueError, OSError) as e:
            self.project_form.warn(f"The project could not be made: {e}")
            return
        self.project_form.name.clear()
        self.project_form.does.clear()
        self.project_form.set_folder(None)
        if description:
            page = self.drafts[made.folder] = DraftPage(made, description)
            page.budget.setValue(config.budget_usd)
            page.start_asked.connect(lambda: self._run(self.start_draft(page)))
            page.over.connect(lambda: self._drop_draft(page))
            self.draft_pages.addWidget(page)
            page.run(config, self._harness)
        await self.open_project(made)

    async def start_draft(self, page: DraftPage) -> None:
        """Start every fleet of the draft, and show the project's chart."""
        fleets = [(row.name.text().strip(), row.does.toPlainText().strip(),
                   row.kind.duty.isChecked()) for row in page.rows]
        names = [name.lower() for name, _, _ in fleets]
        if not fleets or not all(name and does for name, does, _ in fleets):
            return page.warn("Each fleet needs a name and what it does.")
        if len(set(names)) < len(names):
            return page.warn("Two fleets have the same name.")
        page.start.setEnabled(False)
        for name, does, ongoing in fleets:
            await self.start_fleet(name, does, ongoing=ongoing, work=page.project.work,
                                   budget=page.budget.value(), opened=page.project,
                                   show=False)
        self._drop_draft(page)

    def _drop_draft(self, page: DraftPage) -> None:
        if self.drafts.pop(page.project.folder, None) is page:
            self.draft_pages.removeWidget(page)
            page.deleteLater()
            if self.project is not None and self.project.folder == page.project.folder:
                self.adding = False
                self._prepare_start()
            self._refresh()

    async def open_project(self, opened: Project) -> None:
        """Show the project's chart, which is empty until it has a fleet."""
        self.project, self.view = opened, None
        folders = await asyncio.to_thread(opened.fleets)
        for folder in folders:
            if folder in self.views:
                continue
            view = self._view(folder)
            try:
                await view.load()
            except (ValueError, OSError, KeyError):
                self._drop(view)  # not a fleet this version reads
        if self.project is opened:
            self.adding = False
            self._prepare_start()
        await self.list_projects()

    def new_project(self) -> None:
        """The form for a project; fleets that run keep running."""
        self.project = self.view = None
        self.adding = False
        self._refresh()
        self.project_form.name.setFocus()

    def add_fleet(self) -> None:
        self.view, self.adding = None, True
        self._prepare_start()
        self._refresh()
        self.start_form.name.setFocus()

    def show_chart(self) -> None:
        self.view, self.adding = None, False
        self._refresh()

    def open_fleet(self, view: FleetView, unit_id: str | None = None) -> None:
        """The fleet's timeline, for one agent or for all."""
        self.view, self.adding = view, False
        self.fleet_pages.setCurrentWidget(view)
        self._refresh()
        self.agents.setCurrentItem(self._rows.get(unit_id or ""))

    async def start_fleet(self, name: str, task: str, *, ongoing: bool, work: Path,
                          budget: float, opened: Project | None = None,
                          show: bool = True) -> None:
        """A new fleet on the task, in `opened` or the open project; `show` opens it. With
        no task it has no agents and runs nothing: the person builds it on the chart."""
        opened = opened or self.project
        assert opened is not None
        try:
            config = self._config or await asyncio.to_thread(load, self.home / CONFIG_FILE)
            config = replace(config, budget_usd=budget)
            view = self._view(await asyncio.to_thread(opened.new_fleet))
            if not task:
                await asyncio.to_thread(lay_out, config, view.folder, work, name)
                await view.load()
                self.start_form.name.clear()
                self.show_chart()
                await self.list_projects()
                return
            started = asyncio.ensure_future(
                view.start(config, name, task, ongoing=ongoing, work=work))
            await asyncio.sleep(0)  # the manager is made, or could not be
            if started.done():
                started.result()
        except (ValueError, OSError) as e:
            self.start_form.warn(f"The fleet could not start: {e}")
            return
        if show:
            self.start_form.name.clear()
            self.start_form.task.clear()
            self.open_fleet(view)
        await started
        await self.list_projects()

    async def resume(self) -> None:
        if self.view is not None:
            await self.view.resume()

    async def stop(self) -> None:
        if self.view is not None:
            await self.view.stop()

    async def shut(self) -> None:
        """Stop every fleet and close the window."""
        for page in list(self.drafts.values()):
            page.cancel()
        await asyncio.gather(*(view.stop() for view in self.views.values()))
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        self.closed.set()
        self.close()

    def _view(self, folder: Path) -> FleetView:
        view = FleetView(folder, self._harness)
        view.changed.connect(self._refresh)
        self.views[folder] = view
        self.fleet_pages.addWidget(view)
        return view

    def _drop(self, view: FleetView) -> None:
        del self.views[view.folder]
        self.fleet_pages.removeWidget(view)
        view.deleteLater()

    def _prepare_start(self) -> None:
        if self.project is not None:
            self.start_form.set_folder(self.project.work)
            self.start_form.heading.setText(f"New fleet in {self.project.name}")

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

    async def _open_newest(self) -> None:
        """The window opens on the project made last."""
        found = await asyncio.to_thread(project.every, self.home)
        if found and self.project is None and not self.views:
            await self.open_project(found[0])
        else:
            await self.list_projects()

    async def list_projects(self) -> None:
        """Fill the sidebar's list of projects."""
        found = await asyncio.to_thread(project.every, self.home)
        counts = await asyncio.to_thread(lambda: [len(p.fleets()) for p in found])
        self.projects.clear()
        for listed, fleets in zip(found, counts, strict=True):
            item = QListWidgetItem(listed.name)
            running = sum(1 for folder, view in self.views.items()
                          if view.running and folder.parent.parent == listed.folder)
            preview = "1 fleet" if fleets == 1 else f"{fleets} fleets"
            item.setData(PREVIEW_ROLE, f"{preview} · {running} running" if running
                         else preview)
            item.setData(DATE_ROLE, _when(listed.folder.name))
            item.setData(FOLDER_ROLE, str(listed.folder))
            item.setData(FLEETS_ROLE, fleets)
            item.setToolTip(str(listed.work))
            self.projects.addItem(item)
        self._refresh()

    def _project_menu(self, at: QPoint) -> None:
        if (item := self.projects.itemAt(at)) is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename…").triggered.connect(lambda: self.ask_rename(item))
        menu.addAction("Delete…").triggered.connect(lambda: self.ask_delete(item))
        menu.popup(self.projects.viewport().mapToGlobal(at))

    def ask_rename(self, item: QListWidgetItem) -> None:
        """Ask for the listed project's new name; an empty one changes nothing."""
        folder = Path(item.data(FOLDER_ROLE))
        dialog = self.dialog = LineDialog(self, "Rename project", item.text())

        async def rename() -> None:
            if not (name := dialog.line.text().strip()):
                return
            with contextlib.suppress(ValueError, KeyError, OSError):
                renamed = await asyncio.to_thread(
                    lambda: project.rename(project.load(folder), name))
                if self.project is not None and self.project.folder == folder:
                    self.project = renamed
            await self.list_projects()

        dialog.accepted.connect(lambda: self._run(rename()))
        dialog.open()

    def _ask_rename(self, key: str, unit_id: str) -> None:
        """Ask for a new name for the fleet's agent, or for the fleet ("")."""
        view = self.views.get(Path(key))
        if view is None or view.fleet is None:
            return
        unit = view.fleet.units.get(unit_id)
        dialog = self.dialog = LineDialog(
            self, "Rename agent" if unit is not None else "Rename fleet",
            unit.name if unit is not None else view.name)

        async def rename() -> None:
            if name := dialog.line.text().strip():
                await view.rename(name, unit)

        dialog.accepted.connect(lambda: self._run(rename()))
        dialog.open()

    def ask_delete(self, item: QListWidgetItem) -> None:
        """Ask whether the listed project should go, and delete it on a yes."""
        folder = Path(item.data(FOLDER_ROLE))
        fleets = item.data(FLEETS_ROLE)
        has = "its 1 fleet" if fleets == 1 else f"its {fleets} fleets"
        dialog = self.dialog = DeleteDialog(self, "Delete project", f"{item.text()} and {has}")
        dialog.accepted.connect(lambda: self._run(self.delete_project(folder)))
        dialog.open()

    def _ask_delete_fleet(self, key: str) -> None:
        if (view := self.views.get(Path(key))) is None:
            return
        dialog = self.dialog = DeleteDialog(self, "Delete fleet", view.name)
        dialog.accepted.connect(lambda: self._run(self.delete_fleet(view)))
        dialog.open()

    async def delete_fleet(self, view: FleetView) -> None:
        """Stop the fleet and remove its folder; the work folder stays."""
        await view.stop()
        if self.view is view:
            self.view = None
        if view.folder in self.views:
            self._drop(view)
        with contextlib.suppress(ValueError, OSError):
            await asyncio.to_thread(fleet_record.delete, view.folder)
        await self.list_projects()

    async def delete_project(self, folder: Path) -> None:
        """Stop the project's fleets and remove its folder; the work folder stays."""
        for path, view in list(self.views.items()):
            if path.parent.parent == folder:
                await view.stop()
                if self.view is view:
                    self.view = None
                self._drop(view)
        if (page := self.drafts.pop(folder, None)) is not None:
            page.cancel()
            self.draft_pages.removeWidget(page)
            page.deleteLater()
        with contextlib.suppress(ValueError, OSError):
            await asyncio.to_thread(project.delete, folder)
        if self.project is not None and self.project.folder == folder:
            self.project, self.adding = None, False
            if left := await asyncio.to_thread(project.every, self.home):
                return await self.open_project(left[0])
        await self.list_projects()

    def _project_chosen(self, item: QListWidgetItem) -> None:
        folder = Path(item.data(FOLDER_ROLE))
        if self.project is None or folder != self.project.folder:
            self._run(self._open_folder(folder))

    async def _open_folder(self, folder: Path) -> None:
        try:
            opened = await asyncio.to_thread(project.load, folder)
        except (ValueError, KeyError, OSError):
            return await self.list_projects()
        await self.open_project(opened)

    async def _offer_app_menu(self) -> None:
        """The button is there until the app menu starts this install."""
        if app_menu.supported():
            self.app_menu_action.setVisible(not await asyncio.to_thread(app_menu.is_current))

    async def _add_to_app_menu(self) -> None:
        try:
            await asyncio.to_thread(app_menu.install)
        except OSError:
            return
        self.app_menu_action.setVisible(False)

    def open_settings(self) -> None:
        """What new fleets get, from the settings file in the app's home."""
        path = self.home / CONFIG_FILE
        try:
            current = load(path)
        except (ValueError, OSError):
            current = FleetConfig()  # a file that cannot be read is written anew
        dialog = self.dialog = SettingsDialog(self, path, current)
        dialog.saved.connect(self._settings_saved)
        dialog.open()

    def _settings_saved(self, made: FleetConfig) -> None:
        if self._config is None:
            self.start_form.budget.setValue(made.budget_usd)

    def _create_clicked(self) -> None:
        form = self.project_form
        name = form.name.text().strip()
        if not name:
            form.name.setFocus()
            return
        work, problem = _work_folder(form.folder)
        if work is None:
            form.warn(problem)
            return
        self._run(self.create_project(name, work, form.does.toPlainText().strip()))

    def _start_clicked(self) -> None:
        form = self.start_form
        name, task = form.name.text().strip(), form.task.toPlainText().strip()
        if not name:
            form.name.setFocus()
            return
        work, problem = _work_folder(form.folder)
        if work is None:
            form.warn(problem)
            return
        self._run(self.start_fleet(name, task, ongoing=form.duty.isChecked(), work=work,
                                   budget=form.budget.value()))

    def _chart_clicked(self, key: str, unit_id: str) -> None:
        if (view := self.views.get(Path(key))) is not None:
            self.open_fleet(view, unit_id or None)

    def _start_asked(self, key: str) -> None:
        if (view := self.views.get(Path(key))) is None:
            return

        async def start() -> None:
            await view.resume()
            if not view.running:
                self.open_fleet(view)  # where it says why not

        self._run(start())

    def _add_agent(self, key: str, unit_id: str) -> None:
        """Ask for an agent to put under the unit ("": as the fleet's lead), and make it."""
        view = self.views.get(Path(key))
        if view is None or view.fleet is None:
            return
        supervisor = view.fleet.units.get(unit_id)
        dialog = self.dialog = AgentDialog(
            self, supervisor, supervisor.rank - 1 if supervisor else view.fleet.top_rank)

        async def add() -> None:
            name, task = dialog.name.text().strip(), dialog.task.toPlainText().strip()
            try:
                await view.add_agent(supervisor, name, task,
                                     ongoing=dialog.kind.duty.isChecked(),
                                     model=dialog.model.text().strip() or None)
            except (FleetError, UnitError, OSError) as e:
                return dialog.warn(str(e))
            dialog.accept()
            self._refresh()

        dialog.add_asked.connect(lambda: self._run(add()))
        dialog.open()

    def _choose_model(self, key: str, unit_id: str) -> None:
        view = self.views.get(Path(key))
        if view is None or view.fleet is None:
            return
        unit = view.fleet.units[unit_id]
        dialog = self.dialog = ModelDialog(self, unit)
        dialog.accepted.connect(
            lambda: self._run(view.set_model(unit, dialog.model.text().strip())))
        dialog.open()

    def _agent_chosen(self, item: QTreeWidgetItem | None, _: object = None) -> None:
        if self.view is not None:
            self.view.choose(item.data(0, UNIT_ROLE) if item is not None else None)
            self.show_all.setVisible(self.view.shown is not None)

    # ── What is shown ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """The bar, the page and the sidebar, from things as they are now."""
        opened, view = self.project, self.view
        fleets = self.project_views()
        drafted = self.drafts.get(opened.folder) if opened is not None else None
        if drafted is not None and view is None and not self.adding:
            self.draft_pages.setCurrentWidget(drafted)
        else:
            drafted = None
        self.pages.setCurrentWidget(
            self._project_page if opened is None else self._start_page if self.adding
            else self.fleet_pages if view is not None
            else self.draft_pages if drafted is not None else self.chart_scroll)
        on_chart = (opened is not None and view is None and not self.adding
                    and drafted is None)
        self.back_button.setVisible(view is not None or self.adding)
        self.title.setVisible(opened is not None)
        self._bar_gap.setVisible(opened is None)
        self.status.setVisible(view is not None or on_chart)
        self.stop_button.setVisible(view is not None and view.running)
        self.resume_button.setVisible(view is not None and view.can_resume)
        self.resume_button.setText("Resume" if view is None or view.started else "Start")
        self.agents_section.setVisible(view is not None)
        self.projects_label.setVisible(self.projects.count() > 0)
        for i in range(self.projects.count()):
            item = self.projects.item(i)
            if opened is not None and item.data(FOLDER_ROLE) == str(opened.folder):
                self.projects.setCurrentItem(item)
        if opened is None:
            self.projects.setCurrentItem(None)
            return
        if view is None:
            self.title.setText(opened.name)
            running = sum(1 for shown in fleets if shown.running)
            spent = sum(shown.fleet.spent_usd for shown in fleets if shown.fleet is not None)
            self.status.setText(f"{running} running · ${spent:.2f}")
            if on_chart:
                self.chart.show_fleets([ChartFleet(str(shown.folder), shown.name,
                                                   shown.status, shown.fleet,
                                                   running=shown.running,
                                                   editable=not shown.stopping,
                                                   start="" if not shown.can_resume
                                                   else "Resume" if shown.started
                                                   else "Start")
                                        for shown in fleets])
            return
        chosen = view.chosen
        self.title.setText(f"{view.name} › {chosen.called}" if chosen else view.name)
        self.status.setText(f"{chosen.state} · ${chosen.spent_usd:.2f}" if chosen
                            else view.status)
        self._show_agents(view)

    def _show_agents(self, view: FleetView) -> None:
        if self._rows_of is not view:
            self._rows.clear()
            self.agents.clear()
            self._rows_of = view
        fleet = view.fleet
        if fleet is None:
            return
        colors = theme.current()
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
            row.setText(0, f"{unit.name}  K{unit.rank}" if unit.name else f"K{unit.rank}")
            row.setText(1, unit.state)
            row.setText(2, f"${unit.spent_usd:.2f}")
            # What an agent was doing when its fleet stopped is not what it does now.
            tone = {"working": colors.accent, "starting": colors.accent,
                    "waiting": colors.warning}.get(unit.state, colors.muted)
            tone = {"failed": colors.error, "done": colors.ok}.get(
                unit.state, tone if view.running else colors.muted)
            row.setForeground(1, QColor(tone))
            row.setForeground(2, QColor(colors.muted))
            row.setToolTip(0, unit.task)
        # As tall as its agents, up to a part of the sidebar: the projects have the rest.
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
