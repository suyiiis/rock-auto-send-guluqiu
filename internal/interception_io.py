"""Interception transport and pass-through control-key monitor.

Uses the unmodified upstream C DLL. Ordinary physical keyboard packets are
forwarded immediately, without translation or logging. Only control keys and
modifier state are examined after forwarding. No game process is opened.
"""
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time

from global_hotkeys import BINDINGS, HotkeyEvent, HotkeyInbox
from raw_hotkeys import RawKeyFilter
from windows_io import InputGuardError
from runtime_status import mark
from paths import RESOURCE_ROOT

VENDOR = RESOURCE_ROOT / "vendor" / "interception"


class KeyStroke(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("state", ctypes.c_uint16),
                ("information", ctypes.c_uint32)]


class MouseStroke(ctypes.Structure):
    _fields_ = [("state", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("rolling", ctypes.c_int16), ("x", ctypes.c_int32),
                ("y", ctypes.c_int32), ("information", ctypes.c_uint32)]


class Stroke(ctypes.Union):
    _fields_ = [("key", KeyStroke), ("mouse", MouseStroke)]


def verified_file(relative):
    path = VENDOR / relative
    manifest = json.loads((VENDOR / "SOURCE.json").read_text(encoding="utf-8"))
    expected = manifest["files"].get(relative)
    if not expected or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise InputGuardError(f"Interception 文件缺失或校验不符：{path}；请重新复制完整的软件文件夹。")
    return path


class NativeContext:
    def __init__(self):
        if os.name != "nt":
            raise InputGuardError("Interception 仅支持 Windows。")
        arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
        path = verified_file(f"Interception/library/{arch}/interception.dll")
        self.dll = ctypes.CDLL(str(path), use_last_error=True)  # Upstream API is cdecl.
        self.context = None
        self.predicate_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
        # Use the DLL's native predicate, avoiding a C -> Python callback on
        # the driver thread for every device when enabling/disabling filters.
        self.keyboard_predicate = self.predicate_type(("interception_is_keyboard", self.dll))
        functions = {
            "create_context": ([], ctypes.c_void_p),
            "destroy_context": ([ctypes.c_void_p], None),
            "set_filter": ([ctypes.c_void_p, self.predicate_type, ctypes.c_uint16], None),
            "get_filter": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_uint16),
            "wait_with_timeout": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "receive": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
            "send": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
            "get_hardware_id": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_uint),
        }
        for name, (args, result) in functions.items():
            fn = getattr(self.dll, "interception_" + name)
            fn.argtypes, fn.restype = args, result
        self.context = self.dll.interception_create_context()
        if not self.context:
            raise InputGuardError("Interception 驱动尚不可用：请打开软件，完成首次初始化，"
                                  "再手动重启电脑；已安装时请检查驱动是否被系统阻止加载。")

    def devices(self):
        result = []
        for device in range(1, 21):
            buffer = ctypes.create_unicode_buffer(1024)
            count = self.dll.interception_get_hardware_id(self.context, device, buffer, ctypes.sizeof(buffer))
            if count:
                result.append({"id": device, "kind": "keyboard" if device <= 10 else "mouse",
                               "hardware_id": buffer.value})
        return result

    def filter_keyboard(self, enabled):
        self.dll.interception_set_filter(self.context, self.keyboard_predicate, 0xFFFF if enabled else 0)

    def keyboard_filter(self, device):
        return self.dll.interception_get_filter(self.context, device)

    def receive(self, milliseconds=20):
        device = self.dll.interception_wait_with_timeout(self.context, milliseconds)
        if not device:
            return None
        packet = Stroke()
        if self.dll.interception_receive(self.context, device, ctypes.byref(packet), 1) != 1:
            raise InputGuardError("Interception 读取设备事件失败，已停止监听。")
        return device, packet

    def send(self, device, packet):
        if not self.context or not 1 <= device <= 20:
            raise InputGuardError("Interception 输入设备无效或已关闭。")
        if self.dll.interception_send(self.context, device, ctypes.byref(packet), 1) != 1:
            raise InputGuardError(f"Interception 未接受设备 {device} 的输入；设备可能已断开。")

    def close(self):
        if self.context:
            self.filter_keyboard(False)
            self.dll.interception_destroy_context(self.context)
            self.context = None


