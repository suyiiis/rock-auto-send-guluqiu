"""Supported keyboard input and configurable menu navigation."""
KEY_SCANS = {
    "f2": (0x3C, False),
    "1": (0x02, False), "2": (0x03, False), "3": (0x04, False), "4": (0x05, False), "f": (0x21, False),
    "enter": (0x1C, False), "space": (0x39, False), "tab": (0x0F, False),
    "escape": (0x01, False), "left": (0x4B, True), "right": (0x4D, True),
    "up": (0x48, True), "down": (0x50, True),
}
KEY_VKS = {
    "f2": 0x71,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "f": 0x46, "enter": 0x0D, "space": 0x20,
    "tab": 0x09, "escape": 0x1B, "left": 0x25, "right": 0x27,
    "up": 0x26, "down": 0x28,
}
FUNCTION_KEYS = tuple(f"f{number}" for number in range(1, 11))
KEY_SCANS.update({key: (0x3A+number, False) for number,key in enumerate(FUNCTION_KEYS, 1)})
KEY_VKS.update({key: 0x6F+number for number,key in enumerate(FUNCTION_KEYS, 1)})
MENU_LABELS = {
    "home": "画面1挑战", "challenge": "画面2挑战", "prepare": "画面3开始",
    "confirm": "画面4开始", "purple": "画面7切换紫色体力", "blue": "画面7切换蓝色体力", "cleanup": "结算继续",
}


def validate_key_bindings(bindings):
    if not isinstance(bindings, dict):
        raise ValueError("key_bindings 必须是对象")
    for name, sequence in bindings.items():
        if name not in MENU_LABELS:
            raise ValueError(f"未知按键步骤：{name}")
        if sequence is None:
            continue
        if not isinstance(sequence, list) or not sequence or len(sequence) > 30:
            raise ValueError(f"key_bindings.{name} 必须是1到30个按键组成的列表，或 null")
        if any(not isinstance(key, str) or key not in KEY_SCANS for key in sequence):
            raise ValueError(f"key_bindings.{name} 含不支持的按键；支持：{', '.join(KEY_SCANS)}")
