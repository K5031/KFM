"""The window (`kfm-desktop`): projects, each one's fleets as a chart, and for a fleet its
agents, what passes between them and the person's part in it. A frontend on Managers, like
the terminal's Console: their events in, their methods out. Built on Kaden's desktop code: its event loop, theme and Markdown
view."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

import kaden.frontends.desktop  # noqa: F401 - before any Qt import
from kaden.frontends.common import PERMISSION_LABELS, PERMISSION_SHORT
from kaden.frontends.desktop import theme
from kaden.frontends.desktop.loop import QtEventLoop
from kaden.frontends.desktop.window import (
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
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from kfm import app_menu, draft, project
from kfm.api import UnitError
from kfm.chart import Chart, ChartFleet, state_tone
from kfm.config import AUTO, HOME, MANUAL, FleetConfig
from kfm.conversation import AgentPage
from kfm.events import (
    Across,
    Answered,
    Approval,
    Changed,
    Delivered,
    Dismissed,
    FleetEvent,
    Instructed,
    Notice,
    Said,
    Told,
    units,
)
from kfm.fleet import Fleet, FleetError, Unit
from kfm.notify import Notifier
from kfm.project import SECRETARY
from kfm.remote import RemoteService, ServiceError
from kfm.service import (
    Happened,
    Listed,
    Live,
    ProjectInfo,
    Proposed,
    Service,
    Update,
    Updated,
    Within,
)
from kfm.tray import Tray

SIDEBAR = 288  # px, as Claude's and Kaden's
FORM = 760  # px, the start screen's width
AGENT_ROWS = 10  # agents shown before their list scrolls
AGENT_ROW_PX = 30
PROJECT_ROLE = Qt.ItemDataRole.UserRole  # a project row's id
FLEETS_ROLE = Qt.ItemDataRole.UserRole + 10  # how many fleets it has
UNREADABLE_ROLE = Qt.ItemDataRole.UserRole + 11  # made by another version: only deleted
QUIET_STATES = ("starting", "working", "waiting", "asking", "done")  # shown by the agents list alone
OVER = ("done", "idle", "failed", "stopped")  # an agent that is at nothing
THINKING = 600  # characters, the end, of what an agent is thinking, under its feed
BAR = 48  # px, the bar on top
COLUMN = 780  # px, the widest the feed's entries get, as Kaden's column
# What the service refuses, with words for the person (kfm.service).
REFUSALS = (FleetError, UnitError, ValueError, OSError, RuntimeError)
logger = logging.getLogger(__name__)
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


class Warns:
    """For a form or a dialog with a line (`warning`) that says what is wrong."""

    warning: QLabel

    def warn(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))


def foot(dialog: QDialog, verb: str, then: Callable[[], object], *,
         before: QWidget | None = None, safe: bool = False) -> tuple[QHBoxLayout, QPushButton]:
    """The bottom of a dialog: Cancel, and the button that does it (`verb`, pressed:
    `then`), with `before` at the left. `safe`: Enter cancels. Returns the row and the
    button."""
    do = QPushButton(verb)
    do.setProperty("primary", True)
    do.clicked.connect(then)
    cancel = QPushButton("Cancel")
    cancel.clicked.connect(dialog.reject)
    cancel.setDefault(safe)
    row = QHBoxLayout()
    if before is not None:
        row.addWidget(before)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(do)
    return row, do


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


# ── The feed's entries. `concerns`: the units an entry is about; none: everyone. ──


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

    def __init__(self, unit_id: str, unit: Unit | None, text: str) -> None:
        """`unit`: who wrote it, as the fleet's record has it."""
        super().__init__()
        self.concerns = {unit_id}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 4, 15, 4)  # in line with a card's text
        layout.setSpacing(3)
        self.unit = unit_id
        self.header = QWidget()  # hidden where the page is this agent's conversation
        who = QHBoxLayout(self.header)
        who.setContentsMargins(0, 0, 0, 0)
        who.setSpacing(6)
        name = unit.name if unit is not None else ""
        who.addWidget(label(name or unit_id, role="strong", wrap=False))
        who.addWidget(label(unit_id if name else "", role="small", tone="muted", wrap=False))
        who.addStretch(1)
        self.body = MarkdownView()
        self.body.setMarkdown(text)
        layout.addWidget(self.header)
        layout.addWidget(self.body)


class Attention(QFrame):
    """What waits for the person, wherever in the window they are: each approval an agent
    asks for, with its answers, and each question a lead has put to them, with the way
    to its fleet. An agent that asks stands still until it is answered."""

    def __init__(self) -> None:
        super().__init__(objectName="attention")
        self.setStyleSheet(
            f"QFrame#attention {{ border-bottom: 1px solid {theme.current().line}; }}")
        self._rows = QVBoxLayout(self)
        self._rows.setContentsMargins(20, 8, 20, 8)
        self._rows.setSpacing(6)
        self.shown: list[str] = []  # what each row stands for
        self.rows: list[QWidget] = []
        self.hide()

    def show_rows(self, rows: Sequence[tuple[str, str, Sequence[tuple[str, Callable[[], None]]]]]
                  ) -> None:
        """`rows`: what each stands for, its text, and its buttons (the first is the one
        to press). Left alone while they stand for the same things."""
        keys = [key for key, _, _ in rows]
        if keys == self.shown:
            return
        self.shown = keys
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        for _, text, buttons in rows:
            row = QWidget()
            across = QHBoxLayout(row)
            across.setContentsMargins(0, 0, 0, 0)
            across.setSpacing(8)
            row.text = ElidedLabel(text)  # type: ignore[attr-defined]
            row.text.setToolTip(text)  # type: ignore[attr-defined]
            across.addWidget(row.text, 1)  # type: ignore[attr-defined]
            row.buttons = []  # type: ignore[attr-defined]
            for index, (word, press) in enumerate(buttons):
                button = QPushButton(word)
                button.setProperty("primary", index == 0)
                button.clicked.connect(lambda _=False, press=press: press())
                across.addWidget(button)
                row.buttons.append(button)  # type: ignore[attr-defined]
            self._rows.addWidget(row)
            self.rows.append(row)
        self.setVisible(bool(rows))


class LiveEntry(QWidget):
    """What an agent is doing as it does it: the tool it is using, the end of what it is
    thinking, the message it is writing. It stands under the feed and is no part of it:
    the message, once whole, takes its place there."""

    def __init__(self, unit_id: str, unit: Unit | None) -> None:
        super().__init__()
        self.concerns = {unit_id}
        self.unit, self.empty = unit_id, True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 4, 15, 4)
        layout.setSpacing(3)
        self.header = QWidget()
        who = QHBoxLayout(self.header)
        who.setContentsMargins(0, 0, 0, 0)
        who.setSpacing(6)
        name = unit.name if unit is not None else ""
        who.addWidget(label(name or unit_id, role="strong", wrap=False))
        who.addWidget(label(unit_id if name else "", role="small", tone="muted", wrap=False))
        who.addStretch(1)
        self.doing = label("", role="small", tone="muted")
        self.thinking = label("", role="small", tone="muted")
        self.thinking.setStyleSheet("font-style: italic;")
        self.writing = label("")
        for part in (self.header, self.doing, self.thinking, self.writing):
            layout.addWidget(part)

    def show_live(self, thinking: str, writing: str, doing: str) -> None:
        thinking = thinking.strip()
        if len(thinking) > THINKING:
            thinking = "…" + thinking[-THINKING:]
        for part, text in ((self.doing, doing), (self.thinking, thinking),
                           (self.writing, writing.strip())):
            part.setText(text)
            part.setVisible(bool(text))
        self.empty = not (thinking or writing.strip() or doing)


class Card(QFrame):
    """Something that went from one to another in the fleet, with its whole text: who
    and what in a line, the text under it."""

    def __init__(self, header: str, text: str, concerns: set[str]) -> None:
        super().__init__()
        self.setObjectName("delivery")
        self.concerns = concerns
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self.header = label(header, role="strong")
        self.body = MarkdownView()
        self.body.setMarkdown(text)
        layout.addWidget(self.header)
        layout.addWidget(self.body)


class DeliveryCard(Card):
    """A report, question, update or failure on its way up."""

    def __init__(self, event: Delivered, called: Callable[[str], str]) -> None:
        target = called(event.to) if event.to is not None else "you"
        super().__init__(f"{called(event.unit)} → {target} · {event.kind}", event.text,
                         units(event))
        self.setProperty("you", event.to is None)  # it is the person's to read or answer


class AcrossCard(Card):
    """A message between an agent and another team of the project, or its secretary."""

    def __init__(self, event: Across, called: Callable[[str], str]) -> None:
        ends = (called(event.unit), event.team) if event.sent else (event.team,
                                                                   called(event.unit))
        super().__init__(f"{ends[0]} → {ends[1]} · message", event.text, {event.unit})


class InstructionCard(Card):
    """What an agent was given by its supervisor: its task, or a message."""

    def __init__(self, event: Instructed, called: Callable[[str], str]) -> None:
        assert event.by is not None
        super().__init__(f"{called(event.by)} → {called(event.unit)} · {event.kind}",
                         event.text, units(event))


class ApprovalCard(QFrame):
    """An agent wants to do something its permissions do not cover."""

    def __init__(self, event: Approval, called: str) -> None:
        super().__init__()
        self.setObjectName("prompt")
        self.concerns = {event.unit}
        self.asked, self.called = event, called
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        self.question = label(f"{called} wants to run {event.tool} {event.arguments}")
        buttons = QHBoxLayout()
        self.allow = QPushButton("Allow")
        self.allow.setProperty("primary", True)
        self.always = QPushButton("Always allow")
        self.always.setToolTip(f"{called} no longer asks to run {event.tool}")
        self.deny = QPushButton("Deny")
        buttons.addWidget(self.allow)
        buttons.addWidget(self.always)
        buttons.addWidget(self.deny)
        buttons.addStretch()
        layout.addWidget(self.question)
        layout.addLayout(buttons)

    def answered(self, allow: bool, always: bool = False) -> None:
        for button in (self.allow, self.always, self.deny):
            button.hide()
        asked = self.asked
        self.question.setText(
            f"Always allowed: {self.called} ran {asked.tool} {asked.arguments}" if always else
            f"Allowed: {self.called} ran {asked.tool} {asked.arguments}" if allow else
            f"Denied: {self.called} wanted to run {asked.tool} {asked.arguments}")


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


class LeadModel(QComboBox):
    """The model of the lead a fleet starts with, where whoever starts an agent names its
    model: one of the person's list. Hidden where models go by rank."""

    def __init__(self) -> None:
        super().__init__()
        self.setToolTip("The model its lead runs on")
        self.setAccessibleName("The model its lead runs on")
        self.hide()

    def offer(self, config: FleetConfig, chosen: str | None = None) -> None:
        """Show the models there are to choose, with `chosen`, else what is chosen now,
        else the secretary's, else the first."""
        wanted = chosen or self.currentText() or config.models.get("secretary", "")
        self.clear()
        self.addItems(config.auto_models)
        self.setCurrentIndex(max(0, self.findText(wanted)))
        self.setVisible(config.auto)

    def model(self) -> str | None:
        return self.currentText() or None if not self.isHidden() else None


