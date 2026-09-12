"""Background control keys through documented Windows Raw Input.

The hidden message window uses INPUTSINK, without NOLEGACY or input suppression.
Only F6/F8/F9 and modifier state are processed; other keys are discarded and
never stored, translated to text, or logged. No device driver or hook is used.
"""
import ctypes
from collections import deque
import os

from global_hotkeys import WinHotkeyBackend, _MSG


class RawKeyFilter:
    controls = {0x75: 1, 0x77: 2, 0x78: 3}
    modifiers = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

    def __init__(self):
        self.held = set()

    def remove_device(self, device):
        self.held = {key for key in self.held if key[0] != device}

    def feed(self, device, vk, scan, flags):
        if scan == 0xFF or vk >= 0xFF or vk not in self.controls and vk not in self.modifiers:
            return None
        key = (device, vk, scan, flags & 6)
        if flags & 1:  # RI_KEY_BREAK
            self.held.discard(key)
            return None
        if key in self.held:
            return None  # Ignore hardware repeat until key-up.
        self.held.add(key)
        if vk not in self.controls:
            return None
        mods = {item[1] for item in self.held}
        ctrl = bool(mods & {0x11, 0xA2, 0xA3})
        alt = bool(mods & {0x12, 0xA4, 0xA5})
        if mods & {0x10, 0xA0, 0xA1, 0x5B, 0x5C} or ctrl != alt:
            return None
        return self.controls[vk] + (3 if ctrl and alt else 0)


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("page", ctypes.c_uint16), ("usage", ctypes.c_uint16),
                ("flags", ctypes.c_uint32), ("target", ctypes.c_void_p)]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("device", ctypes.c_void_p), ("wparam", ctypes.c_size_t)]


class _RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("scan", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("reserved", ctypes.c_uint16), ("vk", ctypes.c_uint16),
                ("message", ctypes.c_uint32), ("extra", ctypes.c_uint32)]


def decode_keyboard(data):
    header_size = ctypes.sizeof(_RAWINPUTHEADER)
    needed = header_size + ctypes.sizeof(_RAWKEYBOARD)
    if len(data) < needed:
        return None
    header = _RAWINPUTHEADER.from_buffer_copy(data)
    if header.type != 1 or not needed <= header.size <= len(data):
        return None
    keyboard = _RAWKEYBOARD.from_buffer_copy(data, header_size)
    return int(header.device or 0), keyboard.vk, keyboard.scan, keyboard.flags


class RawInputBackend(WinHotkeyBackend):
    description = "Raw Input 后台控制键（F6/F8/F9 和 Ctrl+Alt 组合）"
    source = "Raw Input"

    def __init__(self):
        super().__init__()
        self.hwnd = None
        self.instance = None
        self.atom = None
        self.raw_registered = False
        self.bindings = set()
        self.events = deque()
        self.filter = RawKeyFilter()
        self.class_name = f"RockSeedControl_{os.getpid()}_{id(self)}"
        self.callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                                                ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t)
        callback_type = self.callback_type

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", ctypes.c_uint32), ("proc", callback_type),
                        ("cls_extra", ctypes.c_int), ("wnd_extra", ctypes.c_int),
                        ("instance", ctypes.c_void_p), ("icon", ctypes.c_void_p),
                        ("cursor", ctypes.c_void_p), ("background", ctypes.c_void_p),
                        ("menu", ctypes.c_wchar_p), ("name", ctypes.c_wchar_p)]

        self.window_class = WNDCLASS
        u = self.u
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        u.RegisterClassW.restype = ctypes.c_uint16
        u.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        u.UnregisterClassW.restype = ctypes.c_int
        u.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                     ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p]
        u.CreateWindowExW.restype = ctypes.c_void_p
        u.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        u.DestroyWindow.argtypes = [ctypes.c_void_p]
        u.DestroyWindow.restype = ctypes.c_int
        u.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RAWINPUTDEVICE), ctypes.c_uint, ctypes.c_uint]
        u.RegisterRawInputDevices.restype = ctypes.c_int
        u.GetRawInputData.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_uint), ctypes.c_uint]
        u.GetRawInputData.restype = ctypes.c_uint
        self.k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.k.GetModuleHandleW.restype = ctypes.c_void_p
        self.callback = callback_type(self._window_proc)  # Retain for the window's lifetime.

    def prepare(self):
        thread_id = super().prepare()
        self.instance = self.k.GetModuleHandleW(None)
        klass = self.window_class(proc=self.callback, instance=self.instance, name=self.class_name)
        self.atom = self.u.RegisterClassW(ctypes.byref(klass))
        if not self.atom:
            raise RuntimeError(f"RegisterClassW: {ctypes.get_last_error()}")
        self.hwnd = self.u.CreateWindowExW(0, self.class_name, "", 0, 0, 0, 0, 0,
                                          ctypes.c_void_p(-3), None, self.instance, None)
        if not self.hwnd:
            raise RuntimeError(f"CreateWindowExW: {ctypes.get_last_error()}")
        # RIDEV_INPUTSINK | RIDEV_DEVNOTIFY; no capture/exclusive flags.
        device = _RAWINPUTDEVICE(1, 6, 0x100 | 0x2000, self.hwnd)
        if not self.u.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
            raise RuntimeError(f"RegisterRawInputDevices: {ctypes.get_last_error()}")
        self.raw_registered = True
        return thread_id

    def register(self, identifier, modifiers, vk):
        self.bindings.add(identifier)
        return None

    def unregister(self, identifier):
        self.bindings.discard(identifier)

    def _read(self, handle):
        size = ctypes.c_uint()
        header_size = ctypes.sizeof(_RAWINPUTHEADER)
        result = self.u.GetRawInputData(handle, 0x10000003, None, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF or not header_size <= size.value <= 65536:
            raise RuntimeError(f"GetRawInputData(size): {ctypes.get_last_error()}")
        data = ctypes.create_string_buffer(size.value)
        result = self.u.GetRawInputData(handle, 0x10000003, data, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF:
            raise RuntimeError(f"GetRawInputData: {ctypes.get_last_error()}")
        return decode_keyboard(data.raw[:result])

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == 0x00FF:  # WM_INPUT
                key = self._read(lparam)
                identifier = self.filter.feed(*key) if key is not None else None
                if identifier in self.bindings:
                    self.events.append((identifier, int(self.u.GetForegroundWindow() or 0)))
            elif message == 0x00FE and wparam == 2:  # GIDC_REMOVAL
                self.filter.remove_device(lparam)
        except Exception as exc:
            self.events.append(exc)
        # DefWindowProc is required to clean up foreground WM_INPUT resources.
        return self.u.DefWindowProcW(hwnd, message, wparam, lparam)

    def receive(self):
        msg = _MSG()
        while True:
            if self.events:
                event = self.events.popleft()
                if isinstance(event, Exception):
                    raise event
                return event
            result = self.u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == -1:
                raise RuntimeError(f"GetMessageW: {ctypes.get_last_error()}")
            if result == 0:
                return None
            self.u.DispatchMessageW(ctypes.byref(msg))

    def dispose(self):
        if self.raw_registered:
            device = _RAWINPUTDEVICE(1, 6, 1, None)  # RIDEV_REMOVE requires null HWND.
            self.u.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            self.raw_registered = False
        if self.hwnd:
            self.u.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.atom:
            self.u.UnregisterClassW(self.class_name, self.instance)
            self.atom = None
