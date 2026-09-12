"""Non-UI panel services; subprocesses never block the Tk message loop."""
import copy
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from paths import PROJECT_ROOT, INTERNAL_DIR, process_command
from runtime_status import replace_status


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    replace_status(temporary, path)


def updated_config(original, values):
    config = copy.deepcopy(original)
    ints = {"max_rounds", "stable_frames"}
    floats = {"poll_seconds", "key_hold_seconds", "transition_timeout_seconds"}
    for key in ints | floats:
        if key in values:
            try:
                config[key] = int(str(values[key])) if key in ints else float(values[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} 请输入有效数字") from None
    for name in ("keyboard_id", "mouse_id"):
        if name in values:
            value = str(values[name]).strip()
            config.setdefault("interception", {})[name] = int(value) if value else None
    config.pop("sidebar", None)
    return config


def save_config(config, path=None):
    # Validate the complete draft before replacing the user's saved settings.
    from bot import read_config
    path = Path(path or PROJECT_ROOT / "config.json")
    draft = path.with_name("config_validate_"+uuid.uuid4().hex+".json")
    try:
        draft.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        validated = read_config(draft)
        write_json(path, validated)
        return validated
    finally:
        draft.unlink(missing_ok=True)


class PanelSession:
    def __init__(self):
        self.process = None
        self.status_path = None
        self.output_path = None
        self.output = None
        self.last_status = {}
        self.stopping = False

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, hwnd, config):
        if self.running:
            raise RuntimeError("已有任务正在运行")
        token = uuid.uuid4().hex
        logs = PROJECT_ROOT / "logs"
        logs.mkdir(exist_ok=True)
        snapshot = logs / ("config_"+token+".json")
        write_json(snapshot, config)
        self.status_path = logs / ("session_"+token+".json")
        self.output_path = logs / ("panel_"+token+".log")
        self.output = self.output_path.open("w", encoding="utf-8")
        env = dict(os.environ, ROCK_SEED_RUN_TOKEN=token, ROCK_GIFT_PARENT_PID=str(os.getpid()))
        try:
            self.process = subprocess.Popen(process_command('supervisor', '--config', snapshot,
                '--window-hwnd', int(hwnd)), cwd=PROJECT_ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=self.output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception:
            self.output.close()
            raise
        self.stopping = False
        self.last_status = {"mode": "starting", "rounds": 0, "max_rounds": config["max_rounds"],
                            "phase_label": "切回游戏，保持前台3秒后开始"}

    def command(self, action):
        logs = PROJECT_ROOT / "logs"
        logs.mkdir(exist_ok=True)
        if action == "stop":
            (logs / "stop.request").write_text(str(time.time()), encoding="utf-8")
            self.stopping = True
        else:
            write_json(logs / "control.request", {"id": uuid.uuid4().hex, "action": action})

    def poll(self):
        if self.status_path:
            try:
                self.last_status.update(json.loads(self.status_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        if self.process is not None and not self.running:
            if self.output and not self.output.closed:
                self.output.close()
            code = self.process.returncode
            stopped = self.stopping or self.last_status.get("mode") == "stopped"
            complete = code == 0 and self.last_status.get("phase") == "done"
            self.last_status["mode"] = "stopped" if stopped else "complete" if complete else "error"
            if not stopped and not complete:
                reason = self.last_status.get("detail") or self.last_status.get("interruption_reason") or f"运行进程意外退出（退出码 {code}），任务未完成。请查看日志。"
                self.last_status.update(phase_label=reason, interruption_reason=reason)
            elif stopped:
                self.last_status["interruption_reason"] = self.last_status.get("interruption_reason") or "用户已停止本次任务。"
            else:
                self.last_status["interruption_reason"] = ""
            # Keep the final reason instead of re-reading the worker's stale file.
            self.process = None
            self.status_path = None
        return dict(self.last_status)

    def log_tail(self):
        if not self.output_path:
            return "等待开始。"
        try:
            with self.output_path.open("rb") as handle:
                handle.seek(max(0, self.output_path.stat().st_size-8000))
                return handle.read().decode("utf-8", errors="replace")
        except OSError:
            return "正在准备日志……"

    def force_stop(self):
        if self.running:
            from safe_launcher import stop_child
            stop_child(self.process)