def choose_device(devices, kind, requested=None):
    candidates = [device for device in devices if device["kind"] == kind]
    if requested is not None:
        candidates = [device for device in candidates if device["id"] == requested]
    if not candidates:
        raise InputGuardError(f"没有可用的 Interception {kind} 设备（指定编号：{requested}）。"
                              "请连接键鼠，或检查 config.json 的 interception 配置。")
    # Prefer a physical HID interface over platform/virtual devices. A plain
    # MI_00 interface precedes auxiliary collections from composite keyboards.
    if requested is None:
        candidates.sort(key=lambda d: ('VID_' not in d.get('hardware_id',''),
                                      '&Col' in d.get('hardware_id',''), d['id']))
    return candidates[0]


def encode_event(event):
    """Translate semantic INPUT packets to Interception strokes; no Win32 send."""
    if event.type == 1:
        key = event.ki
        if not key.wScan or key.dwFlags & ~0xB:
            raise InputGuardError("不支持的 Interception 键盘事件。")
        state = (1 if key.dwFlags & 2 else 0) | (2 if key.dwFlags & 1 else 0)
        return "keyboard", Stroke(key=KeyStroke(code=key.wScan, state=state))
    if event.type == 0:
        mouse = event.mi
        if mouse.dwFlags == 0xC001:
            if not 0 <= mouse.dx <= 65535 or not 0 <= mouse.dy <= 65535:
                raise InputGuardError("Interception 鼠标绝对坐标超出范围。")
            return "mouse", Stroke(mouse=MouseStroke(flags=3, x=mouse.dx, y=mouse.dy))
        if mouse.dwFlags in {2, 4}:
            return "mouse", Stroke(mouse=MouseStroke(state=1 if mouse.dwFlags == 2 else 2))
    raise InputGuardError("拒绝未支持的 Interception 输入类型。")


