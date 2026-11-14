"""A project's chart: its fleets side by side, each a card with the tree of who supervises
whom, a row for each rank."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kadence.frontends.desktop import theme
from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kfm.fleet import Fleet, Unit

NODE_W, NODE_H = 168, 56  # px, an agent
GAP_X, GAP_Y = 16, 36  # between agents side by side, and between ranks
HEAD_H = 46  # a fleet's name and state
HEAD_GAP = 22  # under it, before the tree
PAD = 20  # inside a fleet's card
INNER_MIN = 250  # the least a card holds across: its name, its state and Start
FLEET_GAP = 20  # between fleets
ADD_W = 168  # the place for another fleet
MARGIN = 28
RADIUS = 14


@dataclass(frozen=True, slots=True)
class ChartFleet:
    key: str  # tells fleets apart: its id in the service
    name: str
    status: str
    fleet: Fleet | None  # None until its record is read
    running: bool = False
    editable: bool = False  # agents can be added to it and changed
    start: str = ""  # what its button says while it can be started: "Start", "Resume"


def slots(fleet: Fleet) -> tuple[dict[str, tuple[float, int]], float]:
    """Where each unit stands, as (column, row), and how many columns the fleet takes. An
    agent with nobody under it takes one column; a supervisor stands over the middle of
    its agents; the row is how far the unit's rank is below the top one."""
    at: dict[str, tuple[float, int]] = {}

    def place(unit: Unit, left: float) -> float:
        width = 0.0
        for below in fleet.subordinates(unit):
            width += place(below, left + width)
        width = max(width, 1.0)
        at[unit.id] = (left + (width - 1) / 2, fleet.top_rank - unit.rank)
        return width

    lead = fleet.lead
    return at, place(lead, 0.0) if lead is not None else 1.0


def state_tone(state: str, running: bool) -> str:
    """The colour an agent's state is written in. What it was doing when its fleet
    stopped is not what it does now: working and waiting are muted then."""
    colors = theme.current()
    at_work = {"working": colors.accent, "starting": colors.accent, "waiting": colors.warning}
    return {"failed": colors.error, "done": colors.ok}.get(
        state, at_work.get(state, colors.muted) if running else colors.muted)


def _text(role: str | None = None, tone: str | None = None) -> QLabel:
    made = QLabel()
    made.setTextFormat(Qt.TextFormat.PlainText)
    made.setProperty("role", role)
    made.setProperty("tone", tone)
    made.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # the box is clicked
    return made


class _Box(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget, name: str) -> None:
        super().__init__(parent, objectName=name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()


class Node(_Box):
    """An agent: its name and rank, its state and what it has spent. A right-click offers
    what the person can do to it."""

    add_asked = Signal()
    model_asked = Signal()
    rename_asked = Signal()
    task_asked = Signal()
    remove_asked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "node")
        self.menu = QMenu(self)
        self.rename_action = self.menu.addAction("Rename…")
        self.rename_action.triggered.connect(lambda: self.rename_asked.emit())
        self.add_action = self.menu.addAction("Add agent…")
        self.add_action.triggered.connect(lambda: self.add_asked.emit())
        self.model_action = self.menu.addAction("Model…")
        self.model_action.triggered.connect(lambda: self.model_asked.emit())
        self.task_action = self.menu.addAction("Task…")
        self.task_action.triggered.connect(lambda: self.task_asked.emit())
        self.menu.addSeparator()
        self.remove_action = self.menu.addAction("Remove…")
        self.remove_action.triggered.connect(lambda: self.remove_asked.emit())
        self.name, self.rank = _text("strong"), _text("small")
        self.state, self.spent = _text("small"), _text("small")
        rows = QVBoxLayout(self)
        rows.setContentsMargins(12, 8, 12, 8)
        rows.setSpacing(2)
        for left, right in ((self.name, self.rank), (self.state, self.spent)):
            row = QHBoxLayout()
            row.addWidget(left, 1)
            row.addWidget(right)
            rows.addLayout(row)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.menu.popup(event.globalPos())

    def show_unit(self, unit: Unit, editable: bool, running: bool) -> None:
        self.add_action.setEnabled(editable and unit.rank > 1)
        self.model_action.setEnabled(editable)
        self.rename_action.setEnabled(editable)
        self.remove_action.setEnabled(editable)
        self.task_action.setEnabled(editable)
        room = NODE_W - 24 - 30  # beside the rank
        self.name.setText(QFontMetrics(self.name.font()).elidedText(
            unit.name or f"K{unit.rank}", Qt.TextElideMode.ElideRight, room))
        self.rank.setText(f"K{unit.rank}" if unit.name else "")  # a nameless one is its rank
        self.state.setText(unit.state)
        self.state.setStyleSheet(f"color: {state_tone(unit.state, running)};")
        self.spent.setText(f"${unit.spent_usd:.2f}")
        self.setToolTip(f"{unit.task}\n{unit.model}" if unit.model else unit.task)


