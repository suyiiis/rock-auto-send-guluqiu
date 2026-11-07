# -*- coding: utf-8 -*-
"""离线测试：gift1 选球新功能的 vision 输出、controller 序列、配置校验。"""
import json
import sys
sys.path.insert(0, 'internal')
import cv2
import numpy as np

passed = []
failed = []

def check(name, cond, detail=''):
    (passed if cond else failed).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {detail}' if detail and not cond else ''))

# ---------- 1. vision：截图上输出 ball_slots / plus / minus ----------
from vision import Detector
img = cv2.imdecode(np.fromfile(r'C:/Users/Measy/.workbuddy/blobs/db/db838d0445d78fc9fc5afce1db56475c8de86eeb0d6ceff4bc5c5732428e4768.jpg', np.uint8), cv2.IMREAD_COLOR)
obs = Detector().analyze(img)
check('vision: state==gift1', obs['state'] == 'gift1')
slots = obs.get('buttons', {}).get('ball_slots')
check('vision: ball_slots 有 12 个', isinstance(slots, list) and len(slots) == 12, str(slots and len(slots)))
if slots:
    # 第2个球：归一化 (462,204) -> 2048系 (492.8,217.6)；give 锚点匹配标准位置时偏移为0
    b2 = slots[1]
    check('vision: 球2坐标≈(492.8,217.6)', abs(b2[0]-492.8) < 8 and abs(b2[1]-217.6) < 8, str(b2))
    b7 = slots[6]
    check('vision: 球7坐标≈(334.9,375.5)', abs(b7[0]-334.9) < 8 and abs(b7[1]-375.5) < 8, str(b7))
plus = obs.get('buttons', {}).get('plus')
minus = obs.get('buttons', {}).get('minus')
check('vision: plus≈(1868.8,989.9)', plus and abs(plus[0]-1868.8) < 8 and abs(plus[1]-989.9) < 8, str(plus))
check('vision: minus≈(1397.3,989.9)', minus and abs(minus[0]-1397.3) < 8 and abs(minus[1]-989.9) < 8, str(minus))

# ---------- 2. controller：select 模式动作序列 ----------
from controller import Controller
base = {'stable_frames': 3, 'max_rounds': 1, 'transition_timeout_seconds': 10.0,
        'gift_selection': 'select', 'ball_index': 2, 'gift_count': 3}
buttons = {'ball_slots': slots or [[100,100]]*12, 'plus': [1, 2], 'give': [3, 4], 'minus': [5, 6]}
obs_gift1 = {'state': 'gift1', 'buttons': buttons}

c = Controller(base, now=0.0)
c.phase = 'gift1'  # 直接处于 gift1
seq = []
t = 0.0
for i in range(10):
    t += 0.25
    actions = c.step(obs_gift1, t)
    for a in actions:
        seq.append((a.kind, a.value))
    if c.pending:
        break
kinds = [s[0] for s in seq]
check('controller: 序列=ball,+,+,give', kinds == ['click', 'click', 'click', 'click'], str(seq))
check('controller: 第一次点球=球2', seq and seq[0][1] == (buttons['ball_slots'][1]), str(seq and seq[0]))
plus_clicks = [s for s in seq if s[1] == [1, 2]]
check('controller: 点了2次加号', len(plus_clicks) == 2, str(plus_clicks))
check('controller: 最后点赠送', seq and seq[-1][1] == [3, 4] and c.pending == 'gift2', str(seq and seq[-1]))

# count=1 时不点加号
c2 = Controller({**base, 'gift_count': 1}, now=0.0)
c2.phase = 'gift1'
seq2 = []
for i in range(8):
    actions = c2.step(obs_gift1, 0.25*(i+1))
    for a in actions:
        seq2.append((a.kind, a.value))
    if c2.pending:
        break
check('controller: count=1 只点球和赠送', [s[0] for s in seq2] == ['click', 'click'] and seq2[0][1] == buttons['ball_slots'][1], str(seq2))

# 缺省字段时默认球1数量1
c3 = Controller({'stable_frames': 3, 'max_rounds': 1, 'transition_timeout_seconds': 10.0}, now=0.0)
c3.phase = 'gift1'
seq3 = []
for i in range(8):
    actions = c3.step(obs_gift1, 0.25*(i+1))
    for a in actions:
        seq3.append((a.kind, a.value))
    if c3.pending:
        break
check('controller: 缺省配置点球1+直接赠送', [s[0] for s in seq3] == ['click', 'click'] and seq3[0][1] == buttons['ball_slots'][0], str(seq3))

# 暂停恢复后重置子步骤
c4 = Controller(base, now=0.0)
c4.phase = 'gift1'
c4.step(obs_gift1, 0.5)  # 点球
c4.resumed(1.0)          # 暂停恢复
check('controller: resumed 后 stage 重置', c4.gift1_stage == 'ball' and c4.gift1_plus == 0)

