"""The window (`kfm-desktop`): projects, each one's fleets as a chart, and for a fleet its
agents, what passes between them and the person's part in it. A frontend on Managers, like
the terminal's Console: their events in, their methods out. Built on Kayden's desktop code: its event loop, theme and Markdown
view."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Coroutine
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
    DragArea,
    ElidedLabel,
    MarkdownView,
    MessageBox,
    RoundedCorners,
    RowDelegate,
    WindowEdges,
)
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QDoubleValidator, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
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
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kfm import app_menu, draft, harnesses, log, project
from kfm.api import Harness, UnitError
from kfm.chart import Chart, ChartFleet
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load
from kfm.events import Answered, Approval, Changed, Delivered, FleetEvent, Notice, Said, Told
from kfm.fleet import Fleet, FleetError, Unit
from kfm.manager import Manager
from kfm.project import Project

SIDEBAR = 280  # px
FORM = 760  # px, the start screen's width
LOGO = 64  # px, on the start screen
AGENT_ROWS = 10  # agents shown before their list scrolls
AGENT_ROW_PX = 30
FOLDER_ROLE = Qt.ItemDataRole.UserRole  # a project row's folder
QUIET_STATES = ("starting", "working", "waiting", "done")  # shown by the agents list alone
BAR = 48  # px, the bar on top
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
        self.task.setPlaceholderText("What it does")
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
    """An agent the person puts under another: its name, its task, and a model of its own
    if it should not run on its rank's. `add_asked` when the person wants it made."""

    add_asked = Signal()

    def __init__(self, parent: QWidget, supervisor: Unit) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add agent under {supervisor.name}")
        self.setMinimumWidth(460)
        rows = QVBoxLayout(self)
        rows.setSpacing(10)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name")
        self.task = MessageBox()
        self.task.setPlaceholderText("Its task")
        self.kind = KindSwitch()
        self.kind.task_kind.setChecked(True)
        self.model = QLineEdit()
        self.model.setPlaceholderText(f"Model (K{supervisor.rank - 1}'s, if empty)")
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


class ModelDialog(QDialog):
    """The model an agent runs on; empty: its rank's."""

    def __init__(self, parent: QWidget, unit: Unit) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{unit.name}'s model")
        self.setMinimumWidth(380)
        rows = QVBoxLayout(self)
        self.model = QLineEdit(unit.model or "")
        self.model.setPlaceholderText(f"K{unit.rank}'s, if empty")
        self.model.returnPressed.connect(self.accept)
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(save)
        rows.addWidget(self.model)
        rows.addLayout(foot)


# ── A fleet ───────────────────────────────────────────────────────────────


