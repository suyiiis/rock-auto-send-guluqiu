"""OS-owned lock: prevents two launchers from driving the same game at once."""
from contextlib import contextmanager
import os
from pathlib import Path

from paths import PROJECT_ROOT


@contextmanager
def single_run(path=None):
    path = Path(path or PROJECT_ROOT / "logs" / "run.lock")
    path.parent.mkdir(exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("本目录已有脚本正在运行。请使用菜单继续或停止原实例，不要重复启动。") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("本目录已有脚本正在运行。") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
