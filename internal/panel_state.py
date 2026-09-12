"""Presentation state for the movable control window."""
GREEN_THEME = dict(bg='#F2FAF4', card='#E0F2E6', field='#FFFFFF', text='#243C30',
                   muted='#617C6A', accent='#347453', mint='#C9E8D4', line='#DCEBE1',
                   active='#B8DCC5', disabled='#A7BAAE')
ORANGE_THEME = dict(bg='#FFF3E6', card='#FFE3C0', field='#FFFAF4', text='#603A17',
                    muted='#946333', accent='#B86218', mint='#FFD3A2', line='#F2D8BC',
                    active='#F6BD7E', disabled='#C2A487')
INTERRUPTED = {'paused', 'error', 'stopped'}


def theme_for(mode):
    return ORANGE_THEME if mode in INTERRUPTED else GREEN_THEME


def interruption_text(status):
    mode = status.get('mode', 'idle')
    if mode not in INTERRUPTED:
        return ''
    reason = status.get('interruption_reason') or status.get('detail')
    if reason:
        return str(reason)
    return {'paused': '任务已暂停。点击继续恢复。',
            'stopped': '用户已停止本次任务。',
            'error': '运行进程意外结束，任务未完成。请查看日志。'}[mode]