class FleetView(QWidget):
    """One fleet: its timeline, the box for messages to its lead, and its manager while it
    runs. `changed` after anything that alters how it stands."""

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
        box = QFrame(objectName="composer")
        box.setMaximumWidth(COLUMN)
        inside = QHBoxLayout(box)
        inside.setContentsMargins(16, 10, 10, 10)
        inside.addWidget(self.input, 1)
        inside.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        composer = QHBoxLayout()
        composer.setContentsMargins(0, 0, 0, 0)
        composer.addStretch(1)
        composer.addWidget(box, 100)
        composer.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 14)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(composer)
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
                and any(unit.active for unit in self.fleet.units.values()))

    @property
    def status(self) -> str:
        """How it stands and what it has spent of its limit."""
        fleet = self.fleet
        if fleet is None:
            return ""
        budget = (self.manager.config.budget_usd if self.manager is not None
                  else fleet.settings.get("budget_usd", 0.0))
        state = "running" if self.running else "stopping" if self.stopping else "stopped"
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
            self.changed.emit()

    def choose(self, unit_id: str | None) -> None:
        """Show only what concerns the agent; None: everything."""
        self.shown = unit_id
        for entry in self.entries:
            entry.setVisible(self._concerns(entry))

    def say(self, text: str, tone: str) -> None:
        self._add(Line(text, set(), tone))

    def _enable(self) -> None:
        self.input.setEnabled(self.running)
        self.send_button.setEnabled(self.running)

    def _send_clicked(self) -> None:
        text = self.input.toPlainText().strip()
        if text and self.manager is not None:
            self.input.clear()
            self._run(self._tell(self.manager, text))

    async def _tell(self, manager: Manager, text: str) -> None:
        try:
            await manager.say(text)
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
                self.say(text, tone="warning")

    def _add(self, entry: QWidget) -> None:
        entry.setMaximumWidth(COLUMN)
        self.entries.append(entry)
        self._timeline.insertWidget(self._timeline.count() - 1, entry)
        entry.setVisible(self._concerns(entry))

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
            f"QTreeWidget {{ background: transparent; border: none; }}"
            f"QTreeWidget::item {{ padding: 5px 2px; }}"
            f"QTreeWidget::item:selected {{ background: {colors.select}; "
            f"color: {colors.text}; }}"
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
        self.projects_label = label("Projects", role="small", tone="muted")
        side_layout.addWidget(self.projects_label)
        side_layout.addWidget(self.projects, 1)
        self.app_menu_button = QPushButton("Add to App Menu", objectName="nav")
        self.app_menu_button.clicked.connect(lambda: self._run(self._add_to_app_menu()))
        self.app_menu_button.hide()
        side_layout.addWidget(self.app_menu_button)

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
        """A new fleet on the task, in `opened` or the open project; `show` opens it."""
        opened = opened or self.project
        assert opened is not None
        try:
            config = self._config or await asyncio.to_thread(load, self.home / CONFIG_FILE)
            config = replace(config, budget_usd=budget)
            view = self._view(await asyncio.to_thread(opened.new_fleet))
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
            item.setToolTip(str(listed.work))
            self.projects.addItem(item)
        self._refresh()

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
            self.app_menu_button.setVisible(not await asyncio.to_thread(app_menu.is_current))

    async def _add_to_app_menu(self) -> None:
        try:
            await asyncio.to_thread(app_menu.install)
        except OSError:
            return
        self.app_menu_button.hide()

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
        if not name or not task:
            (form.task if name else form.name).setFocus()
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

    def _add_agent(self, key: str, unit_id: str) -> None:
        """Ask for an agent to put under the unit, and make it."""
        view = self.views.get(Path(key))
        if view is None or view.manager is None:
            return
        manager, supervisor = view.manager, view.manager.fleet.units[unit_id]
        dialog = self.dialog = AgentDialog(self, supervisor)

        async def add() -> None:
            name, task = dialog.name.text().strip(), dialog.task.toPlainText().strip()
            if not name or not task:
                return dialog.warn("It needs a name and a task.")
            try:
                await manager.hire(supervisor, name, task,
                                   ongoing=dialog.kind.duty.isChecked(),
                                   model=dialog.model.text().strip() or None)
            except (FleetError, UnitError) as e:
                return dialog.warn(str(e))
            dialog.accept()
            self._refresh()

        dialog.add_asked.connect(lambda: self._run(add()))
        dialog.open()

    def _choose_model(self, key: str, unit_id: str) -> None:
        view = self.views.get(Path(key))
        if view is None or view.manager is None:
            return
        manager, unit = view.manager, view.manager.fleet.units[unit_id]
        dialog = self.dialog = ModelDialog(self, unit)
        dialog.accepted.connect(
            lambda: self._run(manager.set_model(unit, dialog.model.text().strip())))
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
                                                   shown.status, shown.fleet, shown.running)
                                        for shown in fleets])
            return
        self.title.setText(view.name)
        self.status.setText(view.status)
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
            row.setText(0, f"{unit.name}  K{unit.rank}")
            row.setText(1, unit.state)
            row.setText(2, f"${unit.spent_usd:.2f}")
            tone = {"working": colors.accent, "starting": colors.accent,
                    "waiting": colors.warning, "failed": colors.error,
                    "done": colors.ok}.get(unit.state, colors.muted)
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
