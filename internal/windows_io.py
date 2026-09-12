"""Guarded Windows client-area capture and ordinary foreground input.

Importing this module does not inspect the desktop or require Windows.  Win32,
MSS and NumPy are loaded only when their functionality is used.  The module
never activates a window, sends background messages, or changes game memory.
"""

from __future__ import annotations

import ctypes
import math
import os
import time
from typing import Callable
from keymap import KEY_SCANS, KEY_VKS


REFERENCE_WIDTH = 2048
REFERENCE_HEIGHT = 1152
MIN_CLIENT_WIDTH = 640
MIN_CLIENT_HEIGHT = 360
ASPECT_TOLERANCE = 0.025


class InputGuardError(RuntimeError):
    """The bound window is unavailable or input is no longer authorized."""

    def __init__(self, message: str, input_sent: bool = False):
        super().__init__(message)
        # A successful key/button-down may already have advanced the game. Callers
        # must not replay that action solely because focus changed during release.
        self.input_sent = input_sent


def win32_error_text(code: int) -> str:
    if not code:
        return "错误 0：Windows 未提供具体错误原因"
    formatter = getattr(ctypes, "FormatError", None)
    detail = formatter(code).strip() if formatter else ""
    return f"错误 {code}：{detail}" if detail else f"错误 {code}"


# Fixed-width members also make importing these definitions harmless on Unix.
_DWORD = ctypes.c_uint32
_LONG = ctypes.c_int32
_WORD = ctypes.c_uint16
_ULONG_PTR = ctypes.c_size_t
_HWND = ctypes.c_void_p


