"""Screenshot-driven gifting; all real input goes through guarded Interception."""
import argparse
import json
import logging
import math
from pathlib import Path
import sys
import time
from controller import Controller
from paths import PROJECT_ROOT
from runtime_status import mark, progress

VERSION='2026.09.12-gift8-portable'


def read_config(path):
    config=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if type(config.get('max_rounds')) is not int or config['max_rounds']<0:
        raise ValueError('目标次数必须为非负整数，0表示持续循环')
    if type(config.get('stable_frames')) is not int or not 2<=config['stable_frames']<=30:
        raise ValueError('连续确认帧数必须为2到30的整数')
    for key,lo,hi in [('poll_seconds',.05,5),('transition_timeout_seconds',1,120),('key_hold_seconds',.03,1)]:
        v=config.get(key)
        if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(f'{key} 必须在 {lo} 到 {hi} 之间')
    if config.get('input_backend')!='interception':
        raise ValueError('本脚本使用 Interception 输入后端')
    settings=config.get('interception')
    if not isinstance(settings,dict): raise ValueError('缺少键鼠设备配置')
    for key,lo,hi in [('keyboard_id',1,10),('mouse_id',11,20)]:
        v=settings.get(key)
        if v is not None and (type(v) is not int or not lo<=v<=hi):
            raise ValueError(f'{key} 必须在 {lo} 到 {hi} 之间，或留空自动选择')
    # Retire old docking/topmost preferences when loading an existing data folder.
    config.pop('sidebar',None)
    if config.get('gift_selection')!='current':
        raise ValueError('本脚本沿用游戏当前选择，不调整球种或数量')
    return config


def configure_logging():
    folder=PROJECT_ROOT/'logs'
    folder.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',datefmt='%H:%M:%S',
                        handlers=[logging.FileHandler(folder/time.strftime('run_%Y%m%d_%H%M%S.log'),encoding='utf-8'),logging.StreamHandler(sys.stdout)],force=True)


def diagnostic(frame,observation,controller,reason):
    import cv2
    stem=PROJECT_ROOT/'logs'/time.strftime('paused_%Y%m%d_%H%M%S')
    if frame is not None: cv2.imencode('.png',frame)[1].tofile(str(stem.with_suffix('.png')))
    stem.with_suffix('.json').write_text(json.dumps({'version':VERSION,'frame_size':list(frame.shape[:2][::-1]) if hasattr(frame,'shape') else None,
        'phase':controller.phase,'pending':controller.pending,
        'rounds':controller.rounds,'orange_seen':controller.orange_seen,'reason':reason,'observation':observation},ensure_ascii=False,indent=2),encoding='utf-8')


def apply_input_settings(game,config,session=None):
    from windows_io import InputGuardError
    if session is None:
        raise InputGuardError('Interception 会话未打开，拒绝退回 SendInput。')
    game.input_sender=session
    game.input_backend_name='Interception'
    game.keyboard_mode='scan_code'
    game.key_hold_seconds=config.get('key_hold_seconds',.15)


