"""
PyQt5 timeline widget for wtd_preview.
Three lanes: subtitles, dubs, speakers. Draggable segments and markers.
"""

import math
from typing import Callable, Optional

from PyQt5.QtCore import (
    QLineF, QPointF, QRectF, Qt, pyqtSignal, QTimer,
)
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QPainter, QPen, QTransform,
)
from PyQt5.QtWidgets import (
    QApplication, QGraphicsItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView, QGraphicsTextItem,
    QToolTip, QWidget,
)

from preview_srt import SubtitleEntry

# Colors
LANE_COLORS = {
    "subtitle": QColor(60, 130, 216),
    "dub":      QColor(220, 80, 80),
    "speaker":  QColor(80, 180, 100),
}
LANE_BG = {
    "subtitle": QColor(230, 240, 255),
    "dub":      QColor(255, 230, 230),
    "speaker":  QColor(230, 255, 235),
}
PLAYHEAD_COLOR = QColor(255, 200, 0)
MARKER_COLOR = QColor(255, 150, 0)
DUB_MARKER_COLOR = QColor(255, 50, 50)
SELECTED_PEN = QPen(QColor(255, 255, 255), 2)
NORMAL_PEN = QPen(Qt.black, 1)

LANE_HEIGHT = 36
LANE_GAP = 4
HEADER_HEIGHT = 20
MARGIN_LEFT = 60
MARKER_WIDTH = 6
SEGMENT_HEIGHT = 28


class TimelineSegment(QGraphicsRectItem):
    """A draggable segment on a timeline lane."""

    def __init__(self, entry: SubtitleEntry, lane: str,
                 x: float, y: float, w: float,
                 parent=None):
        super().__init__(0, 0, w, SEGMENT_HEIGHT, parent)
        self.entry = entry
        self.lane = lane
        self.setPos(x, y)
        self.setPen(NORMAL_PEN)
        self.setBrush(LANE_COLORS.get(lane, QColor(200, 200, 200)))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._drag_start_x = 0.0
        self._orig_start = 0.0
        self._orig_end = 0.0
        self._dragging = False
        self._edge_drag: Optional[str] = None  # 'start' or 'end'
        self._pixels_per_second = 50.0

        self._tooltip = QGraphicsTextItem(self)
        self._tooltip.setPlainText(entry.text or "")
        self._tooltip.setDefaultTextColor(Qt.white)
        self._tooltip.setFont(QFont("Segoe UI", 8))
        rect = self._tooltip.boundingRect()
        if rect.width() > w - 4:
            self._tooltip.setPlainText(entry.text[:int((w - 4) / 6)] + "…")
        self._tooltip.setPos(2, 2)

    def set_pixels_per_second(self, pps: float) -> None:
        self._pixels_per_second = pps

    def set_duration_px(self, w: float) -> None:
        self.setRect(0, 0, w, SEGMENT_HEIGHT)
        txt = self.entry.text or ""
        allowed = max(10, int((w - 4) / 6))
        if len(txt) > allowed:
            txt = txt[:allowed] + "…"
        self._tooltip.setPlainText(txt)

    def hoverMoveEvent(self, event):
        local_x = event.pos().x()
        if local_x < 6:
            self.setCursor(Qt.SizeHorCursor)
        elif local_x > self.rect().width() - 6:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        dur = self.entry.end - self.entry.start
        QToolTip.showText(
            event.screenPos(),
            f"{self.lane}: {self._fmt(self.entry.start)} → {self._fmt(self.entry.end)} "
            f"({dur:.2f}s)\n{self.entry.text}",
        )
        super().hoverMoveEvent(event)

    @staticmethod
    def _fmt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:06.3f}"

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            local_x = event.pos().x()
            self._orig_start = self.entry.start
            self._orig_end = self.entry.end
            self._drag_start_x = event.scenePos().x()

            if local_x < 6:
                self._edge_drag = 'start'
            elif local_x > self.rect().width() - 6:
                self._edge_drag = 'end'
            else:
                self._edge_drag = None
                self._dragging = True

            self.setSelected(True)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        dx = event.scenePos().x() - self._drag_start_x
        dt = dx / self._pixels_per_second

        if self._edge_drag == 'start':
            new_start = max(0, self._orig_start + dt)
            if new_start < self.entry.end - 0.1:
                self.entry.start = new_start
                px = (self.entry.start - self._orig_start) * self._pixels_per_second
                self.setPos(self.pos().x() + px, self.pos().y())
                new_w = (self.entry.end - self.entry.start) * self._pixels_per_second
                self.set_duration_px(max(4, new_w))
        elif self._edge_drag == 'end':
            new_end = self._orig_end + dt
            if new_end > self.entry.start + 0.1:
                self.entry.end = new_end
                new_w = (self.entry.end - self.entry.start) * self._pixels_per_second
                self.set_duration_px(max(4, new_w))
        elif self._dragging:
            new_start = max(0, self._orig_start + dt)
            dur = self.entry.end - self.entry.start
            self.entry.start = new_start
            self.entry.end = new_start + dur
            px = (self.entry.start - self._orig_start) * self._pixels_per_second
            self.setPos(self.pos().x() + px, self.pos().y())

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._edge_drag = None
        super().mouseReleaseEvent(event)


