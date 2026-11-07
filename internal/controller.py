"""Confirmed states and pending transitions are separate; no desktop access."""
from dataclasses import dataclass
from keymap import FUNCTION_KEYS


@dataclass(frozen=True)
class Action:
    kind: str
    value: object = None


class Controller:
    LABELS = {"detect": "识别起始状态", "unready": "未准备", "prepare1": "准备1",
              "prepare2": "准备2", "gift1": "赠送1：选择界面", "gift2": "赠送2：确认弹窗",
              "giving": "赠送3：等待完成（不限时）", "done": "已完成"}
    TRANSITIONS = {"unready": ("prepare1", "press", "tab"),
                   "prepare1": ("prepare2", "press", "tab"),
                   "prepare2": ("gift1", "press", "gift_key"),
                   "gift1": ("gift2", "click", "give"),
                   "gift2": ("giving", "click", "confirm")}

    def __init__(self, config, now=0.0):
        self.config = config
        self.phase = "detect"
        self.pending = None
        self.since = now
        self.previous = None
        self.stable = 0
        self.rounds = 0
        self.orange_seen = False
        self.last_completion = None
        self.select_mode = True  # Always pick the configured ball and count.
        self.ball_index = config.get("ball_index", 1)
        self.gift_count = config.get("gift_count", 1)
        self._reset_gift1_selection()

    def _reset_gift1_selection(self):
        self.gift1_stage = "ball"
        self.gift1_plus = 0

    def _gift1_selection_step(self, observation):
        """Select mode inside gift1: click the ball, bump the count, then give."""
        buttons = observation.get("buttons") or {}
        if self.gift1_stage == "ball":
            slots = buttons.get("ball_slots") or []
            if not 1 <= self.ball_index <= len(slots):
                return [Action("pause", f"未能定位第 {self.ball_index} 个球的格子，未发送输入。")]
            self.gift1_stage = "settle"
            return [Action("click", slots[self.ball_index - 1])]
        if self.gift1_stage == "settle":
            self.gift1_stage = "plus"
            return []
        if self.gift1_stage == "plus":
            if self.gift1_plus < self.gift_count - 1:
                plus = buttons.get("plus")
                if plus is None:
                    return [Action("pause", "未能定位数量加号按钮，数量未调整。")]
                self.gift1_plus += 1
                return [Action("click", plus)]
            self.gift1_stage = "give"
            return None
        return None

    @property
    def label(self):
        text = self.LABELS[self.phase]
        return text+" → 等待确认 "+self.LABELS[self.pending] if self.pending else text

    def resumed(self, now):
        # Preserve a possibly sent confirmation and orange history on resume.
        self.since = now
        self.previous = None
        self.stable = 0
        self._reset_gift1_selection()

    def input_failed(self, input_sent, now):
        if not input_sent:
            self.pending = None
        self.resumed(now)

    def step(self, observation, now):
        state = observation.get("state", "unknown")
        signature=(state,observation.get('gift_key')) if state in {'prepare2','giving'} else (state,None)
        self.stable = self.stable+1 if signature == self.previous else 1
        self.previous = signature
        ready = self.stable >= self.config["stable_frames"]
        if self.phase == "done":
            return []
        if self.pending:
            if ready and state == self.pending:
                self.phase = self.pending
                self.pending = None
                self.since = now
                if state == "giving":
                    self.orange_seen = True
                if state == "gift1":
                    self._reset_gift1_selection()
                return [Action("transition_complete", self.phase)]
            if now-self.since > self.config["transition_timeout_seconds"]:
                return [Action("pause", f"{self.label}超时；操作已发出，未重复发送。当前识别：{state}")]
            return []
        if self.phase == "giving":
            # Unlimited observation. Unknown/missing UI is never completion.
            if ready and state == "prepare2" and self.orange_seen:
                self.rounds += 1
                self.orange_seen = False
                self.last_completion = now
                limit = self.config["max_rounds"]
                self.phase = "done" if limit and self.rounds >= limit else "prepare2"
                self.since = now
                return [Action("round_complete", self.rounds)]
            if ready and state == "unready" and self.orange_seen:
                # Some gift animations fold the action page back to the main
                # HUD before prepare2 can hold for three frames. The orange
                # icon was already confirmed, so count the round and restart
                # the loop from the main screen.
                self.rounds += 1
                self.orange_seen = False
                self.last_completion = now
                limit = self.config["max_rounds"]
                self.phase = "done" if limit and self.rounds >= limit else "unready"
                self.since = now
                return [Action("round_complete", self.rounds)]
            if ready and state not in {"giving", "prepare2", "unknown"}:
                return [Action("pause", f"赠送等待中出现意外界面 {state}，未计为完成。")]
            return []
        if self.phase == "detect":
            if ready and state in self.LABELS and state not in {"detect", "done"}:
                self.phase = state
                self.orange_seen = state == "giving"
                self.since = now
                if state == "gift1":
                    self._reset_gift1_selection()
                return [Action("transition_complete", state)]
        elif ready and state == self.phase:
            if self.phase == "gift1" and self.select_mode:
                staged = self._gift1_selection_step(observation)
                if staged is not None:
                    return staged
            target, kind, value = self.TRANSITIONS[self.phase]
            if value == 'gift_key':
                value=observation.get('gift_key')
                if value not in FUNCTION_KEYS:
                    return [Action('pause','未能确认“赠送条件球”对应的 F 键，未发送输入。')]
            if kind == "click":
                value = observation.get("buttons", {}).get(value)
                if value is None:
                    return [Action("pause", "已识别界面，但未确认目标按钮位置。")]
            self.pending = target
            self.since = now
            self.stable = 0
            return [Action(kind, value)]
        elif ready and state != "unknown":
            return [Action("pause", f"界面意外变化：{self.LABELS[self.phase]} → {state}。未发送输入。")]
        if now-self.since > self.config["transition_timeout_seconds"]:
            return [Action("pause", f"{self.label}未能稳定识别，当前识别：{state}。")]
        return []
