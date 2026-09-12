"""Small file protocol between the bot and its independent launcher."""
import json
import os
from pathlib import Path
import time
import threading
from paths import PROJECT_ROOT

_write_lock = threading.Lock()
_progress = {}


def progress(**fields):
    if not os.environ.get("ROCK_SEED_STATUS_FILE"):
        return
    if all(_progress.get(k) == v for k, v in fields.items()):
        return
    _progress.update(fields)
    mark(_progress.get("phase_label", "等待启动"))


def replace_status(temporary, path):
    # Windows denies rename while the supervisor briefly has the JSON open.
    # Retry sharing violations, bounded well below the supervisor timeout.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


def mark(stage, *, busy=False, done=False, code=0, detail=""):
    name = os.environ.get("ROCK_SEED_STATUS_FILE")
    if not name:
        return
    path = Path(name)
    root = PROJECT_ROOT / "logs"
    if path.parent.resolve() != root.resolve() or not path.name.startswith("session_"):
        raise RuntimeError("Invalid launcher status path")
    data = {**_progress, "pid": os.getpid(), "stage": stage, "at": time.time(), "busy": busy,
            "done": done, "code": code, "detail": detail}
    temporary = path.with_suffix(".tmp")
    with _write_lock:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        replace_status(temporary, path)
