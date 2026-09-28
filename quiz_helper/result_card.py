"""화면 우측 하단에 뜨는 결과 카드."""
from __future__ import annotations

from html import escape

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gemini_client import QuizResult

APP_NAME = "Quiz Study Helper"
CARD_WIDTH = 400
MARGIN = 20

CONFIDENCE_LABEL = {
    "high": ("높음", "#1e8e3e"),
    "medium": ("보통", "#e37400"),
    "low": ("낮음", "#d93025"),
}

STYLE = """
QFrame#card { background: #ffffff; border: 1px solid #c9ced6; border-radius: 12px; }
QLabel { color: #202124; font-size: 13px; }
QLabel#appName { color: #5f6368; font-size: 12px; font-weight: bold; }
QLabel#sectionTitle { color: #5f6368; font-size: 11px; font-weight: bold; }
QLabel#answer { font-size: 17px; font-weight: bold; color: #1a73e8; }
QLabel#warning { background: #fce8e6; color: #b3261e; border-radius: 6px; padding: 6px 8px; font-weight: bold; }
QLabel#muted { color: #5f6368; }
QLabel#errorTitle { color: #d93025; font-size: 15px; font-weight: bold; }
QPushButton#close { border: none; font-size: 15px; color: #5f6368; padding: 2px 6px; }
QPushButton#close:hover { background: #eceff1; border-radius: 4px; }
"""


class ResultCard(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{APP_NAME} - 결과")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(CARD_WIDTH)
        self._drag_offset: QPoint | None = None

        card = QFrame(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 12, 16)
        layout.setSpacing(6)

        header = QHBoxLayout()
        name = QLabel(f"📘 {APP_NAME}")
        name.setObjectName("appName")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("close")
        close_btn.setToolTip("닫기")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        header.addWidget(name)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        self._body = QVBoxLayout()
        self._body.setSpacing(4)
        layout.addLayout(self._body)

    # ---------------------------------------------------------- 상태 표시
    def show_loading(self) -> None:
        self._reset()
        self._add_label("⏳ 문제를 분석하고 있습니다...")
        self._present()

    def show_result(self, result: QuizResult) -> None:
        self._reset()
        if result.needs_check:
            self._add_label("⚠ 신뢰도 낮음 — 직접 확인 필요", "warning")

        self._add_section("정답")
        self._add_label(escape(result.answer), "answer")

        self._add_section("해설")
        self._add_label(escape(result.explanation))

        text, color = CONFIDENCE_LABEL.get(result.confidence, CONFIDENCE_LABEL["low"])
        self._add_section("신뢰도")
        self._add_label(f'<span style="color:{color}; font-weight:bold;">● {text}</span> ({result.confidence})')

        self._add_section("문제 요약")
        self._add_label(escape(result.question_summary), "muted")
        self._present()

    def show_error(self, title: str, message: str) -> None:
        self._reset()
        self._add_label(f"❌ {escape(title)}", "errorTitle")
        self._add_label(escape(message).replace("\n", "<br>"))
        self._present()

    # ---------------------------------------------------------- 내부
    def _reset(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            if widget := item.widget():
                widget.hide()
                widget.deleteLater()

    def _add_section(self, title: str) -> None:
        self._add_label(title, "sectionTitle").setContentsMargins(0, 6, 0, 0)

    def _add_label(self, html: str, name: str = "") -> QLabel:
        label = QLabel(html)
        label.setObjectName(name)  # 스타일시트가 적용되도록 표시 전에 지정
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.addWidget(label)
        label.show()  # 표시가 지연 예약되면 크기 계산에서 빠지므로 즉시 표시
        return label

    def _present(self) -> None:
        """내용 크기에 맞춰 조정하고 우측 하단에 띄운다."""
        # 새 라벨의 줄바꿈 높이가 반영되도록 레이아웃을 먼저 갱신한다
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - MARGIN, area.bottom() - self.height() - MARGIN)
        self.show()
        self.raise_()

    # 카드를 드래그해서 옮길 수 있게 한다
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