def run_loop(config,game,session,*,detector=None,clock=time.monotonic):
    from vision import Detector
    from windows_io import InputGuardError
    from startup import ForegroundCountdown
    detector=detector or Detector()
    controller=Controller(config,clock())
    countdown=ForegroundCountdown(clock())
    active=False
    observation={}
    last_status=None
    pause_reason=''
    apply_input_settings(game,config,session)
    game.cancel_check=lambda: session.interrupt_pending() or not active
    logging.info('已绑定 %s；沿用游戏当前选中的球和数量；切回游戏并保持前台3秒后开始。',game.title)
    logging.info('使用侧栏暂停/继续/停止。赠送3无超时，只有橙色图标恢复普通色并确认准备2才计数。')
    while True:
        progress(mode='running' if active else 'countdown' if countdown else 'paused',phase=controller.phase,
                 phase_label=controller.label,pending=controller.pending,rounds=controller.rounds,
                 max_rounds=config['max_rounds'],recognized=observation.get('state','unknown'),
                 orange_seen=controller.orange_seen,gift_key=observation.get('gift_key'),
                 interruption_reason=pause_reason)
        session.check_health()
        events=session.drain()
        if session.stop_requested or any(e.action=='stop' for e in events):
            logging.info('已停止，本次完成 %d 轮。',controller.rounds)
            progress(mode='stopped',interruption_reason='用户已停止本次任务。')
            return controller
        for event in events:
            logging.info('收到控制请求：%s',event.action)
            if event.action=='pause' or (event.action=='toggle' and (active or countdown)):
                active=False
                countdown=None
                pause_reason='用户已暂停。点击继续后，切回游戏保持前台3秒。'
            elif event.action in ('resume','toggle') and not active and countdown is None:
                countdown=ForegroundCountdown(clock())
                pause_reason=''
        if countdown:
            result=countdown.update(game.is_ready(),clock())
            if result=='start':
                controller.resumed(clock())
                active=True
                countdown=None
                logging.info('倒计时完成：%s',controller.label)
            elif result=='timeout':
                countdown=None
                pause_reason='等待游戏前台超过120秒。请切回绑定的游戏窗口后继续。'
                logging.info('等待游戏前台超时；已暂停。')
        if not active:
            session.wait(.05)
            continue
        if not game.is_ready():
            active=False
            pause_reason='游戏失去焦点、已最小化或窗口发生变化。切回游戏后点击继续。'
            logging.info('游戏失焦或窗口变化，已暂停；保留状态及待确认操作。')
            continue
        frame=None
        try:
            mark('截图识别及输入',busy=True)
            frame=game.capture()
            observation=detector.analyze(frame)
            if config.get('prepare_only') and observation['state'] in {'gift1','gift2','giving'}:
                progress(mode='stopped',phase_label='准备测试已停止：当前已经进入赠送流程',pending=None)
                logging.warning('准备测试只允许 Tab 切换；当前识别=%s，未发送任何赠送操作。',observation['state'])
                return controller
            actions=controller.step(observation,clock())
            status=(controller.phase,controller.pending,observation['state'],controller.rounds)
            if status!=last_status:
                logging.info('%s | 识别=%s | 完成=%d/%s',controller.label,observation['state'],controller.rounds,config['max_rounds'] or '不限')
                last_status=status
                if config.get('save_live_trace'):
                    import cv2
                    name=time.strftime('trace_%Y%m%d_%H%M%S_')+controller.phase+'_'+observation['state']+'.png'
                    cv2.imencode('.png',frame)[1].tofile(str(PROJECT_ROOT/'logs'/name))
            for action in actions:
                if config.get('prepare_only') and (action.kind=='click' or (action.kind=='press' and action.value!='tab')):
                    raise RuntimeError('准备测试禁止赠送输入')
                if action.kind=='press':
                    game.press(action.value)
                    logging.info('发起转移：按 %s；等待目标界面确认。',action.value)
                elif action.kind=='click':
                    game.click(*action.value)
                    logging.info('发起转移：点击按钮 %s；等待目标界面确认。',action.value)
                elif action.kind=='transition_complete':
                    logging.info('转移已确认：%s',controller.LABELS[action.value])
                elif action.kind=='round_complete':
                    logging.info('第 %d 轮完成：已观察到橙色 → 普通色，并确认准备2。',action.value)
                elif action.kind=='pause':
                    active=False
                    pause_reason=str(action.value)
                    logging.warning(action.value)
                    diagnostic(frame,observation,controller,action.value)
            if config.get('prepare_only') and controller.phase=='prepare2' and controller.pending is None:
                progress(mode='complete',phase='prepared',phase_label='准备2已确认；准备测试结束，未赠送球',pending=None,rounds=0,orange_seen=False)
                logging.info('准备测试完成：准备2已确认，赠送快捷键=%s；未发送赠送输入。',observation.get('gift_key'))
                return controller
            if controller.phase=='done':
                progress(mode='complete',phase='done',phase_label='已完成',pending=None,rounds=controller.rounds,recognized='prepare2',orange_seen=False)
                return controller
        except InputGuardError as exc:
            controller.input_failed(exc.input_sent,clock())
            if session.interrupt_pending():
                # Let the queued toggle see the prior running state; marking
                # inactive here would turn a requested pause into a resume.
                logging.info('输入因控制请求中止，等待处理暂停或停止。')
            else:
                active=False
                pause_reason=f'输入被中止：{exc}'
                logging.warning('输入被中止，已暂停：%s；已发送=%s',exc,exc.input_sent)
                diagnostic(frame,observation,controller,str(exc))
        except Exception as exc:
            active=False
            pause_reason=f'识别或输入异常：{exc}'
            logging.exception('识别或输入异常，已暂停')
            diagnostic(frame,observation,controller,str(exc))
        finally:
            # The supervisor only times out blocked driver/capture calls, never
            # the unlimited, non-busy giving-state wait.
            mark('等待下一次观察')
        session.wait(config['poll_seconds'])


