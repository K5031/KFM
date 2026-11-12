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
from dataclasses import replace
from pathlib import Path
from typing import Any

import kayden.frontends.desktop  # noqa: F401 - before any Qt import
from kayden.frontends.desktop import theme
from kayden.frontends.desktop.loop import QtEventLoop
from kayden.frontends.desktop.window import ElidedLabel, MarkdownView
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kfm import log
from kfm.api import Harness, UnitError
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load
from kfm.events import Answered, Approval, Changed, Delivered, FleetEvent, Notice, Said, Told
from kfm.fleet import RECORD, Fleet, FleetError, Unit
from kfm.manager import Manager

SIDEBAR = 280  # px
FORM = 640  # px, the start form's width
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


class StartForm(QWidget):
    """The window's empty state: what a new fleet needs."""

    def __init__(self) -> None:
        super().__init__()
        self.setMaximumWidth(FORM)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(label("Start a fleet", role="title"))
        outer.addWidget(label("Tell the lead what you want. It plans, hands work to agents "
                              "below it, checks what comes back and reports to you.",
                              tone="muted"))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.task = QPlainTextEdit()
        self.task.setPlaceholderText("What the fleet should achieve")
        self.task.setFixedHeight(96)
        self.duty = QCheckBox("Standing work that never ends (a duty), instead of a task "
                              "that finishes")
        self.folder = QLineEdit(str(Path.cwd()))
        self.browse = QPushButton("Choose…")
        folder = QHBoxLayout()
        folder.addWidget(self.folder)
        folder.addWidget(self.browse)
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0.1, 100_000.0)
        self.budget.setDecimals(2)
        self.budget.setPrefix("$")
        self.start = QPushButton("Start")
        self.start.setProperty("primary", True)
        form.addRow("Task", self.task)
        form.addRow("", self.duty)
        form.addRow("Works in", folder)
        form.addRow("May spend", self.budget)
        form.addRow("", self.start)
        outer.addLayout(form)
        outer.addStretch(2)
        self.browse.clicked.connect(self._choose)

    def _choose(self) -> None:
        dialog = QFileDialog(self, "The folder the fleet works in", self.folder.text())
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.fileSelected.connect(self.folder.setText)
        dialog.open()


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
        self._tasks: set[asyncio.Task[None]] = set()
        self.setWindowTitle("KFM")
        self._build()
        self.show_all.hide()
        self._timer = QTimer(self, interval=REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._refresh()

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
        self.new_button = QPushButton("New fleet", objectName="nav")
        self.open_button = QPushButton("Open a fleet…", objectName="nav")
        self.new_button.clicked.connect(lambda: self._run(self.new_fleet()))
        self.open_button.clicked.connect(lambda: self._run(self._choose_fleet()))
        side = QFrame(objectName="fleetSide")
        side.setFixedWidth(SIDEBAR)
        colors = theme.current()
        side.setStyleSheet(
            f"QFrame#fleetSide {{ background: {colors.side}; }}"
            f"QTreeWidget {{ background: transparent; border: none; }}"
            f"QTreeWidget::item {{ padding: 5px 2px; }}"
            f"QTreeWidget::item:selected {{ background: {colors.select}; "
            f"color: {colors.text}; }}")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.addWidget(self.new_button)
        side_layout.addWidget(self.open_button)
        self.show_all = QPushButton("Show all", objectName="nav")
        self.show_all.clicked.connect(lambda: self.agents.setCurrentItem(None))
        heading = QHBoxLayout()
        heading.addWidget(label("Agents", role="small", tone="muted"), 1)
        heading.addWidget(self.show_all)
        side_layout.addLayout(heading)
        side_layout.addWidget(self.agents, 1)

        self.title = ElidedLabel("No fleet")
        self.title.setProperty("role", "heading")
        self.status = label("", role="small", tone="muted", wrap=False)
        self.stop_button = QPushButton("Stop")
        self.resume_button = QPushButton("Resume")
        self.resume_button.setProperty("primary", True)
        self.stop_button.clicked.connect(lambda: self._run(self.stop()))
        self.resume_button.clicked.connect(lambda: self._run(self.resume()))
        bar = QHBoxLayout()
        bar.addWidget(self.title, 1)
        bar.addWidget(self.status)
        bar.addWidget(self.stop_button)
        bar.addWidget(self.resume_button)

        self.start_form = StartForm()
        self.start_form.start.clicked.connect(self._start_clicked)
        self._column = QWidget(objectName="transcript")
        self._timeline = QVBoxLayout(self._column)
        self._timeline.setContentsMargins(24, 12, 24, 12)
        self._timeline.setSpacing(8)
        self._timeline.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self._column)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Message the lead")
        self.input.setFixedHeight(64)
        self.send_button = QPushButton("Send")
        self.send_button.setProperty("primary", True)
        self.send_button.clicked.connect(self._send_clicked)
        composer = QHBoxLayout()
        composer.addWidget(self.input, 1)
        composer.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        main = QVBoxLayout()
        main.setContentsMargins(20, 14, 20, 14)
        main.addLayout(bar)
        main.addWidget(self.start_form, 1, Qt.AlignmentFlag.AlignHCenter)
        main.addWidget(self.scroll, 1)
        self.composer = QWidget()
        self.composer.setLayout(composer)
        composer.setContentsMargins(0, 0, 0, 0)
        main.addWidget(self.composer)
        whole = QWidget()
        layout = QHBoxLayout(whole)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(side)
        layout.addLayout(main, 1)
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
            manager = Manager(config, folder, work.expanduser().resolve(),
                              harness=self._harness, on_event=self._on_event)
        except (ValueError, OSError) as e:
            self._say(f"The fleet could not start: {e}", tone="error")
            return
        self._clear()
        self.manager, self.fleet, self.folder = manager, manager.fleet, folder
        await manager.start()
        self._timer.start()
        self._refresh()
        await manager.lead(task, ongoing=ongoing)
        self._refresh()

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
        self._refresh()

    async def new_fleet(self) -> None:
        """Back to the empty state; a running fleet is stopped."""
        await self.stop()
        self._clear()
        self.fleet = self.folder = None
        self._refresh()

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
            self._refresh()

    async def shut(self) -> None:
        """Stop the fleet and close the window."""
        await self.stop()
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        self.closed.set()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.closed.is_set():
            self._run(self.shut())
        event.accept()

    def earlier_fleets(self) -> list[tuple[Path, str]]:
        """Every fleet under `home`, newest first, with its start and its task. Blocking."""
        found = []
        for record in sorted((self.home / "fleets").glob(f"*/{RECORD}"), reverse=True):
            try:
                units = json.loads(record.read_text())["units"]
                task = next(u["task"] for u in units if u["supervisor"] is None)
            except (ValueError, KeyError, StopIteration, OSError):
                continue
            found.append((record.parent, f"{record.parent.name}  {_clip(task, 60)}"))
        return found

    async def _choose_fleet(self) -> None:
        fleets = await asyncio.to_thread(self.earlier_fleets)
        menu = QMenu(self)
        if not fleets:
            menu.addAction("No fleets yet").setEnabled(False)
        for folder, text in fleets:
            menu.addAction(text).triggered.connect(
                lambda _=False, folder=folder: self._run(self.open_fleet(folder)))
        menu.popup(self.open_button.mapToGlobal(self.open_button.rect().bottomLeft()))

    def _start_clicked(self) -> None:
        form = self.start_form
        task = form.task.toPlainText().strip()
        if not task:
            form.task.setFocus()
            return
        self._run(self.start_fleet(task, ongoing=form.duty.isChecked(),
                                   work=Path(form.folder.text()), budget=form.budget.value()))

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
        if fleet is None:
            self.title.setText("No fleet")
            self.status.setText("")
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

    def _run(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("KFM")
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
