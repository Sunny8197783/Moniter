"""텍스트를 드래그해서 선택하면 선택한 문장을 가져온다 (드래그 자동 풀이).

동작 순서
1. 전역 마우스 훅(pynput)으로 왼쪽 버튼 드래그가 끝나는 것을 감지한다.
2. 위험한 프로그램(터미널 등)이 아니면 Ctrl+C를 대신 눌러 선택한 텍스트를 복사한다.
3. 클립보드에서 텍스트를 읽은 뒤 원래 클립보드 내용을 되돌려 놓는다.
4. 객관식 문제처럼 보이는 텍스트만 text_selected 시그널로 알린다.
"""
from __future__ import annotations

import os
import re
import sys
import time

from PyQt6.QtCore import QMimeData, QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

MIN_DRAG_PX = 12  # 이보다 짧게 움직이면 클릭으로 본다
SETTLE_MS = 120  # 마우스를 놓은 뒤 선택이 확정될 때까지 기다리는 시간
COPY_TIMEOUT_MS = 700  # Ctrl+C 후 클립보드가 바뀌기를 기다리는 최대 시간
POLL_MS = 20
MIN_TEXT_LEN = 8
MAX_TEXT_LEN = 5000

# Ctrl+C가 복사가 아닌 다른 동작(중지 신호, 파일 복사, 원격 전송 등)을 하는 프로그램은 건너뛴다
DENIED_PROCESSES = {
    # 터미널: 선택이 없으면 Ctrl+C가 실행 중인 프로그램을 중지시킨다
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe",
    "mintty.exe", "putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe", "conemu.exe", "conemu64.exe",
    "tabby.exe", "mobaxterm.exe", "xshell.exe", "securecrt.exe", "hyper.exe",
    # 탐색기/바탕화면: 파일이 복사된다
    "explorer.exe",
    # 엑셀: 셀 복사 표시(점선)가 남는다
    "excel.exe",
    # 원격 데스크톱·가상 머신: 키 입력이 다른 컴퓨터로 간다
    "mstsc.exe", "vmconnect.exe", "vmware.exe", "virtualboxvm.exe", "anydesk.exe", "teamviewer.exe",
}
DENIED_WINDOW_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}