class ModelBox(QComboBox):
    """A model to choose or to type: empty until `offer` lists the models there are."""

    def __init__(self, text: str = "", placeholder: str = "") -> None:
        super().__init__(editable=True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # typed is not listed
        self.setCurrentText(text)
        self.lineEdit().setPlaceholderText(placeholder)

    def text(self) -> str:
        return self.currentText()

    def setText(self, text: str) -> None:
        self.setCurrentText(text)

    def offer(self, models: Sequence[tuple[str, str, str]]) -> None:
        """The models there are to choose from, each by its id with its name and price
        beside it in a tooltip; what the field holds stays, listed or not."""
        held = self.currentText()
        self.clear()
        self.addItem("")  # the default
        for model_id, name, about in models:
            self.addItem(model_id)
            self.setItemData(self.count() - 1, ", ".join(p for p in (name, about) if p),
                             Qt.ItemDataRole.ToolTipRole)
        self.setCurrentText(held)


class KindSwitch(QFrame):
    """Task or Continuous, as two segments."""

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
        self.duty = QPushButton("Continuous", objectName="segment")
        self.duty.setToolTip("Runs until you stop it")
        self.group = QButtonGroup(self)
        for button in (self.task_kind, self.duty):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button)
            kinds.addWidget(button)
        self.task_kind.setChecked(True)
        self.setFixedHeight(36)


BOX_STYLE = ("QFrame#budget {{ border: 1px solid {line}; border-radius: 10px; }}"
             "QFrame#budget QLineEdit {{ background: transparent; border: none; padding: 0; }}")


class ComposerForm(Warns, QWidget):
    """A form in the middle of the page: a heading, and one box that holds a
    name, what it is to do, and along its lower edge whatever else it needs (`_lower`).
    `submitted` when the person wants it made."""

    submitted = Signal()
    PICKER = ""  # the title of the dialog that chooses its folder

    def __init__(self, heading: str, name: str, does: str) -> None:
        super().__init__()
        self.setMaximumWidth(FORM)
        self.setMinimumWidth(480)
        colors = theme.current()
        self.folder: Path | None = None
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.addStretch(3)
        self.heading = label(heading, wrap=False)
        self.heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(self.heading)
        outer.addSpacing(22)

        box = QFrame(objectName="composer")
        inside = QVBoxLayout(box)
        inside.setContentsMargins(16, 14, 12, 12)
        inside.setSpacing(12)
        self.name = _name_field(name)
        self.name.returnPressed.connect(lambda: self.task.setFocus())
        inside.addWidget(self.name)
        self.task = TaskBox()
        self.task.setPlaceholderText(does)
        self.task.submitted.connect(self.submitted)
        inside.addWidget(self.task)
        self.folder_button = QToolButton(objectName="folderPicker")
        self.folder_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.folder_button.setIcon(theme.icon("folder", colors.muted, 16))
        self.folder_button.clicked.connect(self._choose)
        row = QHBoxLayout()
        self._lower(row)
        inside.addLayout(row)
        outer.addWidget(box)
        outer.addSpacing(10)
        self.warning = label("", role="small", tone="error")
        self.warning.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        for line in (*self._under(), self.warning):
            outer.addWidget(line)
        outer.addStretch(5)

    def _lower(self, row: QHBoxLayout) -> None:
        """Fill the box's lower edge."""
        raise NotImplementedError

    def _under(self) -> Sequence[QWidget]:
        """What stands under the box, above the warning."""
        return ()

    def sizeHint(self) -> QSize:
        return QSize(FORM, super().sizeHint().height())

    def set_folder(self, folder: Path | None) -> None:
        raise NotImplementedError

    def _choose(self) -> None:
        start = self.folder.parent if self.folder is not None else Path.home()
        dialog = QFileDialog(self, self.PICKER, str(start))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.fileSelected.connect(lambda chosen: self.set_folder(Path(chosen)))
        dialog.open()