class _POINT(ctypes.Structure):
    _fields_ = [("x", _LONG), ("y", _LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", _LONG), ("top", _LONG),
                ("right", _LONG), ("bottom", _LONG)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", _LONG), ("dy", _LONG), ("mouseData", _DWORD),
                ("dwFlags", _DWORD), ("time", _DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", _WORD), ("wScan", _WORD), ("dwFlags", _DWORD),
                ("time", _DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", _DWORD), ("wParamL", _WORD), ("wParamH", _WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", _DWORD), ("value", _INPUTUNION)]


class _Win32:
    def __init__(self) -> None:
        if os.name != "nt":
            raise InputGuardError("实时截图和输入仅支持 Windows；离线识别不受影响。")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        u = self.user32
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = _HWND
        for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
            method = getattr(u, name)
            method.argtypes = [_HWND]
            method.restype = ctypes.c_int
        u.GetClientRect.argtypes = [_HWND, ctypes.POINTER(_RECT)]
        u.GetClientRect.restype = ctypes.c_int
        u.ClientToScreen.argtypes = [_HWND, ctypes.POINTER(_POINT)]
        u.ClientToScreen.restype = ctypes.c_int
        u.GetWindowTextLengthW.argtypes = [_HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [_HWND, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_int16
        u.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        u.GetCursorPos.restype = ctypes.c_int
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
        u.SendInput.restype = ctypes.c_uint
        self.enum_callback = ctypes.WINFUNCTYPE(ctypes.c_int, _HWND, ctypes.c_ssize_t)
        u.EnumWindows.argtypes = [self.enum_callback, ctypes.c_ssize_t]
        u.EnumWindows.restype = ctypes.c_int
        self._enable_dpi_awareness()

    def _enable_dpi_awareness(self) -> None:
        """Prefer per-monitor v2; process awareness may already be established."""
        u = self.user32
        if hasattr(u, "SetProcessDpiAwarenessContext"):
            u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u.SetProcessDpiAwarenessContext.restype = ctypes.c_int
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        # Also set this worker thread: an embedding host may own the process setting.
        if hasattr(u, "SetThreadDpiAwarenessContext"):
            u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            if u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        try:
            shcore = ctypes.WinDLL("shcore", use_last_error=True)
            shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            shcore.SetProcessDpiAwareness.restype = ctypes.c_int32
            if shcore.SetProcessDpiAwareness(2) == 0:
                return
        except (OSError, AttributeError):
            pass
        if hasattr(u, "SetProcessDPIAware"):
            u.SetProcessDPIAware.argtypes = []
            u.SetProcessDPIAware.restype = ctypes.c_int
            u.SetProcessDPIAware()

    def send(self, event: _INPUT) -> None:
        ctypes.set_last_error(0)
        if self.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
            code = ctypes.get_last_error()
            raise InputGuardError(
                f"SendInput 未被接受（{win32_error_text(code)}）。请确认脚本与游戏的权限级别一致。"
            )


_api_instance: _Win32 | None = None


def _api() -> _Win32:
    global _api_instance
    if _api_instance is None:
        _api_instance = _Win32()
    return _api_instance


def enable_dpi_awareness() -> None:
    """Call before creating a GUI or obtaining display coordinates."""
    _api()._enable_dpi_awareness()


def _window_title(hwnd: int) -> str:
    u = _api().user32
    count = u.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(max(count + 1, 1))
    u.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def enumerate_windows() -> list[dict[str, object]]:
    """Return visible top-level windows as dictionaries with hwnd/title keys."""
    api = _api()
    windows: list[dict[str, object]] = []

    @api.enum_callback
    def collect(hwnd: int, _lparam: int) -> int:
        if api.user32.IsWindowVisible(hwnd):
            title = _window_title(hwnd)
            if title:
                windows.append({"hwnd": int(hwnd), "title": title})
        return 1

    if not api.user32.EnumWindows(collect, 0):
        raise InputGuardError("无法枚举窗口。")
    return windows


def is_key_down(vk: int) -> bool:
    """Read current physical key state; does not send input or consume an edge."""
    return bool(_api().user32.GetAsyncKeyState(int(vk)) & 0x8000)


def hotkey_pressed(vk: int) -> bool:
    """Alias for current held state. The caller must detect rising edges."""
    return is_key_down(vk)


def absolute_mouse_coordinates(x, y, desktop):
    """Map a physical pixel center into the entire virtual desktop (0..65535)."""
    left, top, width, height = desktop
    if width <= 0 or height <= 0 or not left <= x < left + width or not top <= y < top + height:
        raise InputGuardError(f"鼠标目标 ({x}, {y}) 不在有效桌面 {desktop} 内。")
    nx = int(((x - left) + 0.5) * 65536 / width)
    ny = int(((y - top) + 0.5) * 65536 / height)
    return min(65535, max(0, nx)), min(65535, max(0, ny))


class GameWindow:
    """Bind one exact HWND, with all actions guarded against focus changes.

    ``capture`` returns a native-resolution BGR ndarray of the client area.
    Keyboard and mouse events use the selected transport (Interception or SendInput).
    Mouse position is verified before clicking; SetCursorPos is not used.
    Set ``cancel_check`` to a callable returning True to interrupt pending input.
    """

    def __init__(self, hwnd: int):
        self.hwnd = int(hwnd)
        self.cancel_check: Callable[[], bool] = lambda: False
        self._closed = False
        self.keyboard_mode = "scan_code"
        self.key_hold_seconds = 0.15
        self.input_sender = None
        self.input_backend_name = "SendInput"
        self._captured_rect: tuple[int, int, int, int] | None = None
        u = _api().user32
        if not self.hwnd or not u.IsWindow(self.hwnd) or not u.IsWindowVisible(self.hwnd):
            raise InputGuardError("绑定窗口无效或不可见，请切换到游戏窗口后重新绑定。")

    @classmethod
    def foreground(cls) -> "GameWindow":
        return cls(int(_api().user32.GetForegroundWindow() or 0))

    @property
    def title(self) -> str:
        return _window_title(self.hwnd)

    def rect(self) -> tuple[int, int, int, int]:
        """Return physical desktop client coordinates (left, top, width, height)."""
        u = _api().user32
        client = _RECT()
        origin = _POINT(0, 0)
        if (not u.IsWindow(self.hwnd)
                or not u.GetClientRect(self.hwnd, ctypes.byref(client))
                or not u.ClientToScreen(self.hwnd, ctypes.byref(origin))):
            raise InputGuardError("无法取得游戏客户区，窗口可能已经关闭。")
        return (int(origin.x), int(origin.y),
                int(client.right - client.left), int(client.bottom - client.top))

    def readiness_problem(self, require_foreground: bool = True) -> str | None:
        """Explain why this binding cannot be used, or return ``None``.

        Background window selection can skip only the foreground check. Every
        capture/input guard still requires the exact bound window in front.
        Check geometry first so a console or minimized game gives an actionable
        reason instead of a generic focus/size failure.
        """
        if self._closed:
            return "窗口绑定已关闭，请重新绑定。"
        u = _api().user32
        if not u.IsWindow(self.hwnd):
            return f"绑定窗口已不存在（HWND={self.hwnd}），请重新绑定。"
        if not u.IsWindowVisible(self.hwnd):
            return "绑定窗口不可见，请显示游戏窗口。"
        if u.IsIconic(self.hwnd):
            return "绑定窗口已最小化，请还原游戏窗口。"
        try:
            _, _, width, height = self.rect()
        except InputGuardError as exc:
            return str(exc)
        if width < MIN_CLIENT_WIDTH or height < MIN_CLIENT_HEIGHT:
            return (f"游戏客户区为 {width}×{height}，至少需要 "
                    f"{MIN_CLIENT_WIDTH}×{MIN_CLIENT_HEIGHT}；请检查所选窗口。")
        aspect_error = abs((width / height) / (16 / 9) - 1)
        if aspect_error > ASPECT_TOLERANCE:
            return (f"游戏客户区为 {width}×{height}，与 16:9 的比例偏差为 "
                    f"{aspect_error:.1%}（允许 {ASPECT_TOLERANCE:.1%}）；"
                    "请将游戏画面设为 16:9。")
        foreground = int(u.GetForegroundWindow() or 0)
        if require_foreground and foreground != self.hwnd:
            return (f"游戏未处于前台（绑定 HWND={self.hwnd}，前台 HWND={foreground}）；"
                    "请切回游戏窗口。")
        return None

    def is_ready(self) -> bool:
        return self.readiness_problem() is None

    def _guard(self, check_frame: bool = True) -> None:
        if self.cancel_check():
            raise InputGuardError("操作已暂停或停止。")
        problem = self.readiness_problem()
        if problem is not None:
            raise InputGuardError(problem)
        if check_frame and self._captured_rect is not None and self.rect() != self._captured_rect:
            raise InputGuardError("窗口位置或大小已不同于识别截图；已取消本次输入，请重新截图。")

    def close(self) -> None:
        """Invalidate the binding. MSS capture handles are already closed per frame."""
        self._closed = True
        self._captured_rect = None

    def capture(self):
        """Read the active client area. No window is activated by this method."""
        self._guard(check_frame=False)
        import mss
        import numpy as np

        before = self.rect()
        left, top, width, height = before
        with mss.mss() as screen:
            shot = screen.grab({"left": left, "top": top,
                                "width": width, "height": height})
        self._guard(check_frame=False)
        if self.rect() != before:
            raise InputGuardError("截图期间窗口位置发生变化，请等待窗口稳定。")
        self._captured_rect = before
        return np.asarray(shot, dtype=np.uint8)[:, :, :3].copy()

    def _hold(self, seconds: float = 0.05) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._guard()
            time.sleep(min(0.01, max(0, end - time.monotonic())))

    def _send(self, event):
        if self.input_sender is not None:
            self.input_sender.send(event)
        else:
            _api().send(event)

    def _pulse(self, down: _INPUT, up: _INPUT, hold_seconds=0.05) -> None:
        self._guard()
        sent = False
        try:
            self._send(down)
            sent = True
            self._hold(hold_seconds)
        except InputGuardError as exc:
            exc.input_sent = exc.input_sent or sent
            raise
        finally:
            # Release even after a failed down or a foreground/cancel guard.
            # This release intentionally ignores the foreground guard so keys
            # are not left stuck after an interruption.
            try:
                self._send(up)
            except InputGuardError as exc:
                exc.input_sent = exc.input_sent or sent
                raise

    def _cursor_position(self):
        point = _POINT()
        if not _api().user32.GetCursorPos(ctypes.byref(point)):
            code = getattr(ctypes, "get_last_error", lambda: 0)()
            raise InputGuardError(f"GetCursorPos 读取鼠标位置失败（{win32_error_text(code)}）；未点击。")
        return int(point.x), int(point.y)

    def _verify_cursor(self, target):
        actual = self._cursor_position()
        if max(abs(actual[0] - target[0]), abs(actual[1] - target[1])) > 2:
            raise InputGuardError(f"鼠标未到目标位置：目标 {target}，实际 {actual}；未点击。")

    def click(self, x, y):
        """Move with the selected transport, verify position, then click left."""
        if (not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                or not math.isfinite(x) or not math.isfinite(y)
                or not 0 <= x < REFERENCE_WIDTH or not 0 <= y < REFERENCE_HEIGHT):
            raise ValueError("点击坐标必须位于2048×1152参考图内。")
        self._guard()
        before = self.rect()
        left, top, width, height = before
        target = (left + int(x * width / REFERENCE_WIDTH), top + int(y * height / REFERENCE_HEIGHT))
        api = _api()
        desktop = tuple(api.user32.GetSystemMetrics(metric) for metric in (76, 77, 78, 79))
        nx, ny = absolute_mouse_coordinates(*target, desktop)
        move = _INPUT(type=0, mi=_MOUSEINPUT(dx=nx, dy=ny, dwFlags=0x8000 | 0x4000 | 0x0001))
        self._guard()
        self._send(move)
        # A move is not a click: failures before button-down keep input_sent=False.
        deadline = time.monotonic() + 0.3
        while True:
            self._guard()
            actual = self._cursor_position()
            if max(abs(actual[0] - target[0]), abs(actual[1] - target[1])) <= 2:
                break
            if time.monotonic() >= deadline:
                raise InputGuardError(f"{self.input_backend_name} 鼠标定位未生效：目标 {target}，实际 {actual}；未点击。")
            time.sleep(0.01)
        self._guard()
        if self.rect() != before:
            raise InputGuardError("鼠标定位后窗口位置发生变化，未点击。")
        self._verify_cursor(target)
        down = _INPUT(type=0, mi=_MOUSEINPUT(dwFlags=0x0002))
        up = _INPUT(type=0, mi=_MOUSEINPUT(dwFlags=0x0004))
        self._pulse(down, up)

    def press(self, key: str) -> None:
        """Send one bounded key press through the selected transport."""
        normalized = key.lower()
        if normalized not in KEY_SCANS:
            raise ValueError(f"不支持按键 {key!r}；支持：{', '.join(KEY_SCANS)}")
        if self.keyboard_mode not in {"scan_code", "virtual_key"}:
            raise ValueError("keyboard_mode 必须为 scan_code 或 virtual_key")
        if (not isinstance(self.key_hold_seconds, (float, int))
                or not math.isfinite(self.key_hold_seconds) or not 0.03 <= self.key_hold_seconds <= 1):
            raise ValueError("key_hold_seconds 必须为 0.03 到 1 秒")
        self._guard()
        scan, extended = KEY_SCANS[normalized]
        by_scan = self.keyboard_mode == "scan_code"
        flags = (0x0008 if by_scan else 0) | (0x0001 if extended else 0)
        vk = 0 if by_scan else KEY_VKS[normalized]
        down = _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags))
        up = _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags | 0x0002))
        self._pulse(down, up, self.key_hold_seconds)

    def press_sequence(self, keys, interval=0.2):
        sequence = tuple(keys)
        if not sequence or any(key not in KEY_SCANS for key in sequence):
            raise ValueError("按键序列为空或包含不支持的按键")
        if not 0 < interval <= 10:
            raise ValueError("按键间隔必须在0到10秒之间")
        sent = False
        try:
            for index, key in enumerate(sequence):
                self.press(key)
                sent = True
                if index < len(sequence) - 1:
                    self._hold(interval)
        except InputGuardError as exc:
            exc.input_sent = exc.input_sent or sent
            raise