class TimelineView(QGraphicsView):
    """Interactive timeline with zoom, pan, playhead, and three lanes."""

    seek_requested = pyqtSignal(float)
    segment_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(False)

        self._segments: list[TimelineSegment] = []
        self._playhead: Optional[QGraphicsRectItem] = None
        self._playhead_line: Optional[QGraphicsRectItem] = None
        self._playhead_pos = 0.0
        self._duration = 10.0
        self._pixels_per_second = 50.0
        self._total_width = 0.0
        self._looping = False
        self._dub_markers: list[QGraphicsRectItem] = []
        self._header_items: list[QGraphicsTextItem] = []
        self._entries: list[SubtitleEntry] = []

        # Time ruler labels
        self._ruler_y = 0
        self._lane_y_start = HEADER_HEIGHT + 8

        # Zoom state
        self._min_pps = 10.0
        self._max_pps = 500.0

        self._setup_scene()

    def _setup_scene(self) -> None:
        self._scene.setBackgroundBrush(QBrush(QColor(50, 50, 55)))
        # Playhead
        self._playhead = QGraphicsRectItem(0, 0, 3, 2000)
        self._playhead.setBrush(QBrush(PLAYHEAD_COLOR))
        self._playhead.setPen(QPen(Qt.NoPen))
        self._playhead.setZValue(100)
        self._scene.addItem(self._playhead)

    def set_entries(self, entries: list[SubtitleEntry], duration: float) -> None:
        self._entries = entries
        self._duration = duration
        self._rebuild()

    def _rebuild(self) -> None:
        self._scene.clear()
        self._segments.clear()
        self._dub_markers.clear()
        self._header_items.clear()

        # Re-create playhead
        self._playhead = QGraphicsRectItem(0, 0, 3, self._total_height())
        self._playhead.setBrush(QBrush(PLAYHEAD_COLOR))
        self._playhead.setPen(QPen(Qt.NoPen))
        self._playhead.setZValue(100)
        self._scene.addItem(self._playhead)
        self._sync_playhead()

        # Total width
        margin = 200
        self._total_width = max(self._duration * self._pixels_per_second + margin, self.width())

        # Lane backgrounds
        lanes = ["subtitle", "dub", "speaker"]
        for i, lane in enumerate(lanes):
            y = self._lane_y(lane)
            bg = QGraphicsRectItem(0, y, self._total_width, LANE_HEIGHT)
            bg.setBrush(QBrush(LANE_BG.get(lane, QColor(60, 60, 65))))
            bg.setPen(QPen(Qt.NoPen))
            bg.setZValue(-1)
            self._scene.addItem(bg)
            # Lane label
            label = QGraphicsTextItem(lane)
            label.setPos(2, y + 2)
            label.setDefaultTextColor(QColor(180, 180, 190))
            label.setFont(QFont("Segoe UI", 9, QFont.Bold))
            self._scene.addItem(label)

        # Draw segments
        for entry in self._entries:
            lane = self._lane_for_entry(entry)
            x = MARGIN_LEFT + entry.start * self._pixels_per_second
            w = max(4, (entry.end - entry.start) * self._pixels_per_second)
            y = self._lane_y(lane) + (LANE_HEIGHT - SEGMENT_HEIGHT) / 2
            seg = TimelineSegment(entry, lane, x, y, w)
            seg.set_pixels_per_second(self._pixels_per_second)
            self._scene.addItem(seg)
            self._segments.append(seg)

        # Dub marker indicators
        for i, entry in enumerate(self._entries):
            if entry.is_dub:
                for lane_name in ["subtitle", "speaker"]:
                    y = self._lane_y(lane_name)
                    marker = QGraphicsRectItem(
                        MARGIN_LEFT + entry.start * self._pixels_per_second - 1,
                        y, 3, LANE_HEIGHT,
                    )
                    marker.setBrush(QBrush(DUB_MARKER_COLOR))
                    marker.setPen(QPen(Qt.NoPen))
                    marker.setZValue(10)
                    self._scene.addItem(marker)
                    self._dub_markers.append(marker)

        # Time ruler
        self._draw_ruler()

        # Update total height
        self._scene.setSceneRect(0, 0, self._total_width, self._total_height())

    def _total_height(self) -> float:
        return HEADER_HEIGHT + 3 * (LANE_HEIGHT + LANE_GAP) + 20

    def _lane_y(self, lane: str) -> float:
        order = ["subtitle", "dub", "speaker"]
        idx = order.index(lane) if lane in order else 0
        return self._lane_y_start + idx * (LANE_HEIGHT + LANE_GAP)

    def _lane_for_entry(self, entry: SubtitleEntry) -> str:
        if entry.is_dub:
            return "dub"
        return "speaker"  # Speaker lane by default; could refine

    def _draw_ruler(self) -> None:
        step = self._calc_ruler_step()
        total_sec = int(self._total_width / self._pixels_per_second) + 1
        for t in range(0, total_sec, step):
            x = MARGIN_LEFT + t * self._pixels_per_second
            line = self._scene.addLine(QLineF(x, HEADER_HEIGHT - 6, x, HEADER_HEIGHT))
            line.setPen(QPen(QColor(180, 180, 190)))

            h = t // 3600
            m = (t % 3600) // 60
            s = t % 60
            label = QGraphicsTextItem(f"{h}:{m:02d}:{s:02d}")
            label.setPos(x + 2, 1)
            label.setDefaultTextColor(QColor(200, 200, 210))
            label.setFont(QFont("Segoe UI", 8))
            self._scene.addItem(label)

    def _calc_ruler_step(self) -> int:
        px_per_sec = self._pixels_per_second
        if px_per_sec > 150:
            return 1
        elif px_per_sec > 60:
            return 5
        elif px_per_sec > 20:
            return 10
        else:
            return 30

    def set_playhead_pos(self, seconds: float) -> None:
        self._playhead_pos = seconds
        self._sync_playhead()

    def _sync_playhead(self) -> None:
        if not self._playhead:
            return
        x = MARGIN_LEFT + self._playhead_pos * self._pixels_per_second
        self._playhead.setPos(x, 0)
        self.ensureVisible(self._playhead, xMargin=100, yMargin=0)

    def zoom_in(self) -> None:
        self._pixels_per_second = min(self._max_pps, self._pixels_per_second * 1.3)
        self._rebuild()

    def zoom_out(self) -> None:
        self._pixels_per_second = max(self._min_pps, self._pixels_per_second / 1.3)
        self._rebuild()

    def reset_zoom(self) -> None:
        self._pixels_per_second = 50.0
        self._rebuild()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Seek on double-click or single click on empty area
            scene_pos = self.mapToScene(event.pos())
            item = self.itemAt(event.pos())
            if not item or isinstance(item, QGraphicsTextItem):
                sec = (scene_pos.x() - MARGIN_LEFT) / self._pixels_per_second
                sec = max(0, min(sec, self._duration))
                self.seek_requested.emit(sec)
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            # Zoom
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self._pixels_per_second = max(
                self._min_pps, min(self._max_pps, self._pixels_per_second * factor)
            )
            self._rebuild()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-render if wider
        if self._total_width < self.width():
            self._rebuild()

    def keyPressEvent(self, event):
        # Handled by wtd_preview window instead
        super().keyPressEvent(event)

    def get_entries(self) -> list[SubtitleEntry]:
        return self._entries