class Head(_Box):
    """A fleet's name over its tree, how it stands, and Start while it can be started. A
    right-click offers to rename or delete it."""

    delete_asked = Signal()
    rename_asked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "fleetHead")
        self.name, self.status = _text("title"), _text("small")
        self.menu = QMenu(self)
        self.rename_action = self.menu.addAction("Rename…")
        self.rename_action.triggered.connect(lambda: self.rename_asked.emit())
        self.delete_action = self.menu.addAction("Delete…")
        self.delete_action.triggered.connect(lambda: self.delete_asked.emit())
        self.start = QPushButton("Start")
        self.start.setProperty("primary", True)
        self.start.setCursor(Qt.CursorShape.ArrowCursor)
        rows = QVBoxLayout()
        rows.setSpacing(0)
        rows.addWidget(self.name)
        rows.addWidget(self.status)
        across = QHBoxLayout(self)
        across.setContentsMargins(0, 0, 0, 0)
        across.setSpacing(12)
        across.addLayout(rows, 1)
        across.addWidget(self.start, 0, Qt.AlignmentFlag.AlignVCenter)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.menu.popup(event.globalPos())


class Chart(QWidget):
    """`opened`: a fleet's key and the unit clicked, or "" for the fleet as a whole.
    `add_asked`, `model_asked`: a fleet's key and the unit to add under ("": as its lead)
    or to change. `start_asked`, `delete_asked`: a fleet's key. `rename_asked`: a fleet's
    key and the unit to rename, or "" for the fleet. `task_asked`, `remove_asked`: a
    fleet's key and the unit."""

    opened = Signal(str, str)
    add_asked = Signal(str, str)
    model_asked = Signal(str, str)
    start_asked = Signal(str)
    delete_asked = Signal(str)
    rename_asked = Signal(str, str)
    task_asked = Signal(str, str)
    remove_asked = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chart")
        colors = theme.current()
        self.setStyleSheet(
            f"QFrame#node {{ background: {colors.bg}; border: 1px solid {colors.line}; "
            f"border-radius: 10px; }}"
            f"QFrame#node:hover {{ border-color: {colors.accent}; }}"
            f"QFrame#fleetHead {{ border: none; }}"
            f"QPushButton#firstAgent {{ background: transparent; color: {colors.muted}; "
            f"border: 1px dashed {colors.track}; border-radius: 10px; }}"
            f"QPushButton#firstAgent:hover {{ border-color: {colors.accent}; "
            f"color: {colors.text}; }}")
        self.nodes: dict[tuple[str, str], Node] = {}
        self.heads: dict[str, Head] = {}
        self.firsts: dict[str, QPushButton] = {}  # where a fleet with no agent gets its lead
        self._lines: list[tuple[QPointF, QPointF]] = []  # a supervisor's foot, an agent's top
        self._cards: list[QRect] = []
        self._shown: list[ChartFleet] = []
        self.add_button = QPushButton(" Add fleet", self, objectName="firstAgent")
        self.add_button.setIcon(theme.icon("plus", colors.muted, 16))
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)

    def show_fleets(self, fleets: Sequence[ChartFleet]) -> None:
        """Draw `fleets` from left to right, in the middle, on as many lines as they need;
        boxes of what is gone are removed."""
        self._shown = list(fleets)
        placed = [slots(shown.fleet) if shown.fleet is not None else ({}, 1.0)
                  for shown in fleets]
        inners = [max(round(columns * NODE_W + (columns - 1) * GAP_X), INNER_MIN)
                  for _, columns in placed]
        # Cards fill a line and then start the next; the place for another fleet is last.
        widths = [inner + 2 * PAD for inner in inners] + [ADD_W]
        room = max(self.width() - 2 * MARGIN, max(widths))
        lines: list[list[int]] = [[]]
        used = 0
        for i, width in enumerate(widths):
            if lines[-1] and used + width > room:
                lines.append([])
                used = 0
            lines[-1].append(i)
            used += width + FLEET_GAP
        widest = max(sum(widths[i] + FLEET_GAP for i in line) - FLEET_GAP for line in lines)
        start = max(MARGIN, (self.width() - widest) // 2)
        nodes: dict[tuple[str, str], Node] = {}
        heads: dict[str, Head] = {}
        firsts: dict[str, QPushButton] = {}
        self._lines, self._cards = [], []
        y = MARGIN
        for line in lines:
            ranks = max((row + 1 for i in line if i < len(fleets)
                         for _, row in placed[i][0].values()), default=1)
            height = PAD + HEAD_H + HEAD_GAP + ranks * NODE_H + (ranks - 1) * GAP_Y + PAD
            x = start
            for i in line:
                if i == len(fleets):
                    self.add_button.setGeometry(x, y, ADD_W, height)
                else:
                    self._place(fleets[i], placed[i][0], placed[i][1], inners[i],
                                QRect(x, y, widths[i], height), nodes, heads, firsts)
                x += widths[i] + FLEET_GAP
            y += height + FLEET_GAP
        for gone in (*self.nodes.values(), *self.heads.values(), *self.firsts.values()):
            gone.setParent(None)
            gone.deleteLater()
        self.nodes, self.heads, self.firsts = nodes, heads, firsts
        self.setMinimumSize(max(widths) + 2 * MARGIN, y - FLEET_GAP + MARGIN)
        self.update()

    def _place(self, shown: ChartFleet, at: dict[str, tuple[float, int]], columns: float,
               inner: int, card: QRect, nodes: dict[tuple[str, str], Node],
               heads: dict[str, Head], firsts: dict[str, QPushButton]) -> None:
        """One fleet in its card: its head, then its tree in the middle."""
        self._cards.append(card)
        x, top = card.x(), card.y() + PAD + HEAD_H + HEAD_GAP
        head = heads[shown.key] = self.heads.pop(shown.key, None) or self._head(shown.key)
        head.name.setText(shown.name)
        head.status.setText(shown.status)
        head.start.setText(shown.start)
        head.start.setVisible(bool(shown.start))
        head.setGeometry(x + PAD, card.y() + PAD, inner, HEAD_H)
        head.show()
        width = round(columns * NODE_W + (columns - 1) * GAP_X)
        left = x + PAD + (inner - width) // 2
        if shown.fleet is not None and not shown.fleet.units and shown.editable:
            first = firsts[shown.key] = (self.firsts.pop(shown.key, None)
                                         or self._first(shown.key))
            first.setGeometry(left, top, NODE_W, NODE_H)
            first.show()
        boxes: dict[str, QRect] = {}
        for unit_id, (column, row) in at.items():
            key = (shown.key, unit_id)
            node = nodes[key] = self.nodes.pop(key, None) or self._node(*key)
            node.show_unit(shown.fleet.units[unit_id],  # type: ignore[union-attr]
                           shown.editable, shown.running)
            boxes[unit_id] = QRect(left + round(column * (NODE_W + GAP_X)),
                                   top + row * (NODE_H + GAP_Y), NODE_W, NODE_H)
            node.setGeometry(boxes[unit_id])
            node.show()
        for unit_id, rect in boxes.items():
            above = boxes.get(shown.fleet.units[unit_id].supervisor or "")  # type: ignore[union-attr]
            if above is not None:
                self._lines.append((QPointF(above.center().x(), above.bottom() + 1),
                                    QPointF(rect.center().x(), rect.top())))

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.show_fleets(self._shown)

    def _head(self, key: str) -> Head:
        head = Head(self)
        head.clicked.connect(lambda: self.opened.emit(key, ""))
        head.start.clicked.connect(lambda: self.start_asked.emit(key))
        head.delete_asked.connect(lambda: self.delete_asked.emit(key))
        head.rename_asked.connect(lambda: self.rename_asked.emit(key, ""))
        return head

    def _first(self, key: str) -> QPushButton:
        first = QPushButton(" Add agent", self, objectName="firstAgent")
        first.setIcon(theme.icon("plus", theme.current().muted, 16))
        first.setCursor(Qt.CursorShape.PointingHandCursor)
        first.clicked.connect(lambda: self.add_asked.emit(key, ""))
        return first

    def _node(self, key: str, unit_id: str) -> Node:
        node = Node(self)
        node.clicked.connect(lambda: self.opened.emit(key, unit_id))
        node.add_asked.connect(lambda: self.add_asked.emit(key, unit_id))
        node.model_asked.connect(lambda: self.model_asked.emit(key, unit_id))
        node.rename_asked.connect(lambda: self.rename_asked.emit(key, unit_id))
        node.task_asked.connect(lambda: self.task_asked.emit(key, unit_id))
        node.remove_asked.connect(lambda: self.remove_asked.emit(key, unit_id))
        return node

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = theme.current()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors.card))
        for card in self._cards:
            painter.drawRoundedRect(card, RADIUS, RADIUS)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(colors.track), 1.5))
        for foot, head in self._lines:  # down, across at half height, down
            middle = (foot.y() + head.y()) / 2
            painter.drawPolyline([foot, QPointF(foot.x(), middle),
                                  QPointF(head.x(), middle), head])
