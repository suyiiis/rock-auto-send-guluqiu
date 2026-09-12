"""Temporarily disable classic console QuickEdit to avoid selection stalls."""
from contextlib import contextmanager
import ctypes
import os


@contextmanager
def responsive_console():
    kernel = None
    handle = None
    original = ctypes.c_uint32()
    changed = False
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [ctypes.c_uint32]
        kernel.GetStdHandle.restype = ctypes.c_void_p
        kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel.GetConsoleMode.restype = ctypes.c_int
        kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.SetConsoleMode.restype = ctypes.c_int
        handle = kernel.GetStdHandle(0xFFFFFFF6)  # STD_INPUT_HANDLE
        if handle and kernel.GetConsoleMode(handle, ctypes.byref(original)):
            changed = bool(kernel.SetConsoleMode(handle, (original.value | 0x80) & ~0x40))
    try:
        yield changed
    finally:
        if changed:
            kernel.SetConsoleMode(handle, original.value)
