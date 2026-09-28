"""Quiz Study Helper 진입점: 컨트롤 창, 시스템 트레이, 전역 단축키."""
from __future__ import annotations

import sys
import threading
from html import escape
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from capture import CaptureError, CaptureRect, capture_region, image_to_png_bytes
from gemini_client import GeminiClient, GeminiError, InvalidApiKeyError, MissingApiKeyError, QuizResult, load_api_key
from history import History
from result_card import APP_NAME, CONFIDENCE_LABEL, ResultCard
from selector import RegionSelector

HOTKEY = "ctrl+shift+q"
HOTKEY_LABEL = "Ctrl+Shift+Q"
CAPTURE_DELAY_MS = 200  # 오버레이/창이 화면에서 사라질 때까지 기다리는 시간
ICON_PATH = Path(__file__).resolve().parent / "icon.ico"  # 바탕화면 바로가기와 같은 아이콘


def make_app_icon() -> QIcon:
    if ICON_PATH.is_file():
        return QIcon(str(ICON_PATH))
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#1a73e8"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("white"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(40)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "Q")
    p.end()
    return QIcon(pix)


# ---------------------------------------------------------------- 스레드 브리지
class HotkeyBridge(QObject):
    """keyboard 라이브러리 스레드 → Qt 메인 스레드로 이벤트를 넘긴다."""

    triggered = pyqtSignal()

    def register(self) -> str | None:
        """전역 단축키를 등록한다. 실패하면 오류 메시지를 반환한다."""
        try:
            import keyboard

            keyboard.add_hotkey(HOTKEY, self.triggered.emit)
        except Exception as exc:  # 권한 부족(리눅스 root 필요), 입력 장치 접근 불가 등
            detail = str(exc) or exc.__class__.__name__
            return f"키보드 후킹 권한이 없습니다 ({detail}). 관리자 권한으로 실행해 보세요"
        return None

    @staticmethod
    def unregister() -> None:
        try:
            import keyboard

            keyboard.unhook_all()
        except Exception:
            pass


class AnalyzeWorker(QObject):
    """Gemini 호출을 백그라운드 스레드에서 실행하고 결과를 시그널로 돌려준다."""

    succeeded = pyqtSignal(object)  # QuizResult
    failed = pyqtSignal(object)  # GeminiError

    def run(self, client: GeminiClient, png: bytes) -> None:
        threading.Thread(target=self._work, args=(client, png), daemon=True).start()

    def _work(self, client: GeminiClient, png: bytes) -> None:
        try:
            result = client.analyze(png)
        except GeminiError as exc:
            self.failed.emit(exc)
        except Exception as exc:  # 예상 못한 오류도 카드에 표시
            err = GeminiError(f"{exc.__class__.__name__}: {exc}")
            err.title = "알 수 없는 오류"
            self.failed.emit(err)
        else:
            self.succeeded.emit(result)