class StartForm(ComposerForm):
    """A new fleet: its name, one box for what it should do, and what else a fleet needs
    along the box's lower edge."""

    PICKER = "The folder the fleet works in"

    def __init__(self) -> None:
        super().__init__("New fleet", "Fleet name",
                         "What it does, to have a lead build it (optional)")
        self.setStyleSheet(BOX_STYLE.format(line=theme.current().line))
        self.warning.hide()
        self._kinds.buttonToggled.connect(lambda *_: self._describe())
        self._describe()
        self.set_folder(None)  # the project's, unless another is chosen

    def _lower(self, row: QHBoxLayout) -> None:
        row.setSpacing(8)
        segments = KindSwitch()
        self.task_kind, self.duty, self._kinds = (segments.task_kind, segments.duty,
                                                  segments.group)
        self.folder_button.setToolTip("The folder the agents work in")
        self.budget = Budget()
        self.lead_model = LeadModel()
        self.start = QToolButton(objectName="send")
        self.start.setProperty("active", True)
        self.start.setIcon(theme.icon("send", theme.current().on_accent, 18))
        self.start.setFixedSize(36, 36)
        self.start.setToolTip("Start the fleet")
        self.start.setAccessibleName("Start the fleet")
        self.start.clicked.connect(self.submitted)
        row.addWidget(segments)
        row.addWidget(self.folder_button)
        row.addWidget(self.budget)
        row.addWidget(self.lead_model)
        row.addStretch(1)
        row.addWidget(self.start)

    def _under(self) -> Sequence[QWidget]:
        self.hint = label("", role="small", tone="muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        return (self.hint,)

    def set_folder(self, folder: Path | None) -> None:
        """The folder the fleet will work in; None: not chosen yet."""
        self.folder = Path(folder).expanduser() if folder is not None else None
        chosen = self.folder
        self.folder_button.setText(f" {chosen.name or chosen}" if chosen else " Choose a folder")
        self.folder_button.setToolTip(f"The agents work in {chosen}" if chosen
                                      else "The folder the agents work in")
        self.warn("")

    def _describe(self) -> None:
        self.hint.setText(
            "Runs until you stop it." if self.duty.isChecked() else
            "Ends when the lead reports.")


class ProjectForm(ComposerForm):
    """The window with no project: a name, the folder the project's fleets work in, and
    what it should do when its fleets are to be drafted."""

    PICKER = "The folder the project's fleets work in"

    def __init__(self) -> None:
        super().__init__("New project", "Project name",
                         "What it should do, for its secretary (optional)")
        self.base: Path | None = None  # the projects folder: a project's own is made in it
        self.does = self.task
        self.name.textChanged.connect(lambda: self._show_folder())
        self.set_folder(None)

    def _lower(self, row: QHBoxLayout) -> None:
        self.create = QPushButton("Create")
        self.create.setProperty("primary", True)
        self.create.clicked.connect(self.submitted)
        row.addWidget(self.folder_button)
        row.addStretch(1)
        row.addWidget(self.create)

    def set_folder(self, folder: Path | None) -> None:
        self.folder = Path(folder).expanduser() if folder is not None else None
        self._show_folder()
        self.warn("")

    def _show_folder(self) -> None:
        chosen, made = self.folder, self.own_folder()
        self.folder_button.setText(
            f" {chosen.name or chosen}" if chosen
            else f" {made.parent.name}/{made.name}" if made else " Choose a folder")
        self.folder_button.setToolTip(
            f"Its fleets work in {chosen}" if chosen
            else f"Its fleets work in {made}, which is made for it" if made
            else "The folder its fleets work in")

    def set_base(self, base: Path | None) -> None:
        """The projects folder, in which a project gets a folder of its own."""
        self.base = base
        self._show_folder()

    def own_folder(self) -> Path | None:
        """The folder made for the project when none is chosen: named after it, in the
        projects folder. None without a projects folder, or a name that makes one."""
        name = project.folder_name(self.name.text())
        return self.base / name if self.base is not None and name else None


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
    """A fleet of a draft: its name, what it does, Task or Continuous, and a way out of it."""

    def __init__(self, name: str = "", does: str = "", ongoing: bool = False) -> None:
        super().__init__(objectName="composer")
        self.folder: str | None = None  # one of its own, in the project's, if proposed
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
        self.lead_model = LeadModel()
        row = QHBoxLayout()
        row.addWidget(self.kind)
        row.addWidget(self.lead_model)
        row.addStretch(1)
        inside.addLayout(row)


class DraftPage(QWidget):
    """A project's fleets as an agent drafted them, for the person to change. Nothing runs
    until `start` is clicked. `over`: started or discarded."""

    start_asked = Signal()
    over = Signal()

    def __init__(self, opened: ProjectInfo) -> None:
        super().__init__()
        self.project = opened
        self.rows: list[DraftRow] = []
        self.config = FleetConfig()  # what the rows' model lists are made from
        self.shown: draft.Draft | None = None  # the proposal its rows were made from
        self.busy = True  # the secretary is at it
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
        # While the secretary is at it: the word for that, what she last wrote, and a way out.
        self.waiting = QWidget()
        centre = QVBoxLayout(self.waiting)
        centre.setSpacing(0)
        centre.addStretch(2)
        waiting = label("Drafting…", wrap=False)
        waiting.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        waiting.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.saying = QPlainTextEdit(readOnly=True)  # what she does and thinks, its end
        self.saying.setFrameShape(QFrame.Shape.NoFrame)
        self.saying.setFixedSize(560, 104)
        self.saying.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.saying.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.saying.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; color: {theme.current().muted}; "
            "font-size: 12px; }")
        self.stop = QPushButton("Cancel")
        self.stop.clicked.connect(self.cancel)
        centre.addWidget(waiting)
        centre.addSpacing(10)
        centre.addWidget(self.saying, 0, Qt.AlignmentFlag.AlignHCenter)
        centre.addSpacing(22)
        centre.addWidget(self.stop, 0, Qt.AlignmentFlag.AlignHCenter)
        centre.addStretch(3)
        self._proposal = scroll
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.waiting)
        outer.addWidget(scroll)
        self._show()

    def said(self, text: str) -> None:
        """What the secretary wrote while she is waited for."""
        self.saying.setPlainText(text.strip())
        bar = self.saying.verticalScrollBar()
        bar.setValue(bar.maximum())  # its end is what is read

    def live(self, thinking: str, writing: str, doing: str) -> None:
        """What she is doing as she does it: what she thinks or writes, then the tool in
        use, which is the newest of it while it is shown."""
        parts = [(writing or thinking).strip(), doing]
        if any(parts):
            self.said("\n".join(part for part in parts if part))

    def cancel(self) -> None:
        """Drop the proposal, or stop waiting for one."""
        self.over.emit()

    def add_row(self, name: str = "", does: str = "", ongoing: bool = False) -> DraftRow:
        row = DraftRow(name, does, ongoing)
        row.lead_model.offer(self.config)
        row.remove.clicked.connect(lambda: self._remove(row))
        self.rows.append(row)
        self._column.insertWidget(self._column.count() - 2, row)  # above the foot
        return row

    def warn(self, text: str) -> None:
        self.note.setText(text)
        theme.mark(self.note, "tone", "error")

    def fill(self, made: draft.Draft) -> None:
        """Show what the secretary proposed, in place of what was there. The proposal it
        already shows is left as the person has changed it."""
        if made == self.shown:
            return
        self.shown = made
        for row in list(self.rows):
            self._remove(row)
        for team in made.teams:
            row = self.add_row(team.name, team.does, team.ongoing)
            row.folder = team.folder
            row.lead_model.offer(self.config, team.model)
        self.busy = False
        self._show()
        theme.mark(self.note, "tone", "muted")
        self.note.setText(
            f"Proposed for ${made.spent_usd:.2f}. Nothing runs until you start it.")

    def failed(self) -> None:
        """The secretary ended without a proposal."""
        self.busy = False
        self._show()
        self.warn("No proposal. What the secretary said is on her page.")

    def _remove(self, row: DraftRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _show(self) -> None:
        self.heading.setText("Proposal")
        self.waiting.setVisible(self.busy)
        self._proposal.setVisible(not self.busy)


class AgentDialog(Warns, QDialog):
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
        self.model = ModelBox(placeholder=f"Model (K{rank}'s, if empty)")
        self.warning = label("", role="small", tone="error")
        self.warning.hide()
        row, self.add = foot(self, "Add", self.add_asked, before=self.kind)
        for part in (self.name, self.task, self.model, self.warning):
            rows.addWidget(part)
        rows.addLayout(row)


class DeleteDialog(QDialog):
    """Whether to delete a project or a fleet, or to remove an agent (`what`): what goes,
    and what stays."""

    def __init__(self, parent: QWidget, title: str, what: str, verb: str = "Delete") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        rows = QVBoxLayout(self)
        rows.setSpacing(14)
        self.question = label(f"{verb} {what}? This cannot be undone. The folder the agents "
                              "worked in is kept.")
        row, self.delete = foot(self, verb, self.accept, safe=True)
        rows.addWidget(self.question)
        rows.addLayout(row)


class Amount(QLineEdit):
    """A number in a field like the text fields beside it: dollars, or a count."""

    def __init__(self, value: float, *, money: bool = False) -> None:
        super().__init__()
        self._money = money
        self.setValue(value)

    def setValue(self, value: float) -> None:
        self.setText(f"${value:.2f}" if self._money else str(int(value)))

    def value(self) -> float:
        """What is written; 0 for what is no number."""
        try:
            number = float(self.text().strip().lstrip("$").replace(",", ".") or 0)
        except ValueError:
            return 0
        return number if self._money else int(number)


class DialogGrip(DragArea):
    """Where a dialog without a frame is grabbed to move it; it has one size."""

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.ignore()


class SettingsDialog(Warns, QDialog):
    """KFM's settings: the model of each rank and the limits. `save_asked` when the
    person wants them kept; `changes` is what they set."""

    save_asked = Signal()

    def __init__(self, parent: QWidget, config: FleetConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        # No title bar of the desktop's, as Kaden's settings: its own header, which
        # moves it, and a button that closes it.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setObjectName("settings")
        self.setMinimumWidth(460)
        colors = theme.current()
        header = DialogGrip(objectName="settingsHeader")
        top = QHBoxLayout(header)
        top.setContentsMargins(18, 10, 10, 10)
        top.addWidget(label("Settings", role="title", wrap=False), 1)
        self.close_button = QToolButton(objectName="windowControl")
        self.close_button.setIcon(theme.icon("close", colors.muted))
        self.close_button.setToolTip("Close")
        self.close_button.setAccessibleName("Close")
        self.close_button.clicked.connect(self.reject)
        top.addWidget(self.close_button)
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(22, 18, 22, 18)
        rows.setSpacing(6)
        whole = QVBoxLayout(self)
        whole.setContentsMargins(1, 1, 1, 1)  # inside the border
        whole.setSpacing(0)
        whole.addWidget(header)
        whole.addWidget(body)

        def section(title: str) -> QFormLayout:
            """A part of the settings: its name, and its rows under it."""
            if rows.count():
                rows.addSpacing(12)
            rows.addWidget(label(title, role="small", tone="muted", wrap=False))
            form = QFormLayout()
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(8)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            rows.addLayout(form)
            return form

        models = self._models_form = section("Models")
        # Auto: whoever starts an agent names its model, from the ones allowed; only a
        # secretary's, whom nobody starts, is set here. Manual: a model for each rank.
        self.mode = QComboBox()
        self.mode.addItem("Auto", AUTO)
        self.mode.addItem("Manual", MANUAL)
        self.mode.setItemData(0, "Whoever starts an agent chooses its model, from the "
                                 "ones you allow", Qt.ItemDataRole.ToolTipRole)
        self.mode.setItemData(1, "You set the model of each rank", Qt.ItemDataRole.ToolTipRole)
        self.mode.setCurrentIndex(self.mode.findData(config.model_mode))
        models.addRow("Mode", self.mode)
        self.auto = QToolButton(objectName="folderPicker")
        self.auto.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.auto.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.auto.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.auto.setMenu(QMenu(self.auto))
        self.auto.setToolTip("The models there are to choose from")
        self.auto_models = list(config.auto_models)
        models.addRow("Allowed", self.auto)
        self._show_auto()
        # "default" is the model of every rank that has none of its own (FleetConfig.models).
        self.models: dict[str, ModelBox] = {}
        for key in ("secretary", "default",
                    *(f"K{rank}" for rank in range(config.top_rank, 0, -1))):
            field = self.models[key] = ModelBox(config.models.get(key, ""))
            models.addRow({"default": "Default", "secretary": "Secretary"}.get(key, key), field)
        self.models["secretary"].setToolTip("The model a project's secretary runs on")
        every = self.models["default"]
        every.lineEdit().setPlaceholderText("Kaden's model")
        every.setToolTip("The model of every rank that has none of its own; empty: the one "
                         "the agent program is set to")
        every.currentTextChanged.connect(lambda _: self._show_default())
        self.mode.currentIndexChanged.connect(lambda _: self._show_mode())
        self._show_default()
        self._show_mode()
        limits = section("Limits")
        self.budget = Amount(config.budget_usd, money=True)
        self.each = Amount(config.unit_budget_usd, money=True)
        self.agents = Amount(config.max_units)
        limits.addRow("Per fleet", self.budget)
        limits.addRow("Per agent", self.each)
        limits.addRow("Agents", self.agents)
        self.budget.setToolTip("The most a new fleet may spend")
        self.each.setToolTip("The most one agent may spend")
        self.agents.setToolTip("The most agents in a fleet")
        self.permissions = QComboBox()  # what agents do without asking: Kaden's levels
        for level, words in PERMISSION_SHORT.items():
            self.permissions.addItem(words, level.value)
            self.permissions.setItemData(self.permissions.count() - 1,
                                         PERMISSION_LABELS[level], Qt.ItemDataRole.ToolTipRole)
        self.permissions.setCurrentIndex(max(0, self.permissions.findData(config.permissions)))
        self.permissions.setToolTip("What agents do without asking, in fleets made from now on")
        section("Permission").addRow("Level", self.permissions)
        self.projects = QLineEdit(config.projects_folder)
        self.projects.setPlaceholderText("None")
        self.projects.setToolTip("A new project gets a folder named after it in here; "
                                 "empty: a folder is chosen for each project")
        self.browse = QToolButton(objectName="folderPicker")
        self.browse.setIcon(theme.icon("folder", colors.muted, 16))
        self.browse.setToolTip("Choose a folder")
        self.browse.clicked.connect(self._choose_projects)
        chosen = QHBoxLayout()
        chosen.setSpacing(6)
        chosen.addWidget(self.projects, 1)
        chosen.addWidget(self.browse)
        section("Projects").addRow("Folder", chosen)
        for form in self.findChildren(QFormLayout):  # one column of names, of one width
            for row in range(form.rowCount()):
                form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget().setFixedWidth(76)
        self.warning = label("", role="small", tone="error")
        self.warning.hide()
        row, self.save = foot(self, "Save", self.save_asked)
        rows.addSpacing(8)
        rows.addWidget(self.warning)
        rows.addLayout(row)

    def _choose_projects(self) -> None:
        start = Path(self.projects.text().strip() or Path.home()).expanduser()
        picker = self.picker = QFileDialog(self, "The folder new projects get theirs in",
                                           str(start))
        picker.setFileMode(QFileDialog.FileMode.Directory)
        picker.fileSelected.connect(self.projects.setText)
        picker.open()

    def changes(self) -> dict[str, Any]:
        return {"budget_usd": self.budget.value(), "unit_budget_usd": self.each.value(),
                "max_units": self.agents.value(),
                "projects_folder": self.projects.text().strip(),
                "permissions": self.permissions.currentData(),
                "auto_models": list(self.auto_models),
                "model_mode": self.mode.currentData(),
                "models": {rank: field.currentText().strip()
                           for rank, field in self.models.items()}}

    def _show_default(self) -> None:
        """A rank without a model of its own says which one it then runs on."""
        default = self.models["default"].currentText().strip()
        for key, field in self.models.items():
            if key not in ("default", "secretary"):
                field.lineEdit().setPlaceholderText(f"Default: {default}" if default
                                                    else "Default")
                field.setToolTip(f"The model {key} agents run on; empty: the default"
                                 + (f", {default}" if default else ""))

    def _show_auto(self) -> None:
        self.auto.setText(", ".join(self.auto_models) or "None")

    def _toggle_auto(self, model_id: str, on: bool) -> None:
        if on and model_id not in self.auto_models:
            self.auto_models.append(model_id)
        elif not on and model_id in self.auto_models:
            self.auto_models.remove(model_id)
        self._show_auto()

    def _show_mode(self) -> None:
        """Auto shows the models allowed and the secretary's; manual, a model for the
        secretary, the default and each rank."""
        auto = self.mode.currentData() == AUTO
        form = self._models_form
        form.setRowVisible(self.auto, auto)
        for key, field in self.models.items():
            form.setRowVisible(field, key == "secretary" or not auto)
        top = next(key for key in self.models if key.startswith("K"))
        self.models["secretary"].lineEdit().setPlaceholderText(
            "Kaden's model" if auto else f"As {top}")
        self.adjustSize()

    def offer(self, models: Sequence[tuple[str, str, str]]) -> None:
        """The models there are to choose from, each by its id with its name and price
        beside it in a tooltip; what a field holds stays, listed or not."""
        for field in self.models.values():
            field.offer(models)
        menu = self.auto.menu()
        menu.clear()
        for model_id, name, about in models:
            action = menu.addAction(model_id)
            action.setCheckable(True)
            action.setChecked(model_id in self.auto_models)
            action.setToolTip(", ".join(part for part in (name, about) if part))
            action.toggled.connect(
                lambda on, model_id=model_id: self._toggle_auto(model_id, on))
        menu.setToolTipsVisible(True)


class TaskDialog(QDialog):
    """An agent's task, and whether it is a task or continuous, to change."""

    def __init__(self, parent: QWidget, unit: Unit) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{unit.called}'s task")
        self.setMinimumWidth(460)
        rows = QVBoxLayout(self)
        rows.setSpacing(10)
        self.task = TaskBox()
        self.task.setPlainText(unit.task)
        self.task.submitted.connect(self.accept)
        self.kind = KindSwitch()
        (self.kind.duty if unit.ongoing else self.kind.task_kind).setChecked(True)
        self.warning = label("", role="small", tone="error")
        self.warning.hide()
        row, self.save = foot(self, "Save", self.accept, before=self.kind)
        rows.addWidget(self.task)
        rows.addWidget(self.warning)
        rows.addLayout(row)


class LineDialog(QDialog):
    """One line to change: a name."""

    def __init__(self, parent: QWidget, title: str, text: str, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        rows = QVBoxLayout(self)
        self.line = QLineEdit(text)
        self.line.setPlaceholderText(placeholder)
        self.line.returnPressed.connect(self.accept)
        row, _ = foot(self, "Save", self.accept)
        rows.addWidget(self.line)
        rows.addLayout(row)


class ModelDialog(QDialog):
    """The model an agent runs on; empty: its rank's."""

    def __init__(self, parent: QWidget, unit: Unit) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{unit.called}'s model")
        self.setMinimumWidth(380)
        rows = QVBoxLayout(self)
        self.model = ModelBox(unit.model or "", f"K{unit.rank}'s, if empty")
        self.model.lineEdit().returnPressed.connect(self.accept)
        row, _ = foot(self, "Save", self.accept)
        rows.addWidget(self.model)
        rows.addLayout(row)


def _dropped_files(source: QMimeData) -> list[str]:
    """The files among what was dropped."""
    return [url.toLocalFile() for url in source.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()]


# ── A fleet ───────────────────────────────────────────────────────────────


class FleetView(QWidget):
    """One fleet of the service, known by its id there: its feed and the box for
    messages to its lead. `changed` after anything that alters how it stands."""

    changed = Signal()

    def __init__(self, service: Service, fleet_id: str) -> None:
        super().__init__()
        self.service, self.id = service, fleet_id
        self.secretary = fleet_id.endswith(f"/{SECRETARY}")  # a project's, not a team
        self.entries: list[QWidget] = []
        self.lives: dict[str, LiveEntry] = {}  # what each agent is doing now, by its id
        self.shown: str | None = None  # the agent whose entries are shown; None: all
        self._upto: int | None = None  # the number of the last event shown; None: none yet
        self._held: list[tuple[int, FleetEvent]] = []  # what happens until its history is shown
        self._load: asyncio.Future[None] | None = None
        self._approvals: dict[str, ApprovalCard] = {}
        # Agents a supervisor dismissed, by id, as they were called: what they said and
        # reported stays in the feed.
        self._dismissed: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        # The agent on the page as its own Kaden conversation, where it has one to
        # show; else the page is the feed, of that agent or of all.
        self.conversation: AgentPage | None = None
        self._attaching: AgentPage | None = None  # being opened: it hears already
        self._opening: asyncio.Task[None] | None = None
        self._refused: tuple[Any, ...] | None = None  # how things stood when none opened

        # The feed is one column in the middle, as wide as text reads well.
        page = QWidget(objectName="transcript")
        across = QHBoxLayout(page)
        across.setContentsMargins(0, 0, 0, 0)
        self._column = QWidget()
        self._column.setMaximumWidth(COLUMN)
        self._feed = QVBoxLayout(self._column)
        self._feed.setContentsMargins(4, 16, 4, 12)
        self._feed.setSpacing(10)
        self._feed.addStretch(1)
        across.addStretch(1)
        across.addWidget(self._column, 100)
        across.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(page)
        # The feed stays at its end as it grows, for whoever is reading the end; it
        # is put there when its history is first shown and when what it shows changes.
        self._at_end = True
        bar = self.scroll.verticalScrollBar()
        bar.valueChanged.connect(lambda v: setattr(self, "_at_end", v >= bar.maximum() - 4))
        bar.rangeChanged.connect(lambda _lo, hi: self._at_end and bar.setValue(hi))

        self.input = MessageBox()
        self.input.setPlaceholderText(
            "Message the secretary" if self.secretary else "Message the lead")
        self.input.submitted.connect(self._send_clicked)
        self.send_button = QToolButton(objectName="send")
        self.send_button.setProperty("active", True)
        self.send_button.setIcon(theme.icon("send", theme.current().on_accent, 18))
        self.send_button.setFixedSize(34, 34)
        self.send_button.setToolTip("Send")
        self.send_button.setAccessibleName("Send to the lead")
        self.send_button.clicked.connect(self._send_clicked)
        # Kaden's message box: what is attached, then add files, the text and send.
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
        stacked.setContentsMargins(10, 12, 10, 12)  # Claude's room around the text
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
        self.pages = QStackedWidget()  # the feed, or an agent's conversation
        self.pages.addWidget(self.scroll)
        self._prompts = QVBoxLayout()  # what that agent waits for an answer to
        self._prompts.setContentsMargins(0, 0, 0, 0)
        asked = QHBoxLayout()
        asked.setContentsMargins(0, 0, 0, 0)
        asked.addStretch(1)
        held = QWidget()
        held.setMaximumWidth(COLUMN)
        held.setLayout(self._prompts)
        asked.addWidget(held, 100)
        asked.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 14)
        layout.addWidget(self.pages, 1)
        layout.addLayout(asked)
        layout.addLayout(composer)
        colors = theme.current()
        page.setStyleSheet(
            f"QFrame#delivery {{ background: {colors.card}; border: 1px solid transparent; "
            f"border-radius: 12px; }}"
            f"QFrame#delivery[you=\"true\"] {{ border-color: {colors.accent}; }}")
        self._enable()

    @property
    def fleet(self) -> Fleet | None:
        """Its record, as the service keeps it current."""
        return self.service.fleet(self.id)

    @property
    def running(self) -> bool:
        return self.service.state(self.id).running

    @property
    def stopping(self) -> bool:
        return self.service.state(self.id).stopping

    @property
    def name(self) -> str:
        return self.fleet.name if self.fleet is not None else ""

    @property
    def start_word(self) -> str:
        """What its button says while it can be started: "Start", "Resume"; else ""."""
        how = self.service.state(self.id)
        return "" if not how.can_start else "Resume" if how.has_run else "Start"

    @property
    def status(self) -> str:
        """How it stands and what it has spent of its limit."""
        fleet = self.fleet
        if fleet is None:
            return ""
        how = self.service.state(self.id)
        return f"{how.status} · ${fleet.spent_usd:.2f} of ${how.budget_usd:.2f}"

    def load(self) -> asyncio.Future[None]:
        """Watch the fleet and show what happened in it so far: once, however often it is
        asked for. A refusal of the service when the fleet cannot be read."""
        if self._load is None:
            self._load = asyncio.ensure_future(self._show_history())
        return self._load

    @property
    def loaded(self) -> bool:
        """Whether its history is shown."""
        return self._upto is not None

    async def _show_history(self) -> None:
        await self.service.watch(self.id)
        history, upto = await self.service.history(self.id)
        for event in history:
            self._show(event)
        self._upto = upto
        held, self._held = self._held, []
        for number, event in held:  # what happened meanwhile, if the history lacks it
            self.happened(event, number)
        self._enable()
        self.to_end()

    def to_end(self) -> None:
        """Show the latest of the feed, and stay with it as it grows."""
        self._at_end = True
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    async def resume(self, budget_usd: float | None = None) -> None:
        """Start it again where it was; `budget_usd` is a new limit for it."""
        if self.running or self.fleet is None:
            return
        try:
            await self.service.start(self.id, budget_usd=budget_usd)
        except REFUSALS as e:  # it is out of money, or its record cannot be read
            self.say(str(e), tone="error")

    async def add_agent(self, supervisor: Unit | None, name: str, task: str, *,
                        ongoing: bool, model: str | None) -> None:
        """An agent under `supervisor`, or the lead of a fleet with none. FleetError or
        UnitError when it cannot be."""
        await self.service.add_agent(self.id, supervisor.id if supervisor else None, name,
                                     task, ongoing=ongoing, model=model)

    async def retask(self, unit: Unit, task: str, *, ongoing: bool | None = None) -> None:
        """Another task for the agent, as a duty or not. FleetError or UnitError when it
        cannot be."""
        await self.service.set_task(self.id, unit.id, task, ongoing=ongoing)

    async def remove(self, unit: Unit) -> None:
        """Take the agent and everything under it out of the fleet."""
        await self.service.remove_agent(self.id, unit.id)

    async def rename(self, name: str, unit: Unit | None = None) -> None:
        """Another name for the agent, or for the fleet."""
        if unit is not None:
            await self.service.rename_agent(self.id, unit.id, name)
        else:
            await self.service.rename_fleet(self.id, name)

    async def set_model(self, unit: Unit, model: str) -> None:
        await self.service.set_model(self.id, unit.id, model)

    async def stop(self) -> None:
        """Stop the fleet's processes; it can be resumed."""
        await self.service.stop(self.id)

    def updated(self) -> None:
        """Its record or state changed in the service."""
        here = set(self.fleet.units) if self.fleet is not None else set()
        if self.shown is not None and self.shown not in here:
            self.choose(None)
        here |= self._dismissed.keys()
        for entry in [e for e in self.entries if not e.concerns <= here]:
            self.entries.remove(entry)  # what was about an agent taken out goes with it
            entry.setParent(None)
            entry.deleteLater()
        self._enable()
        self._converse()
        self.changed.emit()

    # ── An agent's own conversation ──────────────────────────────────────

    @property
    def _conversing(self) -> str | None:
        """The agent whose conversation the page is to show: the one chosen, or a
        secretary, who is the only agent of her fleet."""
        if self.shown is not None or not self.secretary or self.fleet is None:
            return self.shown
        return self.fleet.lead.id if self.fleet.lead is not None else None

    def _converse(self) -> None:
        """Have the page show that agent's Kaden conversation, if it has one to show
        now; it is looked at again whenever the fleet changes."""
        wanted = self._conversing
        if self._opening is not None or (
                self.conversation is not None and self.conversation.unit == wanted):
            return
        unit = self.fleet.units.get(wanted or "") if self.fleet is not None else None
        stands = (wanted, self.running, unit.session if unit else None)
        if self.conversation is None and (wanted is None or stands == self._refused):
            return
        self._opening = asyncio.ensure_future(self._open(wanted, stands))
        self._tasks.add(self._opening)
        self._opening.add_done_callback(self._tasks.discard)

    async def _open(self, wanted: str | None, stands: tuple[Any, ...]) -> None:
        try:
            await self._leave()
            if wanted is not None:
                page = self._attaching = AgentPage(
                    self.service, self.id, wanted,
                    tell=lambda text, files: self._run(self._tell(text, files)),
                    approve=lambda request_id, allow, always: self._run(
                        self._decide(wanted, request_id, allow, always)),
                    chose=self.input.setFocus, ended=self._gone)
                if await page.begin():
                    self.conversation, self._refused = page, None
                    # The message box has the focus, as in Kaden. Left in the page
                    # that goes, the stack moves it to the first thing in the
                    # conversation that takes it, and the view scrolls up to that.
                    self.input.setFocus()
                    self.pages.addWidget(page.view)
                    self.pages.setCurrentWidget(page.view)
                    self._prompts.addWidget(page.prompts)
                    page.prompts.show()
                    QTimer.singleShot(0, page.view.to_end)
                else:
                    self._refused = stands
                    await page.close()
        finally:
            self._attaching = self._opening = None
        self.changed.emit()
        self._converse()  # what is wanted may have changed meanwhile

    async def _leave(self) -> None:
        """Back to the feed."""
        page, self.conversation = self.conversation, None
        if page is not None:
            self.pages.setCurrentWidget(self.scroll)
            await page.close()

    def _gone(self) -> None:
        """The agent's process ended (it was stopped, or started again on other
        settings): its conversation is opened anew."""
        async def again() -> None:
            await self._leave()
            self._converse()

        self._run(again())

    def within(self, unit_id: str, event: dict[str, Any] | None) -> None:
        """Something happened in an agent's own conversation (service.Within)."""
        for page in (self.conversation, self._attaching):
            if page is not None and page.unit == unit_id:
                page.channel.within(event)

    async def _decide(self, unit_id: str, request_id: str, allow: bool, always: bool) -> None:
        try:
            await self.service.approve(self.id, unit_id, request_id, allow, always=always)
        except REFUSALS as e:
            self.say(str(e), tone="error")

    async def leave(self) -> None:
        """The page is going: let go of the agent it follows."""
        if self._opening is not None:
            self._opening.cancel()
            await asyncio.gather(self._opening, return_exceptions=True)
        await self._leave()

    def choose(self, unit_id: str | None) -> None:
        """Show only what concerns the agent; None: everything."""
        self.shown = unit_id
        self._converse()
        for entry in (*self.entries, *self.lives.values()):
            self._fit(entry)
        self.to_end()
        to = self.chosen
        self.input.setPlaceholderText(
            f"Message {to.called}" if to is not None and to.supervisor is not None
            else "Message the secretary" if self.secretary else "Message the lead")
        self.changed.emit()

    @property
    def chosen(self) -> Unit | None:
        """The agent whose conversation is shown, which is the one a message goes to."""
        return self.fleet.units.get(self.shown or "") if self.fleet is not None else None

    def _fit(self, entry: QWidget) -> None:
        """Show the entry if it concerns the agent chosen; in an agent's own conversation
        what it wrote needs no name over it."""
        entry.setVisible(self._concerns(entry) and not getattr(entry, "empty", False))
        if isinstance(entry, SaidEntry | LiveEntry):
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
        chosen = self.chosen
        try:  # a fleet that is not running is started for it
            await self.service.say(self.id, text, chosen.id if chosen else None, files=files)
        except REFUSALS as e:
            self.say(str(e), tone="error")
            if not self.running:  # it could not start: what was written is not lost
                self.input.setPlainText(text)
                self.attachments.add(files)

    async def _answer(self, event: Approval, allow: bool, always: bool = False) -> None:
        try:
            await self.service.approve(self.id, event.unit, event.request_id, allow,
                                       always=always)
        except REFUSALS as e:
            self.say(str(e), tone="error")

    def happened(self, event: FleetEvent, number: int) -> None:
        """Something happened in the fleet: its event of that number."""
        if self._upto is None:
            self._held.append((number, event))
        elif number > self._upto:
            self._upto = number
            self._show(event)

    def _called(self, unit_id: str) -> str:
        """What people read for the unit; its id for one the record does not have."""
        unit = self.fleet.units.get(unit_id) if self.fleet is not None else None
        return unit.called if unit is not None else self._dismissed.get(unit_id, unit_id)

    def _show(self, event: FleetEvent) -> None:
        match event:
            case Changed(unit=unit, state=state):
                # A report or a question says the rest; a line only for what nothing else
                # shows: it failed, was stopped, or ended without a report.
                if state not in QUIET_STATES:
                    self._add(Line(f"{self._called(unit)} · {state}", {unit}, tone="warning"))
            case Said(unit=unit, text=text):
                known = self.fleet.units.get(unit) if self.fleet is not None else None
                self._add(SaidEntry(unit, known, text))
            case Delivered():
                self._add(DeliveryCard(event, self._called))
            case Across():
                self._add(AcrossCard(event, self._called))
            case Instructed(unit=unit, by=by, text=text):
                # The person's own is what they said; a supervisor's is a card.
                self._add(YouEntry(text, unit) if by is None
                          else InstructionCard(event, self._called))
            case Approval():
                card = ApprovalCard(event, self._called(event.unit))
                card.allow.clicked.connect(lambda: self._run(self._answer(event, True)))
                card.always.clicked.connect(
                    lambda: self._run(self._answer(event, True, always=True)))
                card.deny.clicked.connect(lambda: self._run(self._answer(event, False)))
                self._approvals[event.request_id] = card
                self._add(card)
            case Answered(request_id=request_id, allow=allow, always=always):
                if (card := self._approvals.pop(request_id, None)) is not None:
                    card.answered(allow, always)
            case Told(text=text, to=to, files=files):
                self._add(YouEntry(text, to, files))
            case Notice(text=text):
                self.say(text, tone="warning")
            case Dismissed(unit=unit, by=by, gone=gone):
                self._dismissed.update(gone)
                under = len(gone) - 1
                self._add(Line(f"{self._called(by)} dismissed {self._called(unit)}"
                               + (f" and the {under} under it" if under else ""), {by, unit}))

    def _add(self, entry: QWidget) -> None:
        entry.setMaximumWidth(COLUMN)
        self.entries.append(entry)
        # Above what agents are doing now, which stands last.
        self._feed.insertWidget(self._feed.count() - 1 - len(self.lives), entry)
        self._fit(entry)

    def live(self, unit_id: str, thinking: str, writing: str, doing: str) -> None:
        """What the agent is doing as it does it, under the feed."""
        entry = self.lives.get(unit_id)
        if entry is None:
            if not (thinking or writing or doing):
                return
            known = self.fleet.units.get(unit_id) if self.fleet is not None else None
            entry = self.lives[unit_id] = LiveEntry(unit_id, known)
            entry.setMaximumWidth(COLUMN)
            self._feed.insertWidget(self._feed.count() - 1, entry)
        entry.show_live(thinking, writing, doing)
        self._fit(entry)

    def _concerns(self, entry: QWidget) -> bool:
        concerns: set[str] = entry.concerns  # type: ignore[attr-defined]
        return self.shown is None or not concerns or self.shown in concerns

    def _run(self, work: Coroutine[Any, Any, None]) -> None:
        """Do it in the background; what the service refuses is said in the feed."""
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._ran)

    def _ran(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled() or (error := task.exception()) is None:
            return
        if isinstance(error, REFUSALS):
            self.say(str(error), tone="error")
        else:
            logger.error("work in the background failed", exc_info=error)


# ── The window ────────────────────────────────────────────────────────────


class FleetWindow(QMainWindow):
    """A frontend of `service`: everything it shows and does goes through it."""

    def __init__(self, service: Service, notifier: Notifier | None = None,
                 look: Path | None = None, *, tray: bool = False) -> None:
        super().__init__()
        self.service = service
        # With a system tray, closing the window hides it and KFM runs on; quit() quits.
        self.tray = Tray(self, self.bring_forward, self.quit) if tray else None
        self.told_of_tray = False  # the first close in a run says KFM runs on
        self._quitting = False
        self.look = look  # where the theme chosen is kept; None: nowhere
        self.service.subscribe(self._update)
        self.notifier = notifier  # None: nothing is said outside the window
        self._noticed: set[str] = set()  # what waits and was seen or notified
        self._listed: dict[str, ProjectInfo] = {}  # by project id, as last listed
        self.project: ProjectInfo | None = None  # the one shown
        self.views: dict[str, FleetView] = {}  # every fleet read so far, by its id
        self.view: FleetView | None = None  # the fleet opened; None: the chart
        self.adding = False  # the form for a new fleet is shown
        self.dialog: QDialog | None = None  # the one open about an agent
        self.drafts: dict[str, DraftPage] = {}  # by project id, until started
        # What each agent is doing now, as the service last said it: (fleet, unit).
        self._doing: dict[tuple[str, str], tuple[str, str, str]] = {}
        # The fleets an update of KFM stopped, until they are resumed or it is dismissed.
        self._interrupted = list(service.interrupted)
        self.closed = asyncio.Event()
        # The fleet the agents list shows, and who is under whom in it.
        self._agents_shape: tuple[str, tuple[tuple[str, str | None], ...]] = ("", ())
        self._tasks: set[asyncio.Task[None]] = set()
        self.setWindowTitle("KFM")
        # No title bar: the bar on top is where the window is grabbed, and has its controls.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._edges = WindowEdges(self)
        self._build()
        self._corners = RoundedCorners(self)
        self._refresh()
        self._run(self._offer_app_menu())
        self._run(self._open_newest())
        self._run(self._prepare_project())
        self._run(self._show_settings())

    def _build(self) -> None:
        self.agents = QTreeWidget()
        self.agents.setColumnCount(3)
        self.agents.setHeaderHidden(True)
        self.agents.setRootIsDecorated(False)
        self.agents.setIndentation(16)
        self.agents.header().setStretchLastSection(False)  # the name has the room
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
        # The menu at the top, as in Kaden: what is about KFM as it runs.
        self.menu_button = QToolButton(objectName="menu")
        self.menu_button.setIcon(theme.icon("menu", theme.current().muted, 18))
        self.menu_button.setIconSize(QSize(18, 18))
        self.menu_button.setToolTip("Menu")
        self.menu_button.setAccessibleName("Menu")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        main_menu = QMenu(self)
        themes = main_menu.addMenu("Theme")
        for name in theme.THEMES:
            action = themes.addAction(name.capitalize())
            action.setCheckable(True)
            action.setChecked(name == theme.choice())
            action.triggered.connect(lambda _=False, name=name: self._run(self.set_theme(name)))
        main_menu.addSeparator()
        main_menu.addAction("Restart KFM").triggered.connect(lambda: self.ask_restart())
        main_menu.addAction("Stop KFM").triggered.connect(lambda: self.ask_stop())
        self.menu_button.setMenu(main_menu)
        side_layout.addWidget(self.menu_button, 0, Qt.AlignmentFlag.AlignLeft)
        side_layout.addSpacing(6)
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
        # The corner at the bottom, as in Kaden: who you are, with the app's menu, and
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
        menu.addAction("Quit").triggered.connect(self.quit)
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
        self.secretary_button = QPushButton("Secretary")
        self.secretary_button.setToolTip("Ask about the project, or have it changed")
        self.secretary_button.clicked.connect(self.open_secretary)
        self.limit = Budget()  # a stopped fleet's limit, to raise before it carries on
        self._limit_of: tuple[str, float] | None = None  # the fleet and limit it shows
        self.bar = DragArea()
        self.bar.setFixedHeight(BAR)
        self.bar.setStyleSheet(BOX_STYLE.format(line=theme.current().line))
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(20, 0, 0, 0)  # the controls reach the window's corner
        bar.setSpacing(4)
        bar.addWidget(self.back_button)
        bar.addWidget(self.title, 1)
        self._bar_gap = QWidget()  # keeps the controls at the right while nothing is named
        bar.addWidget(self._bar_gap, 1)
        bar.addWidget(self.status)
        bar.addWidget(self.secretary_button)
        bar.addSpacing(8)
        bar.addWidget(self.stop_button)
        bar.addWidget(self.limit)
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
        self.chart = Chart()
        self.chart.opened.connect(self._chart_clicked)
        self.chart.add_button.clicked.connect(self.add_fleet)
        self.chart.add_asked.connect(self._add_agent)
        self.chart.model_asked.connect(self._choose_model)
        self.chart.start_asked.connect(self._start_asked)
        self.chart.delete_asked.connect(self._ask_delete_fleet)
        self.chart.rename_asked.connect(self._ask_rename)
        self.chart.task_asked.connect(self._ask_task)
        self.chart.remove_asked.connect(self._ask_remove)
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
        self.attention = Attention()
        right.addWidget(self.bar)
        right.addWidget(self.attention)
        right.addWidget(self.pages, 1)
        layout.addWidget(side)
        layout.addLayout(right, 1)
        self.setCentralWidget(whole)

    # ── What the person does ─────────────────────────────────────────────

    def project_views(self) -> list[FleetView]:
        """The open project's fleets, oldest first."""
        if self.project is None:
            return []
        mine = f"{self.project.id}/"
        return [view for fleet_id, view in sorted(self.views.items())
                if fleet_id.startswith(mine) and not view.secretary]

    async def create_project(self, name: str, work: Path, description: str = "") -> None:
        """A new project; with a description, its secretary proposes its teams."""
        try:
            made = await self.service.create_project(name, str(work), description)
        except REFUSALS as e:
            self.project_form.warn(f"The project could not be made: {e}")
            return
        self.project_form.name.clear()
        self.project_form.does.clear()
        self.project_form.set_folder(None)
        if description:
            await self._draft_page(made)
        await self.open_project(made)

    async def _draft_page(self, opened: ProjectInfo) -> DraftPage:
        """The page for what the project's secretary proposes, waiting for it."""
        if (page := self.drafts.get(opened.id)) is None:
            page = self.drafts[opened.id] = DraftPage(opened)
            with contextlib.suppress(*REFUSALS):
                page.config = await self.service.settings()
                page.budget.setValue(page.config.budget_usd)
            page.start_asked.connect(lambda: self._run(self.start_draft(page)))
            page.over.connect(lambda: self._drop_draft(page))
            self.draft_pages.addWidget(page)
        return page

    def _waited(self, project_id: str) -> None:
        """A page that waits for a proposal stops when the secretary ended without one."""
        page = self.drafts.get(project_id)
        view = self.views.get(f"{project_id}/{SECRETARY}")
        lead = view.fleet.lead if view is not None and view.fleet is not None else None
        if page is not None and page.busy and lead is not None and lead.state in OVER:
            page.failed()

    async def _proposed(self, project_id: str) -> None:
        """The project's secretary put teams to the person."""
        try:
            made = await self.service.proposal(project_id)
            listed = {each.id: each for each in await self.service.projects()}
        except REFUSALS:
            return
        if made is None or project_id not in listed:
            return
        (await self._draft_page(listed[project_id])).fill(made)
        if self.project is not None and self.project.id == project_id:
            self.view, self.adding = None, False
        self._refresh()

    async def start_draft(self, page: DraftPage) -> None:
        """Start every fleet of the draft, and show the project's chart."""
        fleets = [(row.name.text().strip(), row.does.toPlainText().strip(),
                   row.kind.duty.isChecked(), row.folder, row.lead_model.model())
                  for row in page.rows]
        names = [fleet[0].lower() for fleet in fleets]
        if not fleets or not all(fleet[0] and fleet[1] for fleet in fleets):
            return page.warn("Each fleet needs a name and what it does.")
        if len(set(names)) < len(names):
            return page.warn("Two fleets have the same name.")
        page.start.setEnabled(False)
        await self._settle(page.project.id)  # before the teams are listed again
        for name, does, ongoing, folder, model in fleets:
            work = Path(page.project.work)
            await self.start_fleet(name, does, ongoing=ongoing,
                                   work=work / folder if folder else work,
                                   budget=page.budget.value(), opened=page.project,
                                   show=False, model=model)
        self._drop_draft(page)

    def _drop_draft(self, page: DraftPage) -> None:
        if self.drafts.pop(page.project.id, None) is page:
            if page.busy:  # she is still at it: cancelled
                self._run(self._stop_secretary(page.project))
            self._run(self._settle(page.project.id))
            self.draft_pages.removeWidget(page)
            page.deleteLater()
            if self.project is not None and self.project.id == page.project.id:
                self.adding = False
                self._prepare_start()
            self._refresh()

    async def _stop_secretary(self, opened: ProjectInfo) -> None:
        with contextlib.suppress(*REFUSALS):
            await self.service.stop(opened.secretary)

    async def _settle(self, project_id: str) -> None:
        with contextlib.suppress(*REFUSALS):
            await self.service.settle_proposal(project_id)

    async def open_project(self, opened: ProjectInfo) -> None:
        """Show the project's chart, which is empty until it has a fleet."""
        self.project, self.view = opened, None
        await self._sync(opened)
        if self.project is opened:
            self.adding = False
            self._prepare_start()
        await self.list_projects()

    async def _sync(self, opened: ProjectInfo) -> None:
        """Have a page for each of the project's fleets, and none for one that is gone."""
        try:
            found = await self.service.fleets(opened.id)
        except REFUSALS:
            found = []
        found.append(opened.secretary)  # a fleet of one agent, and not one of its teams
        for fleet_id in found:  # each can be read: the service lists no other
            await (self.views.get(fleet_id) or self._view(fleet_id)).load()
        mine = f"{opened.id}/"
        for fleet_id, view in list(self.views.items()):
            if fleet_id.startswith(mine) and fleet_id not in found and view.loaded:
                self._drop(view)
        waiting = self.drafts.get(opened.id)
        with contextlib.suppress(*REFUSALS):  # what she proposed while nobody looked
            if ((waiting is None or waiting.busy)  # not over what the person has changed
                    and await self.service.proposal(opened.id)):
                await self._proposed(opened.id)
        self._waited(opened.id)
        for (fleet_id, unit), now in self._doing.items():  # what was said meanwhile
            if fleet_id.startswith(mine) and (view := self.views.get(fleet_id)):
                view.live(unit, *now)
        page = self.drafts.get(opened.id)
        if page is not None and page.busy:
            for (fleet_id, _), now in self._doing.items():
                if fleet_id == opened.secretary:
                    page.live(*now)
        if page is not None and page.busy and not page.saying.toPlainText():
            with contextlib.suppress(*REFUSALS):  # what she wrote before this page was
                history, _ = await self.service.history(opened.secretary)
                if said := [event.text for event in history if isinstance(event, Said)]:
                    page.said(said[-1])

    def new_project(self) -> None:
        """The form for a project; fleets that run keep running."""
        self.project = self.view = None
        self.adding = False
        self._refresh()
        self.project_form.name.setFocus()
        self._run(self._prepare_project())

    async def _prepare_project(self) -> None:
        """The form knows the projects folder, if one is set."""
        with contextlib.suppress(*REFUSALS):
            config = await self.service.settings()
            base = config.projects_folder
            self.project_form.set_base(Path(base).expanduser() if base else None)
            self.start_form.lead_model.offer(config)

    def add_fleet(self) -> None:
        self.view, self.adding = None, True
        self._prepare_start()
        self._refresh()
        self.start_form.name.setFocus()

    def show_chart(self) -> None:
        self.view, self.adding = None, False
        self._refresh()

    def open_secretary(self) -> None:
        """The conversation with the open project's secretary."""
        if self.project is not None and (view := self.views.get(self.project.secretary)):
            self.open_fleet(view)

    def open_fleet(self, view: FleetView, unit_id: str | None = None) -> None:
        """The fleet's feed, for one agent or for all."""
        self.view, self.adding = view, False
        self.fleet_pages.setCurrentWidget(view)
        view.input.setFocus()
        view.to_end()
        if view.conversation is not None:  # one that was open before: at its latest
            QTimer.singleShot(0, view.conversation.view.to_end)
        self._refresh()
        self.agents.setCurrentItem(self._agent_row(unit_id))

    async def start_fleet(self, name: str, task: str, *, ongoing: bool, work: Path,
                          budget: float, opened: ProjectInfo | None = None,
                          show: bool = True, model: str | None = None) -> None:
        """A new fleet on the task, in `opened` or the open project; `show` opens it. With
        no task it has no agents and runs nothing: the person builds it on the chart."""
        opened = opened or self.project
        assert opened is not None
        try:
            fleet_id = await self.service.add_fleet(
                opened.id, name, task, ongoing=ongoing, work=str(work),
                budget_usd=budget, model=model if task else None)
            view = self.views.get(fleet_id) or self._view(fleet_id)
            await view.load()
        except REFUSALS as e:
            self.start_form.warn(f"The fleet could not start: {e}")
            return
        if show:
            self.start_form.name.clear()
            self.start_form.task.clear()
            if task:
                self.open_fleet(view)
            else:
                self.show_chart()
        await self.list_projects()

    async def resume(self) -> None:
        if self.view is not None:
            limit = self.limit.value()
            changed = limit != self.service.state(self.view.id).budget_usd
            await self.view.resume(limit if changed else None)

    async def stop(self) -> None:
        if self.view is not None:
            await self.view.stop()

    async def resume_interrupted(self) -> None:
        """Start the fleets again that an update of KFM stopped."""
        stopped, self._interrupted = self._interrupted, []
        self._refresh()
        for fleet_id in stopped:
            try:
                await self.service.fleets(fleet_id.partition("/")[0])  # so that it is read
                await self.service.start(fleet_id)
            except REFUSALS as e:
                self.dialog = QMessageBox(QMessageBox.Icon.Warning, "KFM", str(e), parent=self)
                self.dialog.show()

    def _forget_interrupted(self) -> None:
        self._interrupted = []
        self._refresh()

    def offer_restart(self) -> None:
        """Say that the service runs the code from before an update, if it does."""
        if self.service.stale:
            self.ask_restart("KFM was updated, and its service still runs the version "
                             "from before. Restart? Fleets that run stop and can be resumed.")

    def ask_restart(self, question: str = "Restart KFM? Fleets that run stop and can be "
                                          "resumed.") -> None:
        """End the service that holds the fleets and start the window again, on what is
        installed now: the service outlives the window, and so runs the code it was
        started on."""
        self._confirm("Restart KFM", question, "Restart", lambda: self._run(self.restart()))

    def _confirm(self, title: str, question: str, verb: str, then: Callable[[], object], *,
                 safe: bool = False) -> None:
        """Ask before doing it: a box with the question, `verb` and Cancel. `safe`: Enter
        cancels."""
        dialog = self.dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(question)
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes
                                  | QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Yes).setText(verb)
        if safe:
            dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.accepted.connect(then)
        dialog.open()

    async def restart(self) -> None:
        if await self._end():
            self.relaunch()

    async def set_theme(self, name: str) -> None:
        """Light, dark, or what the desktop is set to: kept, and the window started
        again in it. The service and its fleets are not touched."""
        if self.look is not None:
            await asyncio.to_thread(self.look.write_text, json.dumps({"theme": name}),
                                    encoding="utf-8")
        self.relaunch()

    def ask_stop(self) -> None:
        """End the service that holds the fleets and close the window: closing the window
        alone leaves them running."""
        self._confirm("Stop KFM", "Stop KFM? Fleets that run stop and can be resumed.",
                      "Stop", lambda: self._run(self.stop_kfm()))

    async def stop_kfm(self) -> None:
        if await self._end():
            await self.shut()

    async def _end(self) -> bool:
        """End the service. False, with the reason shown, when it could not be."""
        try:
            await self.service.end()
        except REFUSALS as e:
            self.dialog = QMessageBox(QMessageBox.Icon.Warning, "KFM", str(e), parent=self)
            self.dialog.open()
            return False
        return True

    @staticmethod
    def relaunch() -> None:
        """This program again, in place of this process."""
        os.execv(sys.executable, [sys.executable, "-m", "kfm.desktop"])

    async def shut(self) -> None:
        """Let go of the service and close the window. The service in this process stops
        its fleets; the one `kfm serve` runs keeps them running."""
        for page in list(self.drafts.values()):
            page.cancel()
        for view in list(self.views.values()):
            await view.leave()
        if self.notifier is not None:
            await self.notifier.aclose()
        with contextlib.suppress(*REFUSALS):
            await self.service.aclose()
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        self.closed.set()
        self.close()

    def _view(self, fleet_id: str) -> FleetView:
        view = self.views[fleet_id] = FleetView(self.service, fleet_id)
        view.changed.connect(self._refresh)
        self.fleet_pages.addWidget(view)
        return view

    def _drop(self, view: FleetView) -> None:
        if self.view is view:
            self.view = None
        del self.views[view.id]
        self.fleet_pages.removeWidget(view)

        async def gone() -> None:
            await view.leave()
            view.deleteLater()

        self._run(gone())

    def _update(self, update: Update) -> None:
        """What the service says happened."""
        match update:
            case Happened(fleet=fleet_id, event=event, number=number):
                if (view := self.views.get(fleet_id)) is not None:
                    view.happened(event, number)
                page = self.drafts.get(fleet_id.partition("/")[0])
                if (page is not None and page.busy and isinstance(event, Said)
                        and fleet_id.endswith(f"/{SECRETARY}")):
                    page.said(event.text)
            case Updated(fleet=fleet_id):
                if (view := self.views.get(fleet_id)) is not None:
                    view.updated()
                    if view.secretary:
                        self._waited(fleet_id.partition("/")[0])
            case Within(fleet=fleet_id, unit=unit, event=event):
                if (view := self.views.get(fleet_id)) is not None:
                    view.within(unit, event)
            case Live(fleet=fleet_id, unit=unit, thinking=thinking, writing=writing,
                      doing=doing):
                # Kept, for a page that is made after it was said.
                self._doing[fleet_id, unit] = (thinking, writing, doing)
                if (view := self.views.get(fleet_id)) is not None:
                    view.live(unit, thinking, writing, doing)
                page = self.drafts.get(fleet_id.partition("/")[0])
                if (page is not None and page.busy
                        and fleet_id.endswith(f"/{SECRETARY}")):
                    page.live(thinking, writing, doing)
            case Listed():
                self._run(self._relist())
            case Proposed(project=project_id):
                self._run(self._proposed(project_id))

    async def _relist(self) -> None:
        """A project or a fleet was added, renamed or removed, here or by another
        frontend."""
        await self.list_projects()
        opened = self.project
        if opened is None:
            return
        if (info := self._listed.get(opened.id)) is None:  # deleted elsewhere
            self.project = self.view = None
            self.adding = False
            return await self._open_newest()
        self.project = info
        await self._sync(info)
        self._refresh()

    def _prepare_start(self) -> None:
        if self.project is not None:
            self.start_form.set_folder(Path(self.project.work))
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
        if self.tray is not None and not self._quitting and not self.closed.is_set():
            event.ignore()  # KFM runs on in the tray, its fleets too
            self.hide()
            if not self.told_of_tray:
                self.tray.showMessage("KFM", "Still running. Quit from this icon.",
                                      QIcon(str(app_menu.ICON)), 4000)
                self.told_of_tray = True
            return
        if self.tray is not None:
            self.tray.hide()
        if not self.closed.is_set():
            self._run(self.shut())
        event.accept()

    def bring_forward(self) -> None:
        """Its tray icon asked for this window: shown as it was before it was hidden or
        minimized."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        """Close the window and let go of the service, tray or not."""
        self._quitting = True
        self.close()

    async def _open_newest(self) -> None:
        """The window opens on the project made last."""
        try:
            found = await self.service.projects()
        except REFUSALS:
            found = []
        if found and self.project is None and self.view is None:
            await self.open_project(found[0])
        else:
            await self.list_projects()

    async def list_projects(self) -> None:
        """Fill the sidebar's list of projects."""
        try:
            found = await self.service.projects()
            old = await self.service.unreadable()
        except REFUSALS:
            return
        self._listed = {info.id: info for info in found}
        self.projects.clear()
        for info in found:
            item = QListWidgetItem(info.name)
            preview = "1 fleet" if info.fleets == 1 else f"{info.fleets} fleets"
            if info.running:
                preview += f" · {info.running} running"
            if info.waiting:  # an approval, or a lead's question: an agent stands still
                preview += f" · {info.waiting} waiting for you"
            item.setData(PREVIEW_ROLE, preview)
            item.setData(DATE_ROLE, _when(info.id))
            item.setData(PROJECT_ROLE, info.id)
            item.setData(FLEETS_ROLE, info.fleets)
            item.setToolTip(info.work)
            self.projects.addItem(item)
        for project_id, name in old:  # there, and nothing but deleting can be done with it
            item = QListWidgetItem(name)
            item.setData(PREVIEW_ROLE, "Made by an older KFM")
            item.setData(DATE_ROLE, _when(project_id))
            item.setData(PROJECT_ROLE, project_id)
            item.setData(UNREADABLE_ROLE, True)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip("This version of KFM cannot open it. Right-click to delete it.")
            self.projects.addItem(item)
        self._refresh()

    def _project_menu(self, at: QPoint) -> None:
        if (item := self.projects.itemAt(at)) is None:
            return
        self.project_actions(item).popup(self.projects.viewport().mapToGlobal(at))

    def project_actions(self, item: QListWidgetItem) -> QMenu:
        """What a right-click on a listed project offers."""
        menu = QMenu(self)
        if not item.data(UNREADABLE_ROLE):
            menu.addAction("Rename…").triggered.connect(lambda: self.ask_rename(item))
        menu.addAction("Delete…").triggered.connect(lambda: self.ask_delete(item))
        return menu

    def ask_rename(self, item: QListWidgetItem) -> None:
        """Ask for the listed project's new name; an empty one changes nothing."""
        project_id = item.data(PROJECT_ROLE)
        dialog = self.dialog = LineDialog(self, "Rename project", item.text())

        async def rename() -> None:
            if not (name := dialog.line.text().strip()):
                return
            with contextlib.suppress(*REFUSALS):
                await self.service.rename_project(project_id, name)
            await self._relist()

        dialog.accepted.connect(lambda: self._run(rename()))
        dialog.open()

    def _ask_rename(self, key: str, unit_id: str) -> None:
        """Ask for a new name for the fleet's agent, or for the fleet ("")."""
        view = self.views.get(key)
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

    def _ask_task(self, key: str, unit_id: str) -> None:
        view = self.views.get(key)
        if view is None or view.fleet is None or unit_id not in view.fleet.units:
            return
        unit = view.fleet.units[unit_id]
        dialog = self.dialog = TaskDialog(self, unit)

        async def change() -> None:
            try:
                await view.retask(unit, dialog.task.toPlainText(),
                                  ongoing=dialog.kind.duty.isChecked())
            except REFUSALS as e:
                view.say(str(e), tone="error")

        dialog.accepted.connect(lambda: self._run(change()))
        dialog.open()

    def _ask_remove(self, key: str, unit_id: str) -> None:
        view = self.views.get(key)
        if view is None or view.fleet is None or unit_id not in view.fleet.units:
            return
        unit = view.fleet.units[unit_id]
        under = len(list(view.fleet.subtree(unit))) - 1
        what = unit.called + (f" and the {under} under it" if under else "")
        dialog = self.dialog = DeleteDialog(self, "Remove agent", what, "Remove")
        dialog.accepted.connect(lambda: self._run(view.remove(unit)))
        dialog.open()

    def ask_delete(self, item: QListWidgetItem) -> None:
        """Ask whether the listed project should go, and delete it on a yes."""
        project_id = item.data(PROJECT_ROLE)
        fleets = item.data(FLEETS_ROLE)
        has = ("what it kept" if item.data(UNREADABLE_ROLE)
               else "its 1 fleet" if fleets == 1 else f"its {fleets} fleets")
        dialog = self.dialog = DeleteDialog(self, "Delete project", f"{item.text()} and {has}")
        dialog.accepted.connect(lambda: self._run(self.delete_project(project_id)))
        dialog.open()

    def _ask_delete_fleet(self, key: str) -> None:
        if (view := self.views.get(key)) is None:
            return
        dialog = self.dialog = DeleteDialog(self, "Delete fleet", view.name)
        dialog.accepted.connect(lambda: self._run(self.delete_fleet(view)))
        dialog.open()

    async def delete_fleet(self, view: FleetView) -> None:
        """Stop the fleet and remove its record; the work folder stays."""
        with contextlib.suppress(*REFUSALS):
            await self.service.delete_fleet(view.id)
        if self.views.get(view.id) is view:  # unless what the service said took it already
            self._drop(view)
        await self.list_projects()

    async def delete_project(self, project_id: str) -> None:
        """Stop the project's fleets and remove its records; the work folder stays."""
        if (page := self.drafts.pop(project_id, None)) is not None:
            page.cancel()
            self.draft_pages.removeWidget(page)
            page.deleteLater()
        with contextlib.suppress(*REFUSALS):
            await self.service.delete_project(project_id)
        for fleet_id, view in list(self.views.items()):
            if fleet_id.startswith(f"{project_id}/"):
                self._drop(view)
        if self.project is not None and self.project.id == project_id:
            self.project, self.adding = None, False
            return await self._open_newest()
        await self.list_projects()

    def _project_chosen(self, item: QListWidgetItem) -> None:
        project_id = item.data(PROJECT_ROLE)
        if self.project is None or project_id != self.project.id:
            self._run(self._open_listed(project_id))

    async def _open_listed(self, project_id: str) -> None:
        if (info := self._listed.get(project_id)) is None:
            return await self.list_projects()
        await self.open_project(info)

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
        """What new fleets get."""
        self._run(self._open_settings())

    async def _open_settings(self) -> None:
        try:
            current = await self.service.settings()
        except REFUSALS:
            current = FleetConfig()  # settings that cannot be read are written anew
        dialog = self.dialog = SettingsDialog(self, current)

        async def save() -> None:
            try:
                made = await self.service.save_settings(**dialog.changes())
            except REFUSALS as e:
                return dialog.warn(str(e))
            dialog.accept()
            self.start_form.budget.setValue(made.budget_usd)
            await self._prepare_project()

        dialog.save_asked.connect(lambda: self._run(save()))
        dialog.open()
        with contextlib.suppress(*REFUSALS):  # asked while the dialog is already there
            offered = await self.service.models()
            if self.dialog is dialog:
                dialog.offer(offered)

    async def _show_settings(self) -> None:
        with contextlib.suppress(*REFUSALS):
            self.start_form.budget.setValue((await self.service.settings()).budget_usd)

    def _create_clicked(self) -> None:
        form = self.project_form
        name = form.name.text().strip()
        if not name:
            form.name.setFocus()
            return
        folder = form.folder
        if folder is None and (folder := form.own_folder()) is not None:
            if project.in_use(folder):  # not one made for this project: ask first
                self._confirm("New project",
                              f"{folder} already exists and has files in it. Agents can "
                              "change anything in it. Use it for this project?",
                              "Use it", lambda: self._create(name, folder), safe=True)
                return
            try:  # one of its own, in the projects folder
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                form.warn(f"{folder} could not be made: {e}")
                return
        self._create(name, folder)

    def _create(self, name: str, folder: Path | None) -> None:
        form = self.project_form
        work, problem = _work_folder(folder)
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
                                   budget=form.budget.value(),
                                   model=form.lead_model.model()))

    def _chart_clicked(self, key: str, unit_id: str) -> None:
        if (view := self.views.get(key)) is not None:
            self.open_fleet(view, unit_id or None)

    def _start_asked(self, key: str) -> None:
        if (view := self.views.get(key)) is None:
            return

        async def start() -> None:
            await view.resume()
            if not view.running:
                self.open_fleet(view)  # where it says why not

        self._run(start())

    def _add_agent(self, key: str, unit_id: str) -> None:
        """Ask for an agent to put under the unit ("": as the fleet's lead), and make it."""
        view = self.views.get(key)
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
            except REFUSALS as e:
                return dialog.warn(str(e))
            dialog.accept()
            self._refresh()

        dialog.add_asked.connect(lambda: self._run(add()))
        dialog.open()
        self._run(self._offer_models(dialog))

    def _choose_model(self, key: str, unit_id: str) -> None:
        view = self.views.get(key)
        if view is None or view.fleet is None:
            return
        unit = view.fleet.units[unit_id]
        dialog = self.dialog = ModelDialog(self, unit)
        dialog.accepted.connect(
            lambda: self._run(view.set_model(unit, dialog.model.text().strip())))
        dialog.open()
        self._run(self._offer_models(dialog))

    async def _offer_models(self, dialog: AgentDialog | ModelDialog) -> None:
        """List the models there are in the dialog's model field."""
        with contextlib.suppress(*REFUSALS):
            offered = await self.service.models()
            if self.dialog is dialog:
                dialog.model.offer(offered)

    def _agent_rows(self) -> list[QTreeWidgetItem]:
        """Every row of the agents list, top down. For use at once, not to keep."""
        rows: list[QTreeWidgetItem] = []
        walk = QTreeWidgetItemIterator(self.agents)
        while (row := walk.value()) is not None:
            rows.append(row)
            walk += 1
        return rows

    def _agent_row(self, unit_id: str | None) -> QTreeWidgetItem | None:
        return next((row for row in self._agent_rows()
                     if row.data(0, UNIT_ROLE) == unit_id), None)

    def _agent_chosen(self, item: QTreeWidgetItem | None, _: object = None) -> None:
        if self.view is not None:
            self.view.choose(item.data(0, UNIT_ROLE) if item is not None else None)
            self.show_all.setVisible(self.view.shown is not None)

    # ── What is shown ────────────────────────────────────────────────────

    def _show_attention(self) -> None:
        """Every approval and every lead's question that waits for the person, in the
        fleets read so far, of whatever project."""
        self._notify_waiting()
        rows: list[tuple[str, str, list[tuple[str, Callable[[], None]]]]] = []
        if self._interrupted:
            many = len(self._interrupted)
            said = f"KFM was updated · {many} fleet{'s' * (many != 1)} stopped"
            rows.append(("updated", said,
                         [("Resume", lambda: self._run(self.resume_interrupted())),
                          ("Dismiss", self._forget_interrupted)]))
        for view in self.views.values():
            fleet = view.fleet
            if fleet is None:
                continue
            for asked in self.service.state(view.id).approvals:
                if (view is self.view and view.conversation is not None
                        and view.conversation.unit == asked.unit):
                    continue  # asked on the page that is open, in the agent's own words
                unit = fleet.units.get(asked.unit)
                who = unit.called if unit is not None else asked.unit
                rows.append((
                    f"{view.id}/{asked.request_id}",
                    f"{view.name} › {who} wants to run {asked.tool} {asked.arguments}",
                    [(word, lambda view=view, asked=asked, allow=allow, always=always:
                      self._run(view._answer(asked, allow, always)))
                     for word, allow, always in (("Allow", True, False),
                                                 ("Always allow", True, True),
                                                 ("Deny", False, False))]))
            lead = fleet.lead
            if (lead is not None and lead.state == "waiting" and lead.question
                    and view.running and view is not self.view):
                rows.append((f"{view.id}?{lead.question}",
                             f"{view.name} › {lead.called} asks: {lead.question}",
                             [("Answer", lambda view=view: self.open_fleet(view))]))
        self.attention.show_rows(rows)

    def away(self) -> bool:
        """The person is not looking at this window."""
        return not self.isActiveWindow()

    def _notify_waiting(self) -> None:
        """A desktop notification for each approval and each lead's question that is new
        while the person is elsewhere; a click on it opens its fleet. What they saw
        waiting in the window is not news later."""
        waiting: dict[str, tuple[FleetView, str, str]] = {}
        for view in self.views.values():
            fleet = view.fleet
            if fleet is None:
                continue
            for asked in self.service.state(view.id).approvals:
                unit = fleet.units.get(asked.unit)
                who = unit.called if unit is not None else asked.unit
                waiting[f"{view.id}/{asked.request_id}"] = (
                    view, f"{view.name} › {who}",
                    f"wants to run {asked.tool} {asked.arguments}")
            lead = fleet.lead
            if lead is not None and lead.state == "waiting" and lead.question and view.running:
                waiting[f"{view.id}?{lead.question}"] = (
                    view, f"{view.name} › {lead.called}", f"asks: {lead.question}")
        if self.notifier is not None and self.away():
            for key in waiting.keys() - self._noticed:
                view, title, body = waiting[key]
                self.notifier.send(title, body, lambda view=view: self._come_to(view))
        self._noticed = set(waiting)

    def _come_to(self, view: FleetView) -> None:
        """Bring the window forward on the fleet, if it is still there."""
        if self.views.get(view.id) is not view:
            return
        self.bring_forward()
        self.open_fleet(view)

    def _refresh(self) -> None:
        """The bar, the page and the sidebar, from things as they are now."""
        self._show_attention()
        opened, view = self.project, self.view
        fleets = self.project_views()
        drafted = self.drafts.get(opened.id) if opened is not None else None
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
        self.secretary_button.setVisible(on_chart)
        self.stop_button.setVisible(view is not None and view.running)
        self.resume_button.setVisible(view is not None and bool(view.start_word))
        self.resume_button.setText(view.start_word if view is not None else "")
        self.limit.setVisible(self.resume_button.isVisibleTo(self))
        limit = (view.id, self.service.state(view.id).budget_usd) if view is not None else None
        if limit is not None and limit != self._limit_of:  # not over what is being typed
            self.limit.setValue(limit[1])
        self._limit_of = limit
        self.agents_section.setVisible(  # the secretary is one agent: nothing to list
            view is not None and (opened is None or view.id != opened.secretary))
        self.projects_label.setVisible(self.projects.count() > 0)
        for i in range(self.projects.count()):
            item = self.projects.item(i)
            if opened is not None and item.data(PROJECT_ROLE) == opened.id:
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
                self.chart.show_fleets([ChartFleet(shown.id, shown.name,
                                                   shown.status, shown.fleet,
                                                   running=shown.running,
                                                   editable=not shown.stopping,
                                                   start=shown.start_word)
                                        for shown in fleets])
            return
        chosen = view.chosen
        self.title.setText(f"{view.name} › {chosen.called}" if chosen else view.name)
        self.status.setText(f"{chosen.state} · ${chosen.spent_usd:.2f}" if chosen
                            else view.status)
        self._show_agents(view)

    def _show_agents(self, view: FleetView) -> None:
        """The open fleet's agents, each under its supervisor. No row is kept hold of from
        one call to the next: Qt deletes rows when the list is cleared, and a reference
        to a deleted row crashes the program. The list is built anew when the fleet or
        who is under whom changed, and its rows are found by the unit they stand for."""
        fleet = view.fleet
        units = list(fleet.units.values()) if fleet is not None else []
        shape = (view.id, tuple((unit.id, unit.supervisor) for unit in units))
        if shape != self._agents_shape:
            current = self.agents.currentItem()
            chosen = current.data(0, UNIT_ROLE) if current is not None else None
            del current
            self.agents.blockSignals(True)  # the choice is put back, not given up
            self.agents.clear()
            made: dict[str, QTreeWidgetItem] = {}
            for unit in units:
                parent = made.get(unit.supervisor or "")
                row = made[unit.id] = QTreeWidgetItem(
                    parent if parent is not None else self.agents)
                row.setData(0, UNIT_ROLE, unit.id)
                row.setExpanded(True)
            kept = made.get(chosen or "") if self._agents_shape[0] == view.id else None
            self.agents.setCurrentItem(kept)
            self.agents.blockSignals(False)
            self._agents_shape = shape
            if chosen is not None and kept is None:  # the one chosen is gone
                self._agent_chosen(None)
            del made, row, kept
        colors = theme.current()
        by_id = {unit.id: unit for unit in units}
        for row in self._agent_rows():
            unit = by_id[row.data(0, UNIT_ROLE)]
            row.setText(0, f"{unit.name}  {unit.id}" if unit.name else unit.id)
            row.setText(1, unit.state)
            row.setText(2, f"${unit.spent_usd:.2f}")
            row.setForeground(1, QColor(state_tone(unit.state, view.running)))
            row.setForeground(2, QColor(colors.muted))
            row.setToolTip(0, unit.task)
        # As tall as its agents, up to a part of the sidebar: the projects have the rest.
        rows = min(len(units), AGENT_ROWS)
        self.agents.setFixedHeight(max(1, rows) * AGENT_ROW_PX + 6)

    def _run(self, work: Coroutine[Any, Any, None]) -> None:
        """Do it in the background; what the service refuses is shown: in the dialog
        that is open, if it has a place for it, else in the open fleet's feed, else in a
        box of its own."""
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._ran)

    def _ran(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled() or (error := task.exception()) is None:
            return
        if not isinstance(error, REFUSALS):
            logger.error("work in the background failed", exc_info=error)
            return
        dialog = self.dialog
        if dialog is not None and dialog.isVisible() and hasattr(dialog, "warn"):
            dialog.warn(str(error))
        elif self.view is not None:
            self.view.say(str(error), tone="error")
        else:
            self.dialog = QMessageBox(QMessageBox.Icon.Warning, "KFM", str(error), parent=self)
            self.dialog.open()


def saved_theme(path: Path) -> str:
    """The theme kept in `path` (theme.THEMES); the desktop's where there is none."""
    try:
        name = json.loads(path.read_text(encoding="utf-8")).get("theme")
    except (OSError, ValueError, AttributeError):
        return "system"
    return name if name in theme.THEMES else "system"


def main() -> None:

    app = QApplication(sys.argv)
    app.setApplicationName("KFM")
    app.setDesktopFileName(app_menu.APP_ID)  # the app menu's entry: its icon and its name
    app.setWindowIcon(QIcon(str(app_menu.ICON)))
    app.setQuitOnLastWindowClosed(False)  # the service is let go of first
    look = HOME / "window.json"
    theme.set_theme(app, saved_theme(look))
    loop = QtEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        try:  # the service that owns the fleets, started if none runs
            service = loop.run_until_complete(RemoteService.connect())
        except ServiceError as e:
            QMessageBox.critical(None, "KFM", str(e))
            return
        window = FleetWindow(service, Notifier(), look,
                             tray=QSystemTrayIcon.isSystemTrayAvailable())
        window.resize(1180, 760)
        window.show()
        window.offer_restart()
        loop.run_until_complete(window.closed.wait())


if __name__ == "__main__":
    main()