# 走完一轮后回到 gift1 时重置（gift2->giving->prepare2->gift1）
c5 = Controller({**base, 'max_rounds': 2}, now=0.0)
c5.phase = 'gift1'
t5 = 0.0
seq5 = []
for i in range(8):
    t5 += 0.25
    for a in c5.step(obs_gift1, t5):
        seq5.append((a.kind, a.value))
    if c5.pending:
        break

def step_n(ctrl, obs, start, n=3, dt=0.25):
    for _ in range(n):
        start += dt
        ctrl.step(obs, start)
    return start

t5 = step_n(c5, {'state': 'gift2', 'buttons': {'confirm': [7, 8]}}, t5, n=3)  # 确认 gift2
t5 = step_n(c5, {'state': 'gift2', 'buttons': {'confirm': [7, 8]}}, t5, n=1)  # 点击"确认"，pending=giving
t5 = step_n(c5, {'state': 'giving', 'buttons': {}}, t5)     # 确认 giving (orange_seen)
t5 = step_n(c5, {'state': 'prepare2', 'buttons': {}}, t5)   # giving -> prepare2, rounds=1
t5 = step_n(c5, {'state': 'prepare2', 'gift_key': 'f5', 'buttons': {}}, t5)  # press F5
t5 = step_n(c5, obs_gift1, t5)                              # 确认重新进入 gift1
check('controller: 重新进入 gift1 后 stage 重置', c5.gift1_stage == 'ball' and c5.gift1_plus == 0 and c5.phase == 'gift1' and c5.rounds == 1,
      f'phase={c5.phase} stage={c5.gift1_stage} plus={c5.gift1_plus} rounds={c5.rounds}')

# giving 中动作页收起回主界面（unready）：计完成并从主界面续跑下一轮
c6 = Controller({**base, 'max_rounds': 2}, now=0.0)
c6.phase = 'giving'
c6.orange_seen = True
t6 = 0.0
t6 = step_n(c6, {'state': 'unready', 'buttons': {}}, t6)
check('controller: giving->unready 计完成', c6.rounds == 1 and c6.phase == 'unready', f'rounds={c6.rounds} phase={c6.phase}')
t6 = step_n(c6, {'state': 'unready', 'buttons': {}}, t6)                      # press tab
t6 = step_n(c6, {'state': 'prepare1', 'buttons': {}}, t6)                    # 确认 prepare1
t6 = step_n(c6, {'state': 'prepare1', 'buttons': {}}, t6, n=1)               # press tab
t6 = step_n(c6, {'state': 'prepare2', 'gift_key': 'f2', 'buttons': {}}, t6, n=4)  # 确认 prepare2 并按 F2
t6 = step_n(c6, obs_gift1, t6)                                               # 确认 gift1
check('controller: unready 后下一轮正常进 gift1', c6.phase == 'gift1' and c6.gift1_stage == 'ball', f'phase={c6.phase}')

# ---------- 3. read_config 校验 ----------
from bot import read_config
import tempfile, os
def validate(cfg):
    fd, p = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f)
    try:
        return read_config(p)
    finally:
        os.unlink(p)

good = {'max_rounds': 0, 'poll_seconds': .25, 'stable_frames': 3, 'transition_timeout_seconds': 10.0,
        'key_hold_seconds': .15, 'input_backend': 'interception', 'interception': {},
        'ball_index': 2, 'gift_count': 3}
check('config: ball2+count3 合法', validate(good) is not None)
check('config: 老配置带 gift_selection 被忽略', validate({**good, 'gift_selection': 'current'}).get('gift_selection') is None)
check('config: 无 ball_index 默认 1', validate({k: v for k, v in good.items() if k != 'ball_index'}).get('ball_index', 1) == 1)
def expect_fail(cfg, name):
    try:
        validate(cfg)
        check(name, False, '未抛异常')
    except ValueError:
        check(name, True)
expect_fail({**good, 'ball_index': 0}, 'config: ball_index=0 拒绝')
expect_fail({**good, 'ball_index': 13}, 'config: ball_index=13 拒绝')
expect_fail({**good, 'gift_count': 0}, 'config: gift_count=0 拒绝')
expect_fail({**good, 'gift_count': 1000}, 'config: gift_count=1000 拒绝')

# ---------- 4. updated_config 集成 ----------
from panel_service import updated_config
merged = updated_config(good, {'ball_index': '5', 'gift_count': '20'})
check('panel: 输入转成 5+20', merged['ball_index'] == 5 and merged['gift_count'] == 20, str(merged))
check('panel: 合并后可通过校验', validate(merged) is not None)
legacy = updated_config({'gift_selection': 'select', 'ball_index': 1, 'gift_count': 1}, {'ball_index': '3'})
check('panel: 老的 gift_selection 被清除', 'gift_selection' not in legacy, str(legacy))

# ---------- 5. sidebar / bot 可导入（语法检查） ----------
import sidebar, bot, panel_service, controller, vision
check('import: 全部模块可导入', True)

print()
print(f'===== {len(passed)} passed, {len(failed)} failed =====')
sys.exit(1 if failed else 0)
