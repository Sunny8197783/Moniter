"""Quiz Study Helper 진입점: 컨트롤 창, 시스템 트레이, 전역 단축키."""
from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from html import escape
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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
from gemini_client import GeminiClient, GeminiError, InvalidApiKeyError, QuizResult, load_api_key
from history import History
from result_card import APP_NAME, CONFIDENCE_LABEL, ResultCard
from selector import RegionSelector
from settings import Settings
from text_select import DragTextWatcher, normalize_text

HOTKEY = "ctrl+shift+q"
HOTKEY_LABEL = "Ctrl+Shift+Q"
CAPTURE_DELAY_MS = 200  # 오버레이/창이 화면에서 사라질 때까지 기다리는 시간
TEXT_CACHE_SIZE = 30  # 같은 문제를 다시 드래그하면 API를 다시 부르지 않도록 기억하는 개수
DRAG_ON_TEXT = "켜짐 — 문제와 보기를 마우스로 드래그해 선택하면 자동으로 답이 뜹니다."
DRAG_OFF_TEXT = "꺼짐 — 영역 선택만 사용합니다."
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
    """Gemini 호출을 백그라운드 스레드에서 실행하고 결과를 요청 번호와 함께 시그널로 돌려준다."""

    succeeded = pyqtSignal(int, object)  # 요청 번호, QuizResult
    failed = pyqtSignal(int, object)  # 요청 번호, GeminiError
    progress = pyqtSignal(int, str)  # 요청 번호, 재시도 중 안내 문구

    def run(self, request_id: int, job: Callable[[Callable[[str], None]], QuizResult]) -> None:
        """job(progress)를 백그라운드에서 실행한다."""
        threading.Thread(target=self._work, args=(request_id, job), daemon=True).start()

    def _work(self, request_id: int, job: Callable[[Callable[[str], None]], QuizResult]) -> None:
        try:
            result = job(lambda message: self.progress.emit(request_id, message))
        except GeminiError as exc:
            self.failed.emit(request_id, exc)
        except Exception as exc:  # 예상 못한 오류도 카드에 표시
            err = GeminiError(f"{exc.__class__.__name__}: {exc}")
            err.title = "알 수 없는 오류"
            self.failed.emit(request_id, err)
        else:
            self.succeeded.emit(request_id, result)


