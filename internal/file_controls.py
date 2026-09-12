"""Independent controls that remain usable when the game consumes hotkeys."""
import json
from pathlib import Path
import time

from global_hotkeys import HotkeyEvent
from paths import PROJECT_ROOT


class FileControls:
    def __init__(self, folder=None, started=None):
        self.folder = Path(folder or PROJECT_ROOT / "logs")
        self.started = time.time() if started is None else started
        self.seen = None
        self.stopped = False

    def poll(self):
        events = []
        try:
            if not self.stopped and (self.folder / "stop.request").stat().st_mtime >= self.started:
                self.stopped = True
                events.append(HotkeyEvent("stop", "菜单停止", source="独立控制入口"))
        except FileNotFoundError:
            pass
        try:
            path = self.folder / "control.request"
            if path.stat().st_mtime < self.started:
                return events
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            token, action = data.get("id"), data.get("action")
            if token and token != self.seen and action in {"pause", "resume", "stop"}:
                self.seen = token
                events.append(HotkeyEvent(action, "菜单" + {"pause": "暂停", "resume": "继续", "stop": "停止"}[action],
                                         source="独立控制入口"))
        except (FileNotFoundError, ValueError):
            pass
        return events
