"""Supervise the bot without loading Interception into the launcher process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import re
from paths import PROJECT_ROOT, INTERNAL_DIR, process_command

BASE = PROJECT_ROOT
DRIVER_TIMEOUT = 10


def decision(status, now, started, stop_at=0):
    if stop_at >= started:
        return "stop"
    if status is None:
        return "timeout" if now - started >= 15 else None
    if status.get("done"):
        return "done"
    if status.get("busy") and now - status["at"] >= DRIVER_TIMEOUT:
        return "timeout"
    return None


def stop_child(child):
    # Only this launcher's own subprocess tree is targeted, never all Python.
    if child.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        if child.poll() is None:
            child.kill()
    except OSError:
        pass


def launch(args):
    logs = BASE / "logs"
    logs.mkdir(exist_ok=True)
    token = os.environ.get("ROCK_SEED_RUN_TOKEN", "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        token = uuid.uuid4().hex
    status_file = logs / ("session_" + token + ".json")
    report = logs / ("launcher_" + token + ".log")
    stop_file = logs / "stop.request"
    env = dict(os.environ, ROCK_SEED_STATUS_FILE=str(status_file))
    started = time.time()
    child = subprocess.Popen(process_command('worker', *args), cwd=BASE, env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    status = None
    try:
        while True:
            owner = os.environ.get('ROCK_GIFT_PARENT_PID')
            if owner and not parent_alive(int(owner)):
                return 130
            code = child.poll()
            if code is not None:
                return code
            try:
                status = json.loads(status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # Atomic replace can briefly deny Windows readers. Keep the
                # most recent heartbeat; a transient read is not a new launch.
                pass
            try:
                stop_at = stop_file.stat().st_mtime
            except OSError:
                stop_at = 0
            action = decision(status, time.time(), started, stop_at)
            if action:
                if action in {"stop", "done"}:
                    # Give the child time to send key/button UP and close the
                    # driver before using the independent termination fallback.
                    try:
                        return child.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
                if action == "done":
                    code = status.get("code", 1)
                    message = status.get("detail") or "脚本已结束。"
                elif action == "stop":
                    code, message = 1, "已收到独立停止请求，正在结束本次脚本。"
                else:
                    stage = (status or {}).get("stage", "启动")
                    code, message = 1, f"脚本在“{stage}”阶段超时，已终止本次运行。未继续发送游戏输入。"
                report.write_text(message + "\n" + json.dumps(status, ensure_ascii=False), encoding="utf-8")
                stop_child(child)
                from runtime_status import replace_status
                final={**(status or {}), 'mode':'stopped' if action=='stop' else 'error',
                       'detail':message, 'interruption_reason':message, 'code':code, 'done':True}
                temporary=status_file.with_suffix('.supervisor.tmp')
                temporary.write_text(json.dumps(final,ensure_ascii=False),encoding='utf-8')
                replace_status(temporary,status_file)
                print(message, flush=True)
                return code
            time.sleep(0.1)
    except KeyboardInterrupt:
        stop_child(child)
        return 130
    finally:
        stop_child(child)


def parent_alive(pid):
    if os.name!='nt':
        try: os.kill(pid,0); return True
        except OSError: return False
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x100000,False,pid)
    if not handle: return False
    try: return kernel.WaitForSingleObject(handle,0)==258
    finally: kernel.CloseHandle(handle)


def entry(args=None):
    from console_io import responsive_console
    from contextlib import nullcontext
    from run_lease import single_run
    # A read-only screenshot probe never sends input or opens the driver.
    args=list(sys.argv[1:] if args is None else args)
    readonly = "--observe" in args or "--inspect-image" in args
    with responsive_console():
        try:
            with nullcontext() if readonly else single_run():
                return launch(args)
        except RuntimeError as exc:
            print(str(exc), flush=True)
            return 1


if __name__=='__main__':
    sys.exit(entry())
