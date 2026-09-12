"""Dedicated control-key message thread and cancellation queue.

Legacy SendInput mode defaults to Raw Input; the application defaults to Interception.
Importing this module does not access the desktop.
"""
from collections import deque
from dataclasses import dataclass
import ctypes
import os
import threading
import time


@dataclass(frozen=True)
class HotkeyEvent:
    action: str
    label: str
    hwnd: int = 0
    when: float = 0.0
    source: str = "RegisterHotKey"


# id, semantic action, label, modifiers (MOD_NOREPEAT is added by the backend), vk
BINDINGS = (
    (1, "bind", "F6", 0, 0x75), (2, "toggle", "F8", 0, 0x77),
    (3, "stop", "F9", 0, 0x78),
    (4, "bind", "Ctrl+Alt+F6", 3, 0x75),
    (5, "toggle", "Ctrl+Alt+F8", 3, 0x77),
    (6, "stop", "Ctrl+Alt+F9", 3, 0x78),
)


class HotkeyInbox:
    def __init__(self):
        self._queue = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.failure = None

    @property
    def stop_requested(self):
        return self._stop.is_set()

    def publish(self, event):
        with self._lock:
            if event.action == "stop":
                self._stop.set()
            self._queue.append(event)
            self._wake.set()

    def fail(self, reason):
        with self._lock:
            self.failure = str(reason)
            self._wake.set()

    def check_health(self):
        if self.failure:
            raise RuntimeError(f"全局热键监听失败：{self.failure}")

    def interrupt_pending(self):
        with self._lock:
            return bool(self.failure or self._stop.is_set()
                        or any(event.action in {"toggle", "pause", "stop"} for event in self._queue))

    def drain(self):
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
            if not self.failure and not self._stop.is_set():
                self._wake.clear()
            return events

    def wait(self, seconds):
        self._wake.wait(seconds)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", _POINT), ("lPrivate", ctypes.c_uint32)]


class WinHotkeyBackend:
    description = "RegisterHotKey 系统全局热键"
    source = "RegisterHotKey"
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("全局热键仅支持 Windows")
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        self.u.RegisterHotKey.restype = ctypes.c_int
        self.u.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.u.UnregisterHotKey.restype = ctypes.c_int
        self.u.GetMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        self.u.GetMessageW.restype = ctypes.c_int
        self.u.PeekMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        self.u.PeekMessageW.restype = ctypes.c_int
        self.u.PostThreadMessageW.argtypes = [ctypes.c_uint32, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.u.PostThreadMessageW.restype = ctypes.c_int
        self.u.GetForegroundWindow.argtypes = []
        self.u.GetForegroundWindow.restype = ctypes.c_void_p
        self.k.GetCurrentThreadId.argtypes = []
        self.k.GetCurrentThreadId.restype = ctypes.c_uint32

    def prepare(self):
        msg = _MSG()
        self.u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # Create this thread's queue.
        return int(self.k.GetCurrentThreadId())

    def register(self, identifier, modifiers, vk):
        ctypes.set_last_error(0)
        if self.u.RegisterHotKey(None, identifier, modifiers | 0x4000, vk):
            return None
        return ctypes.get_last_error()

    def unregister(self, identifier):
        self.u.UnregisterHotKey(None, identifier)

    def receive(self):
        msg = _MSG()
        while True:
            result = self.u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == -1:
                raise RuntimeError(f"GetMessageW 错误 {ctypes.get_last_error()}")
            if result == 0:
                return None
            if msg.message == 0x0312:  # WM_HOTKEY
                return int(msg.wParam), int(self.u.GetForegroundWindow() or 0)

    def stop(self, thread_id):
        self.u.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT


class GlobalHotkeys(HotkeyInbox):
    def __init__(self, backend_factory=None):
        super().__init__()
        if backend_factory is None:
            from raw_hotkeys import RawInputBackend
            backend_factory = RawInputBackend
        self.backend_factory = backend_factory
        self._backend = None
        self._thread_id = None
        self._thread = None
        self._ready = threading.Event()
        self._closing = threading.Event()
        self.available = []
        self.registration_failures = []
        self.description = "全局控制键"

    def _worker(self):
        registered = {}
        try:
            self._backend = self.backend_factory()
            self.description = getattr(self._backend, "description", "全局控制键")
            self._thread_id = self._backend.prepare()
            for identifier, action, label, modifiers, vk in BINDINGS:
                error = self._backend.register(identifier, modifiers, vk)
                if error is None:
                    registered[identifier] = (action, label)
                    self.available.append(label)
                else:
                    self.registration_failures.append(f"{label}（错误 {error}，可能已被其他程序占用）")
            present = {action for action, _ in registered.values()}
            if present != {"bind", "toggle", "stop"}:
                raise RuntimeError("无法注册完整的绑定/暂停/停止快捷键。请关闭旧脚本。"
                                   + "；".join(self.registration_failures))
            self._ready.set()
            while not self._closing.is_set():
                item = self._backend.receive()
                if item is None:
                    if not self._closing.is_set():
                        raise RuntimeError("消息监听线程意外结束")
                    break
                identifier, hwnd = item
                if identifier in registered:
                    action, label = registered[identifier]
                    self.publish(HotkeyEvent(action, label, hwnd, time.monotonic(),
                                            getattr(self._backend, "source", "RegisterHotKey")))
        except Exception as exc:
            self.fail(exc)
        finally:
            if self._backend is not None:
                for identifier in registered:
                    self._backend.unregister(identifier)
                if hasattr(self._backend, "dispose"):
                    self._backend.dispose()
            self._ready.set()

    def start(self):
        if self._thread is not None:
            raise RuntimeError("热键监听已启动")
        self._thread = threading.Thread(target=self._worker, name="RockGlobalHotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            self.close()
            raise RuntimeError("全局热键注册超时")
        self.check_health()
        return self

    def close(self):
        self._closing.set()
        if self._backend is not None and self._thread_id is not None:
            self._backend.stop(self._thread_id)
        if self._thread is not None:
            self._thread.join(timeout=1)

    def __enter__(self):
        try:
            return self.start()
        except Exception:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()