# ---------------------------------------------------------------- 객관식 판별
_CIRCLED_RE = re.compile("[①-⑳❶-❿㉠-㉻]")  # ①~⑳ ❶~❿ ㉠~㉻
_MARKER_RES = (
    re.compile(r"(?:^|\s)\(?([1-9])\s*[).](?!\d)"),  # 1) 1. (1)
    re.compile(r"(?:^|\s)\(?([A-Ea-e])\s*[).]"),  # A. a) (A)
    re.compile(r"(?:^|\s)\(?([가나다라마ㄱㄴㄷㄹㅁ])\s*[).]"),  # 가. ㄱ) (가)
)
_QUESTION_WORDS_RE = re.compile(
    r"고르(시오|세요|면)|고른 것은|옳은 것은|옳지 않은 것은|알맞은 것은|적절한 것은|적절하지 않은 것은|"
    r"해당하는 것은|아닌 것은|which of the following|choose the|select the (best|correct)",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    """공백·줄바꿈 차이를 없앤 비교용 문자열."""
    return re.sub(r"\s+", " ", text).strip()


def looks_like_multiple_choice(text: str) -> bool:
    """보기 번호가 두 개 이상 있거나 '고르시오' 같은 문제 표현이 있으면 객관식으로 본다."""
    text = text.strip()
    if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
        return False
    if len(set(_CIRCLED_RE.findall(text))) >= 2:
        return True
    if any(len(set(pattern.findall(text))) >= 2 for pattern in _MARKER_RES):
        return True
    return bool(_QUESTION_WORDS_RE.search(text))


# ---------------------------------------------------------------- 클립보드
def clone_mime_data(source: QMimeData | None) -> QMimeData:
    """클립보드 내용을 되돌려 놓기 위해 모든 형식을 복사해 둔다."""
    backup = QMimeData()
    if source is None:
        return backup
    for fmt in source.formats():
        if fmt == "application/x-qt-image":
            continue  # 이미지는 아래에서 따로 복사한다
        data = source.data(fmt)
        if data is not None and not data.isEmpty():
            backup.setData(fmt, data)
    if source.hasImage():
        backup.setImageData(source.imageData())
    return backup


# ---------------------------------------------------------------- Windows 도우미
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _GA_ROOT = 2

    def _window_pid(hwnd) -> int:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _process_name(pid: int) -> str:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            _kernel32.CloseHandle(handle)

    def _class_name(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def foreground_app() -> tuple[int, str, str]:
        """(프로세스 ID, 실행 파일 이름, 창 클래스)"""
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return 0, "", ""
        pid = _window_pid(hwnd)
        return pid, _process_name(pid), _class_name(hwnd)

    def window_pid_at(x: int, y: int) -> int:
        hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not hwnd:
            return 0
        root = _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd
        return _window_pid(root)

else:  # 테스트용 (리눅스 등): 다른 프로그램 정보는 알 수 없으므로 우리 창 여부만 Qt로 판단한다

    def foreground_app() -> tuple[int, str, str]:
        return 0, "", ""

    def window_pid_at(x: int, y: int) -> int:
        return os.getpid() if QApplication.topLevelAt(QPoint(int(x), int(y))) is not None else 0


# ---------------------------------------------------------------- 감시기
class DragTextWatcher(QObject):
    """드래그로 선택한 텍스트를 가져와 객관식 문제이면 알린다."""

    text_selected = pyqtSignal(str)
    skipped = pyqtSignal(str)  # 사용자에게 알릴 만한 건너뜀 사유 (상태 표시용)
    _drag_finished = pyqtSignal(int, int, int, int)  # 마우스 훅 스레드 → 메인 스레드

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._listener = None
        self._keyboard = None
        self._press: tuple[int, int] | None = None
        self._copying = False
        self._paused = False
        self._clip_changes = 0
        self._backup: QMimeData | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll_clipboard)
        self._copy_started = 0.0
        self._ignore_clipboard_until = 0.0  # 우리가 되돌려 놓은 클립보드의 변경 알림은 무시한다
        self._drag_finished.connect(self._on_drag_finished)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

    # ---- 켜기/끄기
    @property
    def running(self) -> bool:
        return self._listener is not None

    def start(self) -> str | None:
        """감시를 시작한다. 실패하면 사용자에게 보여 줄 오류 메시지를 반환한다."""
        if self._listener is not None:
            return None
        try:
            from pynput import keyboard, mouse
        except Exception as exc:  # 미설치, 디스플레이 없음 등
            return f"pynput을 불러올 수 없습니다 ({exc}). pip install -r requirements.txt를 다시 실행해 주세요."
        try:
            listener = mouse.Listener(on_click=self._on_click)
            listener.daemon = True
            listener.start()
            self._keyboard = keyboard.Controller()
        except Exception as exc:
            return f"마우스 감시를 시작할 수 없습니다: {exc}"
        self._listener = listener
        return None

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def set_paused(self, paused: bool) -> None:
        """영역 선택 중처럼 잠시 무시해야 할 때 사용한다."""
        self._paused = paused

    # ---- 마우스 훅 스레드 (빠르게 반환해야 한다)
    def _on_click(self, x, y, button, pressed, injected=False) -> None:
        if getattr(button, "name", "") != "left":
            return
        if pressed:
            self._press = (int(x), int(y))
            return
        press, self._press = self._press, None
        if press is None:
            return
        if abs(int(x) - press[0]) + abs(int(y) - press[1]) >= MIN_DRAG_PX:
            self._drag_finished.emit(press[0], press[1], int(x), int(y))

    # ---- 메인 스레드
    def _on_drag_finished(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if self._paused or self._copying or self._listener is None:
            return
        own_pid = os.getpid()
        if window_pid_at(x1, y1) == own_pid or window_pid_at(x2, y2) == own_pid:
            return  # 우리 앱 창(결과 카드, 영역 선택 등) 안에서의 드래그
        pid, process, window_class = foreground_app()
        if pid == own_pid or process in DENIED_PROCESSES or window_class in DENIED_WINDOW_CLASSES:
            return
        QTimer.singleShot(SETTLE_MS, self._copy_selection)

    def _copy_selection(self) -> None:
        if self._copying or self._keyboard is None:
            return
        # Shift/Ctrl/Alt를 누른 채라면 Ctrl+C가 다른 단축키가 된다 (예: Chrome의 Ctrl+Shift+C)
        if QGuiApplication.queryKeyboardModifiers() != Qt.KeyboardModifier.NoModifier:
            return
        clipboard = QApplication.clipboard()
        self._backup = clone_mime_data(clipboard.mimeData())
        self._clip_changes = 0
        self._copying = True
        self._copy_started = time.monotonic()
        try:
            self._send_ctrl_c()
        except Exception:
            self._finish_copy(None)
            return
        self._poll_timer.start()

    def _send_ctrl_c(self) -> None:
        from pynput.keyboard import Key, KeyCode

        # Windows에서는 한/영 상태와 상관없이 C 키(가상 키 0x43)를 누른다
        c_key = KeyCode.from_vk(0x43) if sys.platform == "win32" else KeyCode.from_char("c")
        with self._keyboard.pressed(Key.ctrl):
            self._keyboard.press(c_key)
            self._keyboard.release(c_key)

    def _on_clipboard_changed(self) -> None:
        if self._copying and time.monotonic() >= self._ignore_clipboard_until:
            self._clip_changes += 1

    def _poll_clipboard(self) -> None:
        if self._clip_changes > 0:
            self._finish_copy(QApplication.clipboard().text())
        elif (time.monotonic() - self._copy_started) * 1000 >= COPY_TIMEOUT_MS:
            self._finish_copy(None)  # 선택된 텍스트가 없어서 복사되지 않음

    def _finish_copy(self, text: str | None) -> None:
        self._poll_timer.stop()
        backup, self._backup = self._backup, None
        if text is not None and backup is not None:
            self._restore_clipboard(backup)
        self._copying = False
        if not text or not text.strip():
            return
        if looks_like_multiple_choice(text):
            self.text_selected.emit(text.strip())
        elif len(text.strip()) >= MIN_TEXT_LEN:
            self.skipped.emit("선택한 텍스트가 객관식 문제로 보이지 않아 건너뛰었습니다 (문제와 보기를 함께 선택해 주세요).")

    def _restore_clipboard(self, backup: QMimeData) -> None:
        self._ignore_clipboard_until = time.monotonic() + 0.1
        clipboard = QApplication.clipboard()
        if backup.formats():
            clipboard.setMimeData(backup)
        else:
            clipboard.clear()