# ---------------------------------------------------------------- 컨트롤 창
class ControlWindow(QMainWindow):
    select_requested = pyqtSignal()
    clear_history_requested = pyqtSignal()
    drag_mode_toggled = pyqtSignal(bool)

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
        desc = QLabel(
            "화면의 객관식 문제를 영역 선택으로 캡처하거나, 문제 텍스트를 드래그해 선택하면 "
            "Gemini가 정답과 해설을 알려 줍니다."
        )
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

        self.drag_checkbox = QCheckBox("✍ 텍스트 드래그로 자동 풀이")
        self.drag_checkbox.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.drag_checkbox.toggled.connect(self.drag_mode_toggled.emit)
        self.drag_label = QLabel()
        self.drag_label.setStyleSheet("color: #5f6368; margin-left: 22px;")

        self.status_label = QLabel("대기 중")
        self.hotkey_label = QLabel()
        self.api_label = QLabel()
        for label in (self.drag_label, self.status_label, self.hotkey_label, self.api_label):
            label.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.select_button)
        layout.addWidget(self.drag_checkbox)
        layout.addWidget(self.drag_label)
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

    def set_drag_mode(self, enabled: bool, description: str) -> None:
        self.drag_checkbox.blockSignals(True)  # 코드에서 바꿀 때는 toggled 시그널을 다시 보내지 않는다
        self.drag_checkbox.setChecked(enabled)
        self.drag_checkbox.blockSignals(False)
        self.drag_label.setText(description)

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
        self.settings = Settings()
        self.card = ResultCard()
        self.selector = RegionSelector(self)
        self.worker = AnalyzeWorker()
        self.hotkeys = HotkeyBridge()
        self.drag_watcher = DragTextWatcher(self)
        self.client: GeminiClient | None = None
        self._window_was_visible = False

        # 요청 번호: 여러 문제를 연달아 보내도 카드에는 가장 최근 요청의 결과만 보여 준다
        self._request_seq = 0
        self._shown_request = 0
        self._pending_text: dict[int, str] = {}  # 요청 번호 → 드래그한 텍스트(비교용)
        self._text_cache: OrderedDict[str, QuizResult] = OrderedDict()
        self._shown_text: str | None = None  # 카드에 떠 있는 결과의 드래그 텍스트

        self.window = ControlWindow(self.icon)
        self.window.select_requested.connect(self.start_selection)
        self.window.clear_history_requested.connect(self.clear_history)
        self.window.drag_mode_toggled.connect(self.set_drag_mode)
        self.window.set_history(self.history.items)
        self.window.set_api_status(self._has_api_key())

        self.selector.selected.connect(self.on_region_selected)
        self.selector.cancelled.connect(self.on_selection_cancelled)
        self.worker.succeeded.connect(self.on_success)
        self.worker.failed.connect(self.on_failure)
        self.worker.progress.connect(self.on_progress)
        self.drag_watcher.text_selected.connect(self.on_text_selected)
        self.drag_watcher.skipped.connect(self.window.set_status)

        self.hotkeys.triggered.connect(self.start_selection)
        self.window.set_hotkey_status(self.hotkeys.register())

        self.tray = self._build_tray()
        self.window.tray = self.tray
        self.set_drag_mode(bool(self.settings.get("drag_auto", True)), save=False)
        self.window.show()

    def _build_tray(self) -> QSystemTrayIcon | None:
        self._drag_action: QAction | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon, self)
        tray.setToolTip(f"{APP_NAME} — {HOTKEY_LABEL}로 영역 선택, 텍스트 드래그로 자동 풀이")
        menu = QMenu()
        title = QAction(APP_NAME, menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()
        menu.addAction(f"영역 선택 ({HOTKEY_LABEL})", self.start_selection)
        self._drag_action = QAction("텍스트 드래그로 자동 풀이", menu)
        self._drag_action.setCheckable(True)
        self._drag_action.toggled.connect(self.set_drag_mode)
        menu.addAction(self._drag_action)
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
        except GeminiError:
            return False
        return True

    def _ensure_client(self) -> GeminiClient | None:
        """Gemini 클라이언트를 준비한다. 키 문제가 있으면 카드에 알리고 None을 반환한다."""
        if self.client is None:
            try:
                self.client = GeminiClient()
            except GeminiError as exc:  # 키 없음, 키에 잘못된 문자 등
                self.window.set_api_status(False)
                self.card.show_error(exc.title, str(exc))
                self.window.set_status(f"❌ {exc.title}")
                return None
            self.window.set_api_status(True)
        return self.client

    # ---- 텍스트 드래그 자동 풀이
    def set_drag_mode(self, enabled: bool, save: bool = True) -> None:
        if save:
            self.settings.set("drag_auto", enabled)
        description = DRAG_OFF_TEXT
        if enabled:
            error = self.drag_watcher.start()
            if error:
                enabled, description = False, f"⚠ {error}"
            else:
                description = DRAG_ON_TEXT
        else:
            self.drag_watcher.stop()
        self.window.set_drag_mode(enabled, description)
        if self._drag_action is not None:
            self._drag_action.blockSignals(True)
            self._drag_action.setChecked(enabled)
            self._drag_action.blockSignals(False)

    def on_text_selected(self, text: str) -> None:
        if self.selector.is_active():
            return
        key = normalize_text(text)
        if key in self._pending_text.values():
            return  # 같은 문제를 이미 분석 중
        if key == self._shown_text and self.card.isVisible():
            return  # 같은 문제의 결과가 이미 떠 있음 (스크롤바를 끄는 등 선택이 그대로 남은 경우)
        cached = self._text_cache.get(key)
        if cached is not None:
            self._text_cache.move_to_end(key)
            self._request_seq += 1  # 진행 중인 이전 요청의 결과가 이 카드를 덮지 않도록 한다
            self._shown_request = self._request_seq
            self._shown_text = key
            self.card.show_result(cached, note="📋 이미 푼 문제입니다 (저장된 결과)")
            self.window.set_status(f"✅ 저장된 결과: {cached.answer}")
            return
        client = self._ensure_client()
        if client is None:
            return
        self._shown_text = key
        self._start_job(lambda progress: client.analyze_text(text, progress=progress), text_key=key)

    # ---- 흐름: 영역 선택 → 캡처 → 분석 → 표시
    def start_selection(self) -> None:
        if self.selector.is_active():
            return
        self.drag_watcher.set_paused(True)
        # 컨트롤 창과 이전 결과 카드가 캡처에 찍히지 않도록 숨긴다
        self._window_was_visible = self.window.isVisible() and not self.window.isMinimized()
        self.window.hide()
        self.card.hide()
        self.window.set_status("영역을 드래그해서 선택하세요 (ESC: 취소)")
        QTimer.singleShot(CAPTURE_DELAY_MS, self.selector.start)

    def on_selection_cancelled(self) -> None:
        self.drag_watcher.set_paused(False)
        self.window.set_status("선택이 취소되었습니다.")
        self._restore_window()

    def on_region_selected(self, rect: CaptureRect) -> None:
        # 오버레이가 화면에서 완전히 사라진 뒤 캡처한다
        QTimer.singleShot(CAPTURE_DELAY_MS, lambda: self._capture_and_analyze(rect))

    def _capture_and_analyze(self, rect: CaptureRect) -> None:
        self.drag_watcher.set_paused(False)
        try:
            png = image_to_png_bytes(capture_region(rect))
        except CaptureError as exc:
            self._restore_window()
            self.card.show_error("캡처 실패", str(exc))
            self.window.set_status(str(exc))
            return
        self._restore_window()

        client = self._ensure_client()
        if client is None:
            return
        self._shown_text = None
        self._start_job(lambda progress: client.analyze(png, progress=progress))

    def _start_job(self, job: Callable[[Callable[[str], None]], QuizResult], text_key: str | None = None) -> None:
        self._request_seq += 1
        request_id = self._request_seq
        self._shown_request = request_id
        if text_key is not None:
            self._pending_text[request_id] = text_key
        self.window.set_status("⏳ Gemini로 분석 중...")
        self.card.show_loading()
        self.worker.run(request_id, job)

    def on_success(self, request_id: int, result: QuizResult) -> None:
        text_key = self._pending_text.pop(request_id, None)
        self.history.add(result)
        self.window.set_history(self.history.items)
        if text_key is not None:
            self._text_cache[text_key] = result
            while len(self._text_cache) > TEXT_CACHE_SIZE:
                self._text_cache.popitem(last=False)
        if request_id != self._shown_request:
            return  # 그사이 다른 문제를 요청했으므로 카드는 그대로 둔다 (히스토리에는 저장됨)
        self.card.show_result(result)
        status = f"✅ 완료: {result.answer}"
        if self.client is not None:
            used = result.model or self.client.model
            status += f"\n모델: {used}"
            if used != self.client.model:
                status += f" ('{self.client.model}' 서버 혼잡으로 우회)"
            elif self.client.model_switched_from:
                status += f" (설정한 '{self.client.model_switched_from}'을 쓸 수 없어 자동 선택)"
        self.window.set_status(status)

    def on_progress(self, request_id: int, message: str) -> None:
        if request_id == self._shown_request:
            self.card.show_loading(message)
            self.window.set_status(f"⏳ {message}")

    def on_failure(self, request_id: int, error: GeminiError) -> None:
        text_key = self._pending_text.pop(request_id, None)
        if isinstance(error, InvalidApiKeyError):
            self.client = None  # .env를 고친 뒤 다시 시도하면 새 키로 클라이언트를 만든다
        if request_id != self._shown_request:
            return
        if text_key is not None and text_key == self._shown_text:
            self._shown_text = None  # 같은 문제를 다시 드래그하면 재시도할 수 있게 한다
        self.card.show_error(error.title, str(error))
        self.window.set_status(f"❌ {error.title}")

    def clear_history(self) -> None:
        self.history.clear()
        self.window.set_history(self.history.items)

    def _restore_window(self) -> None:
        if self._window_was_visible:
            self.window.show()
            self._window_was_visible = False

    def quit(self) -> None:
        self.hotkeys.unregister()
        self.drag_watcher.stop()
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
