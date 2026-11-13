"""A project's chart: its fleets side by side, each drawn as the tree of who supervises
whom, a row for each rank."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kayden.frontends.desktop import theme
from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from kfm.fleet import Fleet, Unit

NODE_W, NODE_H = 168, 56  # px, an agent
GAP_X, GAP_Y = 16, 36  # between agents side by side, and between ranks
HEAD_H = 46  # a fleet's name and state
FLEET_GAP = 56  # between fleets
MARGIN = 28


@dataclass(frozen=True, slots=True)
class ChartFleet:
    key: str  # tells fleets apart: its folder
    name: str
    status: str
    fleet: Fleet | None  # None until its record is read


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
    """An agent: its name and rank, its state and what it has spent."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "node")
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

    def show_unit(self, unit: Unit) -> None:
        room = NODE_W - 24 - 30  # beside the rank
        self.name.setText(QFontMetrics(self.name.font()).elidedText(
            unit.name, Qt.TextElideMode.ElideRight, room))
        self.rank.setText(f"K{unit.rank}")
        self.state.setText(unit.state)
        colors = theme.current()
        tone = {"working": colors.accent, "starting": colors.accent,
                "waiting": colors.warning, "failed": colors.error,
                "done": colors.ok}.get(unit.state, colors.muted)
        self.state.setStyleSheet(f"color: {tone};")
        self.spent.setText(f"${unit.spent_usd:.2f}")
        self.setToolTip(unit.task)


class Head(_Box):
    """A fleet's name over its tree, and how it stands."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "fleetHead")
        self.name, self.status = _text("title"), _text("small")
        rows = QVBoxLayout(self)
        rows.setContentsMargins(2, 0, 2, 4)
        rows.setSpacing(0)
        rows.addWidget(self.name)
        rows.addWidget(self.status)


class Chart(QWidget):
    """`opened`: a fleet's key and the unit clicked, or "" for the fleet as a whole."""

    opened = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chart")
        colors = theme.current()
        self.setStyleSheet(
            f"QFrame#node {{ background: {colors.bg}; border: 1px solid {colors.line}; "
            f"border-radius: 10px; }}"
            f"QFrame#node:hover {{ border-color: {colors.accent}; }}"
            f"QFrame#fleetHead {{ border: none; }}")
        self.nodes: dict[tuple[str, str], Node] = {}
        self.heads: dict[str, Head] = {}
        self._lines: list[tuple[QPointF, QPointF]] = []  # a supervisor's foot, an agent's top
        self.add_button = QPushButton(" Add fleet", self, objectName="nav")
        self.add_button.setIcon(theme.icon("plus", colors.text, 16))
        self.add_button.adjustSize()

    def show_fleets(self, fleets: Sequence[ChartFleet]) -> None:
        """Draw `fleets` from left to right; boxes of what is gone are removed."""
        x, lowest = MARGIN, 0
        nodes: dict[tuple[str, str], Node] = {}
        heads: dict[str, Head] = {}
        self._lines = []
        top = MARGIN + HEAD_H + GAP_Y // 2
        for shown in fleets:
            at, columns = slots(shown.fleet) if shown.fleet is not None else ({}, 1.0)
            width = round(columns * NODE_W + (columns - 1) * GAP_X)
            head = heads[shown.key] = self.heads.pop(shown.key, None) or self._head(shown.key)
            head.name.setText(shown.name)
            head.status.setText(shown.status)
            head.setGeometry(x, MARGIN, width, HEAD_H)
            head.show()
            placed: dict[str, QRect] = {}
            for unit_id, (column, row) in at.items():
                key = (shown.key, unit_id)
                node = nodes[key] = self.nodes.pop(key, None) or self._node(*key)
                node.show_unit(shown.fleet.units[unit_id])  # type: ignore[union-attr]
                placed[unit_id] = QRect(x + round(column * (NODE_W + GAP_X)),
                                        top + row * (NODE_H + GAP_Y), NODE_W, NODE_H)
                node.setGeometry(placed[unit_id])
                node.show()
                lowest = max(lowest, row + 1)
            for unit_id, rect in placed.items():
                above = placed.get(shown.fleet.units[unit_id].supervisor or "")  # type: ignore[union-attr]
                if above is not None:
                    self._lines.append((QPointF(above.center().x(), above.bottom() + 1),
                                        QPointF(rect.center().x(), rect.top())))
            x += width + FLEET_GAP
        for gone in (*self.nodes.values(), *self.heads.values()):
            gone.setParent(None)
            gone.deleteLater()
        self.nodes, self.heads = nodes, heads
        self.add_button.move(x, MARGIN + (HEAD_H - self.add_button.height()) // 2)
        self.setMinimumSize(x + self.add_button.width() + MARGIN,
                            top + lowest * (NODE_H + GAP_Y) + MARGIN)
        self.update()

    def _head(self, key: str) -> Head:
        head = Head(self)
        head.clicked.connect(lambda: self.opened.emit(key, ""))
        return head

    def _node(self, key: str, unit_id: str) -> Node:
        node = Node(self)
        node.clicked.connect(lambda: self.opened.emit(key, unit_id))
        return node

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.current().line), 1.5))
        for foot, head in self._lines:  # down, across at half height, down
            middle = (foot.y() + head.y()) / 2
            painter.drawPolyline([foot, QPointF(foot.x(), middle),
                                  QPointF(head.x(), middle), head])