# ---------------------------------------------------------------- 컨트롤 창
class ControlWindow(QMainWindow):
    select_requested = pyqtSignal()
    clear_history_requested = pyqtSignal()

    def __init__(self, icon: QIcon):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.resize(480, 460)
        self.quitting = False
        self._tray_notice_shown = False
        self.tray: QSystemTrayIcon | None = None

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_home_tab(), "캡처")
        self.tabs.addTab(self._build_history_tab(), "히스토리")

        # 전역 단축키가 동작하지 않을 때를 대비해 창 안에서도 단축키를 받는다
        QShortcut(QKeySequence(HOTKEY_LABEL), self, activated=self.select_requested.emit)

    def _build_home_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"📘 {APP_NAME}")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        desc = QLabel("화면의 객관식 문제 영역을 선택하면 Gemini가 정답과 해설을 알려 줍니다.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #5f6368;")

        self.select_button = QPushButton(f"영역 선택  ({HOTKEY_LABEL})")
        self.select_button.setMinimumHeight(44)
        self.select_button.setStyleSheet(
            "QPushButton { background: #1a73e8; color: white; font-size: 14px; font-weight: bold;"
            " border: none; border-radius: 8px; }"
            "QPushButton:hover { background: #1765cc; }"
            "QPushButton:disabled { background: #9aa0a6; }"
        )
        self.select_button.clicked.connect(self.select_requested.emit)

        self.status_label = QLabel("대기 중")
        self.hotkey_label = QLabel()
        self.api_label = QLabel()
        for label in (self.status_label, self.hotkey_label, self.api_label):
            label.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.select_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        layout.addWidget(self.hotkey_label)
        layout.addWidget(self.api_label)
        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.history_list = QListWidget()
        self.history_detail = QTextBrowser()
        self.history_detail.setPlaceholderText("항목을 선택하면 문제 요약, 정답, 해설이 표시됩니다.")
        splitter.addWidget(self.history_list)
        splitter.addWidget(self.history_detail)
        splitter.setSizes([150, 260])
        self.history_list.currentItemChanged.connect(self._show_history_detail)

        buttons = QHBoxLayout()
        self.history_count = QLabel()
        clear_btn = QPushButton("기록 전체 삭제")
        clear_btn.clicked.connect(self._confirm_clear)
        buttons.addWidget(self.history_count)
        buttons.addStretch()
        buttons.addWidget(clear_btn)

        layout.addWidget(splitter)
        layout.addLayout(buttons)
        return page

    # ---- 상태 갱신
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_busy(self, busy: bool) -> None:
        self.select_button.setEnabled(not busy)

    def set_hotkey_status(self, error: str | None) -> None:
        if error is None:
            self.hotkey_label.setText(f"⌨ 전역 단축키 {HOTKEY_LABEL} 사용 가능")
        else:
            self.hotkey_label.setText(
                f"⚠ 전역 단축키 등록 실패: {error}\n  (창이 활성화된 상태에서는 {HOTKEY_LABEL}로 사용 가능)"
            )

    def set_api_status(self, ok: bool) -> None:
        self.api_label.setText(
            "🔑 GEMINI_API_KEY 설정됨" if ok else "⚠ GEMINI_API_KEY가 없습니다. .env 파일을 확인해 주세요."
        )

    def set_history(self, items: list[QuizResult]) -> None:
        self.history_list.clear()
        self.history_detail.clear()
        for result in items:
            flag = " ⚠" if result.needs_check else ""
            item = QListWidgetItem(f"[{result.created_at[5:16]}] {result.question_summary} → {result.answer}{flag}")
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setToolTip(result.question_summary)
            self.history_list.addItem(item)
        self.history_count.setText(f"최근 {len(items)}개 (최대 10개)")

    def _show_history_detail(self, item: QListWidgetItem | None) -> None:
        if item is None:
            self.history_detail.clear()
            return
        r: QuizResult = item.data(Qt.ItemDataRole.UserRole)
        conf_text, color = CONFIDENCE_LABEL.get(r.confidence, CONFIDENCE_LABEL["low"])
        warn = "<p style='color:#d93025;'><b>⚠ 직접 확인 필요</b></p>" if r.needs_check else ""
        self.history_detail.setHtml(
            f"{warn}"
            f"<p><b>문제 요약</b><br>{escape(r.question_summary)}</p>"
            f"<p><b>정답</b><br><span style='color:#1a73e8; font-size:15px;'><b>{escape(r.answer)}</b></span></p>"
            f"<p><b>해설</b><br>{escape(r.explanation)}</p>"
            f"<p><b>신뢰도</b> <span style='color:{color};'>{conf_text}</span>"
            f" &nbsp; <span style='color:#5f6368;'>{escape(r.created_at)}</span></p>"
        )

    def _confirm_clear(self) -> None:
        if self.history_list.count() == 0:
            return
        answer = QMessageBox.question(self, APP_NAME, "히스토리를 모두 삭제할까요?")
        if answer == QMessageBox.StandardButton.Yes:
            self.clear_history_requested.emit()

    def show_history_tab(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.bring_to_front()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # 창을 닫으면 트레이로 숨긴다 (종료는 트레이 메뉴에서)
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.quitting or self.tray is None:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_notice_shown:
            self.tray.showMessage(APP_NAME, "트레이에서 계속 실행 중입니다. 종료는 트레이 아이콘 메뉴에서 하세요.")
            self._tray_notice_shown = True


# ---------------------------------------------------------------- 앱 로직
class QuizHelperApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.icon = make_app_icon()
        self.history = History()
        self.card = ResultCard()
        self.selector = RegionSelector(self)
        self.worker = AnalyzeWorker()
        self.hotkeys = HotkeyBridge()
        self.client: GeminiClient | None = None
        self.busy = False
        self._window_was_visible = False

        self.window = ControlWindow(self.icon)
        self.window.select_requested.connect(self.start_selection)
        self.window.clear_history_requested.connect(self.clear_history)
        self.window.set_history(self.history.items)
        self.window.set_api_status(self._has_api_key())

        self.selector.selected.connect(self.on_region_selected)
        self.selector.cancelled.connect(self.on_selection_cancelled)
        self.worker.succeeded.connect(self.on_success)
        self.worker.failed.connect(self.on_failure)

        self.hotkeys.triggered.connect(self.start_selection)
        self.window.set_hotkey_status(self.hotkeys.register())

        self.tray = self._build_tray()
        self.window.tray = self.tray
        self.window.show()

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon, self)
        tray.setToolTip(f"{APP_NAME} — {HOTKEY_LABEL}로 영역 선택")
        menu = QMenu()
        title = QAction(APP_NAME, menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()
        menu.addAction(f"영역 선택 ({HOTKEY_LABEL})", self.start_selection)
        menu.addAction("창 열기", self.window.bring_to_front)
        menu.addAction("히스토리 보기", self.window.show_history_tab)
        menu.addSeparator()
        menu.addAction("종료", self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        self._tray_menu = menu  # 가비지 컬렉션 방지
        tray.show()
        return tray

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.window.bring_to_front()

    @staticmethod
    def _has_api_key() -> bool:
        try:
            load_api_key()
        except MissingApiKeyError:
            return False
        return True

    # ---- 흐름: 영역 선택 → 캡처 → 분석 → 표시
    def start_selection(self) -> None:
        if self.selector.is_active():
            return
        if self.busy:
            self.window.set_status("⏳ 이전 문제를 분석 중입니다. 잠시만 기다려 주세요.")
            return
        # 컨트롤 창과 이전 결과 카드가 캡처에 찍히지 않도록 숨긴다
        self._window_was_visible = self.window.isVisible() and not self.window.isMinimized()
        self.window.hide()
        self.card.hide()
        self.window.set_status("영역을 드래그해서 선택하세요 (ESC: 취소)")
        QTimer.singleShot(CAPTURE_DELAY_MS, self.selector.start)

    def on_selection_cancelled(self) -> None:
        self.window.set_status("선택이 취소되었습니다.")
        self._restore_window()

    def on_region_selected(self, rect: CaptureRect) -> None:
        # 오버레이가 화면에서 완전히 사라진 뒤 캡처한다
        QTimer.singleShot(CAPTURE_DELAY_MS, lambda: self._capture_and_analyze(rect))

    def _capture_and_analyze(self, rect: CaptureRect) -> None:
        try:
            png = image_to_png_bytes(capture_region(rect))
        except CaptureError as exc:
            self._restore_window()
            self.card.show_error("캡처 실패", str(exc))
            self.window.set_status(str(exc))
            return
        self._restore_window()

        if self.client is None:
            try:
                self.client = GeminiClient()
            except MissingApiKeyError as exc:
                self.window.set_api_status(False)
                self.card.show_error(exc.title, str(exc))
                self.window.set_status("API 키가 없습니다.")
                return
            self.window.set_api_status(True)

        self._set_busy(True)
        self.window.set_status("⏳ Gemini로 분석 중...")
        self.card.show_loading()
        self.worker.run(self.client, png)

    def on_success(self, result: QuizResult) -> None:
        self._set_busy(False)
        self.history.add(result)
        self.window.set_history(self.history.items)
        self.card.show_result(result)
        self.window.set_status(f"✅ 완료: {result.answer}")

    def on_failure(self, error: GeminiError) -> None:
        self._set_busy(False)
        if isinstance(error, InvalidApiKeyError):
            self.client = None  # .env를 고친 뒤 다시 시도하면 새 키로 클라이언트를 만든다
        self.card.show_error(error.title, str(error))
        self.window.set_status(f"❌ {error.title}")

    def clear_history(self) -> None:
        self.history.clear()
        self.window.set_history(self.history.items)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.window.set_busy(busy)

    def _restore_window(self) -> None:
        if self._window_was_visible:
            self.window.show()
            self._window_was_visible = False

    def quit(self) -> None:
        self.hotkeys.unregister()
        self.window.quitting = True
        if self.tray:
            self.tray.hide()
        self.app.quit()


def _set_windows_app_id() -> None:
    """작업 표시줄에 python 아이콘 대신 앱 아이콘이 보이도록 한다 (Windows 전용)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QuizStudyHelper")
    except Exception:
        pass


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # 창을 닫아도 트레이에서 계속 실행
    helper = QuizHelperApp(app)
    if helper.tray is None:
        # 트레이가 없는 환경에서는 창을 닫으면 종료한다
        app.setQuitOnLastWindowClosed(True)
    app.aboutToQuit.connect(helper.hotkeys.unregister)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
