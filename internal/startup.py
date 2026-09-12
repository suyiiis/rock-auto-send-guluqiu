"""Explicit window selection and a cancellable foreground countdown."""
import logging


def choose_window():
    # These imports stay lazy so countdown tests never access the desktop.
    from windows_io import enumerate_windows, GameWindow, InputGuardError

    candidates = []
    for item in enumerate_windows():
        try:
            window = GameWindow(item["hwnd"])
            if window.readiness_problem(require_foreground=False) is None:
                candidates.append(window)
        except InputGuardError:
            continue
    candidates.sort(key=lambda w: ("洛克王国" not in w.title, w.title.casefold()))
    if not candidates:
        raise RuntimeError("没有可用的16:9窗口。请先恢复游戏窗口，关闭此脚本后重新打开。")
    print("\n请选择游戏窗口（已排除最小化、尺寸或比例不符的窗口）：")
    for index, window in enumerate(candidates, 1):
        _, _, width, height = window.rect()
        print(f"  {index}. {window.title}  [{width}×{height}]")
    default = 1 if "洛克王国" in candidates[0].title else None
    while True:
        suffix = "，回车选1" if default else ""
        value = input(f"窗口编号{suffix}（Q退出）：").strip()
        if value.lower() == "q":
            for window in candidates:
                window.close()
            return None
        if not value and default:
            value = str(default)
        if value.isdecimal() and 1 <= int(value) <= len(candidates):
            chosen = candidates[int(value) - 1]
            for window in candidates:
                if window is not chosen:
                    window.close()
            logging.info("手动选定窗口：%s；客户区 %s。", chosen.title, chosen.rect())
            return chosen
        print("请输入列表中的有效编号。")


class ForegroundCountdown:
    """Only a continuously ready bound window can complete this countdown."""

    def __init__(self, now, seconds=3.0, timeout=120.0):
        self.started_at = now
        self.seconds = seconds
        self.timeout = timeout
        self.ready_since = None
        self.finished = False

    def update(self, ready, now):
        if self.finished:
            return "idle"
        if now - self.started_at >= self.timeout:
            self.finished = True
            return "timeout"
        if not ready:
            self.ready_since = None
            return "waiting"
        if self.ready_since is None:
            self.ready_since = now
            return "counting"
        if now - self.ready_since >= self.seconds:
            self.finished = True
            return "start"
        return "counting"

    def cancel(self):
        self.finished = True