class InterceptionSession(HotkeyInbox):
    description = "Interception 驱动输入与控制键监听"
    available = [entry[2] for entry in BINDINGS]
    registration_failures = []
    # Only the control keys and modifiers are interpreted; all other scans pass through.
    control_vks = {0x40: 0x75, 0x42: 0x77, 0x43: 0x78,
                   0x1D: 0x11, 0x38: 0x12, 0x2A: 0x10, 0x36: 0x10,
                   0x5B: 0x5B, 0x5C: 0x5C}

    def __init__(self, settings=None, context_factory=NativeContext, foreground=None):
        super().__init__()
        self.settings = settings or {}
        self.context_factory = context_factory
        self.native = None
        self.devices = []
        self.keyboard = None
        self.mouse = None
        self.foreground = foreground
        self._thread = None
        self._closing = threading.Event()
        self._ready = threading.Event()
        self._write_lock = threading.Lock()
        self._filter = RawKeyFilter()
        self._controls_thread = None
        self._raw = None
        from file_controls import FileControls
        self._files = FileControls()

    def _controls(self):
        try:
            while not self._closing.is_set():
                for event in self._files.poll():
                    self.publish(event)
                if self._raw is not None:
                    for event in self._raw.drain():
                        self.publish(event)
                    if self._raw.failure:
                        logging.warning("备用热键不可用：%s；请使用菜单控制。", self._raw.failure)
                        self._raw.close()
                        self._raw = None
                self._closing.wait(0.05)
        except Exception as exc:
            self.fail(exc)

    def _start_raw_controls(self):
        from global_hotkeys import GlobalHotkeys
        self.description = "独立菜单控制；Raw Input 热键仅作备用（游戏内可能收不到）"
        self.available = ["菜单暂停", "菜单继续", "菜单停止"]
        try:
            self._raw = GlobalHotkeys().start()
            self.available += self._raw.available
        except Exception as exc:
            logging.warning("备用热键启动失败：%s；独立菜单仍可控制。", exc)
            if self._raw is not None:
                self._raw.close()
                self._raw = None

    def _monitor(self):
        try:
            self._ready.set()
            mapping = {entry[0]: (entry[1], entry[2]) for entry in BINDINGS}
            while not self._closing.is_set():
                item = self.native.receive(20)
                if item is None:
                    continue
                device, packet = item
                # Return the unmodified physical event before any parsing/logging.
                with self._write_lock:
                    self.native.send(device, packet)
                if not 1 <= device <= 10:
                    continue
                key = packet.key
                vk = self.control_vks.get(key.code)
                if vk is None:
                    continue
                identifier = self._filter.feed(device, vk, key.code, key.state)
                if identifier is not None:
                    action, label = mapping[identifier]
                    self.publish(HotkeyEvent(action, label, int(self.foreground() or 0),
                                            time.monotonic(), "Interception"))
        except Exception as exc:
            self.fail(exc)
        finally:
            # Disable interception promptly on any failure, so normal typing resumes.
            try:
                self.native.filter_keyboard(False)
            except Exception as exc:
                self.fail(exc)
            self._ready.set()

    def send(self, event):
        if self.native is None:
            raise InputGuardError("Interception 会话已关闭。")
        kind, packet = encode_event(event)
        device = self.keyboard if kind == "keyboard" else self.mouse
        # Health/cancellation is checked by GameWindow before down; releases still
        # need to reach the driver after a monitor failure or a pause request.
        with self._write_lock:
            self.native.send(device["id"], packet)

    def __enter__(self):
        try:
            mark("驱动初始化", busy=True)
            logging.info("Interception：正在打开驱动。")
            self.native = self.context_factory()
            logging.info("Interception：正在读取键鼠设备。")
            self.devices = self.native.devices()
            self.keyboard = choose_device(self.devices, "keyboard", self.settings.get("keyboard_id"))
            self.mouse = choose_device(self.devices, "mouse", self.settings.get("mouse_id"))
            if self.foreground is None:
                user = ctypes.WinDLL("user32", use_last_error=True)
                user.GetForegroundWindow.argtypes = []
                user.GetForegroundWindow.restype = ctypes.c_void_p
                self.foreground = user.GetForegroundWindow
            if self.settings.get('panel_controls_only', False):
                # Any F1–F10 key can be a game action. Do not register a bare
                # F8/F9 control listener that could consume our own gift key.
                self.description = 'Interception 输入；侧栏独立控制'
                self.available = ['侧栏暂停', '侧栏继续', '侧栏停止']
            elif self.settings.get("monitor_keyboard", False):
                logging.info("Interception：正在检查控制键监听。")
                self.native.filter_keyboard(True)
                actual = self.native.keyboard_filter(self.keyboard["id"])
                if actual == 0xFFFF:
                    self._thread = threading.Thread(target=self._monitor, name="InterceptionControlKeys", daemon=True)
                    self._thread.start()
                    if not self._ready.wait(3):
                        raise InputGuardError("Interception 控制键监听启动超时。")
                else:
                    self.native.filter_keyboard(False)
                    logging.warning("驱动监听读回 %#06x；发送功能独立运行，改用菜单控制。", actual)
                    self._start_raw_controls()
            else:
                self._start_raw_controls()
            self._controls_thread = threading.Thread(target=self._controls, name="RockFileControls", daemon=True)
            self._controls_thread.start()
            self.check_health()
            logging.info("Interception：监听已就绪。")
            mark("监听就绪")
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        mark("关闭驱动监听", busy=True)
        self._closing.set()
        if self._controls_thread is not None:
            self._controls_thread.join(1)
        if self._raw is not None:
            self._raw.close()
            self._raw = None
        if self._thread is not None:
            self._thread.join(2)
            if self._thread.is_alive():
                self.native.filter_keyboard(False)
                raise InputGuardError("Interception 监听未及时退出；已关闭键盘过滤，请关闭脚本进程。")
        if self.native is not None:
            self.native.close()
            self.native = None
        mark("驱动监听已关闭")

    def __exit__(self, *_):
        self.close()


def open_input_session(config):
    if config.get("input_backend", "interception") == "interception":
        return InterceptionSession(dict(config.get('interception') or {}, panel_controls_only=True))
    from global_hotkeys import GlobalHotkeys
    return GlobalHotkeys()
