"""화면 전체를 덮는 반투명 오버레이에서 드래그로 영역을 선택한다."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QScreen
from PyQt6.QtWidgets import QWidget

from capture import CaptureRect

OVERLAY_COLOR = QColor(0, 0, 0, 110)
BORDER_COLOR = QColor(0, 170, 255)
HINT_TEXT = "드래그해서 문제 영역을 선택하세요  ·  ESC: 취소"


class _ScreenOverlay(QWidget):
    """모니터 하나를 덮는 오버레이. 선택 결과를 컨트롤러에 넘긴다."""

    def __init__(self, screen: QScreen, controller: "RegionSelector"):
        super().__init__(None)
        self._screen = screen
        self._controller = controller
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())

    # ---- 선택 영역 계산 ----
    def selection(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        a, b = self._origin, self._current
        return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(a.x() - b.x()), abs(a.y() - b.y()))

    def to_physical(self, local: QRect) -> CaptureRect:
        """위젯 로컬(논리 픽셀) 좌표 → mss용 물리 픽셀 좌표.

        Qt6는 각 모니터의 좌상단을 실제 픽셀 위치로 유지하고 크기만 배율로 나누므로
        물리 좌표 = 모니터 원점 + 로컬 좌표 × devicePixelRatio 이다.
        """
        geo = self._screen.geometry()
        dpr = self._screen.devicePixelRatio()
        return CaptureRect(
            left=round(geo.x() + local.x() * dpr),
            top=round(geo.y() + local.y() * dpr),
            width=round(local.width() * dpr),
            height=round(local.height() * dpr),
        )

    # ---- 이벤트 ----
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), OVERLAY_COLOR)

        sel = self.selection()
        if sel.isValid():
            # 선택 영역은 어둡게 하지 않는다. 알파 0이면 Windows에서 클릭이 통과되므로 1로 둔다.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(sel, QColor(0, 0, 0, 1))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(BORDER_COLOR, 2))
            painter.drawRect(sel.adjusted(0, 0, -1, -1))
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(sel.left(), max(sel.top() - 6, 14), f"{sel.width()} × {sel.height()}")
        else:
            painter.setPen(Qt.GlobalColor.white)
            font = painter.font()
            font.setPointSize(16)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, HINT_TEXT)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = self._current = event.position().toPoint()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._controller.cancel()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._current = event.position().toPoint()
        sel = self.selection()
        self._origin = self._current = None
        if sel.width() < 5 or sel.height() < 5:
            self.update()  # 클릭만 한 경우: 다시 드래그 대기
            return
        self._controller.finish(self.to_physical(sel))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._controller.cancel()


class RegionSelector(QObject):
    """모든 모니터에 오버레이를 띄우고 선택 결과를 시그널로 알린다."""

    selected = pyqtSignal(object)  # CaptureRect
    cancelled = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._overlays: list[_ScreenOverlay] = []

    def is_active(self) -> bool:
        return bool(self._overlays)

    def start(self) -> None:
        if self._overlays:
            return
        for screen in QGuiApplication.screens():
            overlay = _ScreenOverlay(screen, self)
            overlay.show()
            self._overlays.append(overlay)
        # 커서가 있는 모니터의 오버레이가 키보드(ESC) 입력을 받도록 한다
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for overlay in self._overlays:
            if overlay._screen is cursor_screen:
                overlay.activateWindow()
                overlay.raise_()
                overlay.setFocus()

    def _close_all(self) -> None:
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            overlay.close()

    def finish(self, rect: CaptureRect) -> None:
        self._close_all()
        self.selected.emit(rect)

    def cancel(self) -> None:
        if not self._overlays:
            return
        self._close_all()
        self.cancelled.emit()