def main(argv=None):
    parser=argparse.ArgumentParser(description='洛克王国自动赠送咕噜球')
    parser.add_argument('--config',type=Path,default=PROJECT_ROOT/'config.json')
    parser.add_argument('--window-hwnd',type=int)
    parser.add_argument('--select-window',action='store_true')
    parser.add_argument('--rounds',type=int)
    parser.add_argument('--inspect-image',type=Path)
    parser.add_argument('--driver-status',action='store_true')
    parser.add_argument('--observe',action='store_true',help='只观察当前游戏，不发送输入')
    parser.add_argument('--prepare-only',action='store_true',help='只验证 Tab 准备过程，不赠送球')
    args=parser.parse_args(argv)
    config=read_config(args.config)
    config['prepare_only']=args.prepare_only
    if args.inspect_image:
        import cv2
        import numpy as np
        from vision import Detector
        frame=cv2.imdecode(np.fromfile(str(args.inspect_image),np.uint8),cv2.IMREAD_COLOR)
        if frame.shape[:2]==(1127,1924): frame=frame[45:1125,2:1922]
        print(json.dumps(Detector().analyze(frame),ensure_ascii=False,indent=2))
        return
    if args.driver_status:
        from interception_io import NativeContext
        mark('读取驱动设备',busy=True)
        native=NativeContext()
        try: print(json.dumps(native.devices(),ensure_ascii=False,indent=2))
        finally: native.close()
        return
    configure_logging()
    logging.info('版本 %s',VERSION)
    from vision import SIMILARITY_THRESHOLD
    logging.info('模板相似度门槛：大于 %.2f（所有场景及 F 键）。',SIMILARITY_THRESHOLD)
    if args.rounds is not None:
        if args.rounds<0: parser.error('次数不可为负数')
        config['max_rounds']=args.rounds
    from windows_io import GameWindow
    from startup import choose_window
    game=GameWindow(args.window_hwnd) if args.window_hwnd else choose_window() if args.select_window else None
    if game is None: parser.error('请从侧栏选择游戏，或使用 --select-window')
    try:
        if args.observe:
            from vision import Detector
            from startup import ForegroundCountdown
            countdown=ForegroundCountdown(time.monotonic())
            print('切回选定的游戏窗口，保持前台3秒后识别；Ctrl+C取消。')
            while True:
                result=countdown.update(game.is_ready(),time.monotonic())
                if result=='start': break
                if result=='timeout': raise RuntimeError('等待游戏前台超时，未截图或发送输入。')
                time.sleep(.1)
            print(json.dumps(Detector().analyze(game.capture()),ensure_ascii=False,indent=2))
            return
        from interception_io import open_input_session
        with open_input_session(config) as session:
            return run_loop(config,game,session)
    finally:
        game.close()


def entry(argv=None):
    outcome=0
    detail=''
    mark('启动')
    try:
        main(argv)
    except KeyboardInterrupt:
        outcome=130
        progress(mode='stopped')
    except SystemExit as exc:
        outcome=exc.code if isinstance(exc.code,int) else 1
    except Exception as exc:
        outcome=1
        detail=str(exc)
        logging.exception('运行失败')
    finally:
        mark('结束',done=True,code=outcome,detail=detail)
    return outcome


if __name__=='__main__':
    sys.exit(entry())
