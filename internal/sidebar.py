"""Movable control window with visible interruption status and round limit."""
import ctypes
from ctypes import wintypes
import os
import time
import tkinter as tk
from tkinter import ttk
from bot import read_config
from panel_service import PanelSession, updated_config, save_config
from paths import PROJECT_ROOT, RESOURCE_ROOT
from panel_state import GREEN_THEME, theme_for, interruption_text, INTERRUPTED

BG=GREEN_THEME['bg']
CARD=GREEN_THEME['card']
FIELD=GREEN_THEME['field']
TEXT=GREEN_THEME['text']
MUTED=GREEN_THEME['muted']
GREEN=GREEN_THEME['accent']
MINT=GREEN_THEME['mint']
LINE=GREEN_THEME['line']
FONT=('Microsoft YaHei UI',10)


def work_area(hwnd):
    class MonitorInfo(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]
    u=ctypes.windll.user32
    u.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
    u.MonitorFromWindow.restype=wintypes.HANDLE
    u.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
    info=MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not u.GetMonitorInfoW(u.MonitorFromWindow(hwnd,2),ctypes.byref(info)):
        raise RuntimeError('无法读取显示器工作区')
    r=info.work
    return r.left,r.top,r.right,r.bottom


class Sidebar:
    def __init__(self,root):
        self.root=root
        self.config=read_config(PROJECT_ROOT/'config.json')
        self.session=PanelSession()
        self.windows=[]
        self._hwnd=None
        self.collapsed=False
        self.closing=False
        self.diagnosing=False
        self.mode='idle'
        self._theme_name=None
        self._positioned=False
        self.show_stamp=time.time()
        self.last_refresh=0
        root.title('自动送球')
        root.configure(bg=BG)
        root.resizable(False,False)
        root.attributes('-topmost',False)
        root.protocol('WM_DELETE_WINDOW',self.close)
        if (RESOURCE_ROOT/'assets/app.ico').exists(): root.iconbitmap(str(RESOURCE_ROOT/'assets/app.ico'))
        self.rounds=tk.StringVar(value=str(self.config['max_rounds']))
        self.window_choice=tk.StringVar()
        self.mode_text=tk.StringVar(value='待机')
        self.phase_text=tk.StringVar(value='可以开始')
        self.count_text=tk.StringVar(value='0 / ∞')
        self.notice=tk.StringVar(value='')
        self.reason_text=tk.StringVar(value='')
        self.primary_text=tk.StringVar(value='▶  开始赠送')
        style=ttk.Style(root)
        style.theme_use('clam')
        style.configure('TCombobox',fieldbackground=FIELD,background=BG,foreground=TEXT,arrowcolor=GREEN,bordercolor=LINE,padding=5)
        style.map('TCombobox',fieldbackground=[('readonly',FIELD)],selectbackground=[('readonly',FIELD)],selectforeground=[('readonly',TEXT)])
        self._build()
        self._record_theme()
        self.apply_theme('idle')
        self.refresh_windows()
        self.apply_window_settings()
        root.after(200,self.poll)

    def label(self,parent,text=None,variable=None,color=TEXT,size=10,bold=False,**kw):
        return tk.Label(parent,text=text,textvariable=variable,bg=parent.cget('bg'),fg=color,
                        font=('Microsoft YaHei UI',size,'bold' if bold else 'normal'),**kw)

    def button(self,parent,text=None,command=None,accent=False,variable=None,**kw):
        return tk.Button(parent,text=text,textvariable=variable,command=command,relief='flat',bd=0,
                         highlightthickness=0,cursor='hand2',bg=MINT if accent else FIELD,fg=GREEN if accent else TEXT,
                         activebackground=GREEN_THEME['active'] if accent else LINE,activeforeground=TEXT,
                         disabledforeground=GREEN_THEME['disabled'],font=FONT,padx=10,pady=11,**kw)

    def _build(self):
        self.body=tk.Frame(self.root,bg=BG,padx=22,pady=20)
        self.body.pack(fill='both',expand=True)
        header=tk.Frame(self.body,bg=BG)
        header.pack(fill='x',pady=(0,22))
        icon=tk.Canvas(header,width=32,height=32,bg=BG,highlightthickness=0)
        icon.pack(side='left',padx=(0,10))
        icon.create_rectangle(4,12,28,28,fill=MINT,outline='')
        icon.create_rectangle(2,10,30,15,fill=MINT,outline='')
        icon.create_line(16,10,16,28,fill=GREEN,width=2)
        icon.create_oval(7,3,16,11,outline=GREEN,width=2)
        icon.create_oval(16,3,25,11,outline=GREEN,width=2)
        self.label(header,'自动送球',size=16,bold=True).pack(side='left')
        self.collapse_icon=tk.PhotoImage(width=16,height=16)
        self.collapse_icon.put(FIELD,to=(0,0,16,16))
        self.collapse_icon.put(GREEN,to=(3,7,13,9))
        self.button(header,'',self.collapse,image=self.collapse_icon,width=16,height=16).pack(side='right')
        self.button(header,'?',self.diagnostics,width=2).pack(side='right',padx=(0,4))
        status=tk.Frame(self.body,bg=CARD,padx=20,pady=18)
        status.pack(fill='x')
        badge=tk.Frame(status,bg=CARD)
        badge.pack(fill='x')
        dot=tk.Canvas(badge,width=10,height=10,bg=CARD,highlightthickness=0)
        dot.create_oval(2,2,8,8,fill=GREEN,outline='')
        dot.pack(side='left',padx=(0,5))
        self.label(badge,variable=self.mode_text,size=9,color=GREEN).pack(side='left')
        self.label(status,variable=self.count_text,size=30,bold=True,anchor='w').pack(fill='x',pady=(13,1))
        self.label(status,variable=self.phase_text,size=9,color=MUTED,anchor='w',wraplength=278,justify='left').pack(fill='x')
        self.reason_label=self.label(status,variable=self.reason_text,size=10,color=TEXT,
                                     anchor='w',wraplength=278,justify='left')
        self.target=tk.Frame(self.body,bg=BG)
        self.window_box=ttk.Combobox(self.target,textvariable=self.window_choice,state='readonly',font=FONT,width=22)
        self.window_box.pack(fill='x')
        self.window_box.bind('<<ComboboxSelected>>',self.select_window)
        settings=tk.Frame(self.body,bg=BG)
        settings.pack(fill='x',pady=(23,22))
        row=tk.Frame(settings,bg=BG)
        row.pack(fill='x',pady=(0,9))
        self.label(row,'赠送次数',bold=True).pack(side='left')
        self.label(row,'0 为不限',size=9,color=MUTED).pack(side='right')
        field=tk.Frame(settings,bg=LINE,padx=1,pady=1)
        field.pack(fill='x')
        self.rounds_entry=tk.Entry(field,textvariable=self.rounds,bg=FIELD,fg=TEXT,insertbackground=GREEN,
                                   relief='flat',bd=0,font=('Microsoft YaHei UI',16),justify='center',
                                   disabledbackground=FIELD,disabledforeground=MUTED,width=12)
        self.rounds_entry.pack(fill='x',ipady=8)
        self.rounds_entry.bind('<Return>',lambda _e:self.primary())
        controls=tk.Frame(self.body,bg=BG)
        controls.pack(fill='x')
        controls.columnconfigure(0,weight=2)
        controls.columnconfigure(1,weight=1)
        self.primary_button=self.button(controls,command=self.primary,variable=self.primary_text,accent=True)
        self.primary_button.grid(row=0,column=0,sticky='ew',padx=(0,8))
        self.stop_button=self.button(controls,'■  停止',lambda:self.control('stop'))
        self.stop_button.grid(row=0,column=1,sticky='ew')
        self.label(self.body,variable=self.notice,color=MUTED,size=9,wraplength=310,justify='left',anchor='w').pack(fill='x',pady=(12,0))
        self.log_button=self.button(self.body,'查看日志',self.open_logs)
        self.compact=tk.Frame(self.root,bg=BG,padx=8,pady=8)
        self.label(self.compact,variable=self.count_text,size=11,bold=True).pack(pady=(0,6))
        self.label(self.compact,variable=self.mode_text,size=9,color=GREEN).pack(pady=(0,6))
        self.button(self.compact,'展开',self.expand,accent=True).pack(fill='x')
        self.button(self.compact,'■  停止',lambda:self.control('stop')).pack(fill='x',pady=(6,0))

    def _record_theme(self):
        """Remember semantic colors once, including hidden and compact widgets."""
        roles={color:role for role,color in GREEN_THEME.items()}
        self._theme_widgets=[]
        def visit(widget):
            settings={}
            for option in ('background','foreground','activebackground','activeforeground',
                           'disabledbackground','disabledforeground','insertbackground'):
                try: role=roles.get(str(widget.cget(option)))
                except tk.TclError: continue
                if role: settings[option]=role
            items=[]
            if isinstance(widget,tk.Canvas):
                for item in widget.find_all():
                    for option in ('fill','outline'):
                        try: role=roles.get(widget.itemcget(item,option))
                        except tk.TclError: continue
                        if role: items.append((item,option,role))
            self._theme_widgets.append((widget,settings,items))
            for child in widget.winfo_children(): visit(child)
        visit(self.root)

    def apply_theme(self,mode):
        palette=theme_for(mode)
        name='orange' if mode in INTERRUPTED else 'green'
        if name==self._theme_name: return
        for widget,settings,items in self._theme_widgets:
            widget.configure(**{key:palette[role] for key,role in settings.items()})
            for item,option,role in items: widget.itemconfigure(item,**{option:palette[role]})
        self.collapse_icon.put(palette['field'],to=(0,0,16,16))
        self.collapse_icon.put(palette['accent'],to=(3,7,13,9))
        style=ttk.Style(self.root)
        style.configure('TCombobox',fieldbackground=palette['field'],background=palette['bg'],
                        foreground=palette['text'],arrowcolor=palette['accent'],bordercolor=palette['line'])
        style.map('TCombobox',fieldbackground=[('readonly',palette['field'])],
                  selectbackground=[('readonly',palette['field'])],selectforeground=[('readonly',palette['text'])])
        self._theme_name=name

    def select_window(self,_event=None):
        i=self.window_box.current()
        if 0<=i<len(self.windows): self._hwnd=self.windows[i]['hwnd']

    def refresh_windows(self):
        from windows_io import enumerate_windows,GameWindow
        self.last_refresh=time.monotonic()
        try:
            found=[]
            for item in enumerate_windows():
                if '洛克王国' not in item['title']: continue
                try:
                    w=GameWindow(item['hwnd'])
                    try:
                        if w.readiness_problem(require_foreground=False) is None: found.append(item)
                    finally: w.close()
                except Exception: continue
            self.windows=found
            if not any(w['hwnd']==self._hwnd for w in found):
                self._hwnd=found[0]['hwnd'] if len(found)==1 else None
            labels=[f"{i+1}. {w['title'].strip()}" for i,w in enumerate(found)]
            self.window_box['values']=labels
            if len(found)>1:
                if not self.target.winfo_manager(): self.target.pack(after=self.body.winfo_children()[1],fill='x',pady=(15,0))
                selected=next((i for i,w in enumerate(found) if w['hwnd']==self._hwnd),None)
                self.window_choice.set(labels[selected] if selected is not None else '请选择游戏窗口')
            else: self.target.pack_forget()
        except Exception: self._hwnd=None

    def selected_hwnd(self):
        return self._hwnd

    def apply_window_settings(self):
        self.root.update_idletasks()
        left,top,right,bottom=work_area(self._hwnd or self.root.winfo_id())
        width=136 if self.collapsed else max(360,self.body.winfo_reqwidth())
        content=self.compact if self.collapsed else self.body
        height=min(content.winfo_reqheight()+4,bottom-top-70)
        if not self._positioned:
            x=left+(right-left-width)//2
            y=top+max(12,(bottom-top-height)//2)
            self.root.geometry(f'{width}x{height}{x:+d}{y:+d}')
            self._positioned=True
        elif (self.root.winfo_width(),self.root.winfo_height())!=(width,height):
            # Size-only geometry preserves a position chosen by dragging the title bar.
            self.root.geometry(f'{width}x{height}')

    def overlaps_game(self):
        if not self._hwnd: return False
        from windows_io import GameWindow
        game=GameWindow(self._hwnd)
        try: x,y,w,h=game.rect()
        finally: game.close()
        px,py,pw,ph=self.root.winfo_rootx(),self.root.winfo_rooty(),self.root.winfo_width(),self.root.winfo_height()
        return px<x+w and px+pw>x and py<y+h and py+ph>y

    def collapse(self):
        self.body.pack_forget()
        self.compact.pack(fill='both',expand=True)
        self.collapsed=True
        self.apply_window_settings()

    def expand(self):
        self.compact.pack_forget()
        self.body.pack(fill='both',expand=True)
        self.collapsed=False
        self.apply_window_settings()
        self.root.deiconify()

    def primary(self):
        if self.session.running: self.control('resume' if self.mode=='paused' else 'pause')
        else: self.start()

    def start(self):
        try: self.config=save_config(updated_config(self.config,{'max_rounds':self.rounds.get()}))
        except (ValueError,TypeError):
            self.notice.set('请输入 0 或正整数')
            return
        except Exception:
            self.notice.set('次数保存失败，请重试')
            return
        self.refresh_windows()
        if not self._hwnd:
            self.notice.set('请先打开游戏' if not self.windows else '请选择游戏窗口')
            return
        try:
            self.session.start(self._hwnd,self.config)
            self.notice.set('')
        except Exception as exc:
            self.session.last_status={'mode':'error','interruption_reason':f'启动失败：{exc}'}
            self.notice.set('')
            self.log_button.pack(fill='x',pady=(8,0))
            self.expand()

    def control(self,action):
        if not self.session.running: return
        self.session.command(action)
        self.notice.set('正在停止…' if action=='stop' else '')

    def open_logs(self):
        os.startfile(str(PROJECT_ROOT/'logs'))

    def diagnostics(self):
        from first_run import FirstRun
        if self.session.running:self.session.command('pause')
        self.diagnosing=True
        self.body.pack_forget();self.compact.pack_forget()
        def restore():
            self.diagnosing=False
            self.root.title('自动送球');self.root.protocol('WM_DELETE_WINDOW',self.close)
            self.expand()
        self.diagnostic=FirstRun(self.root,can_install=lambda:not self.session.running,on_close=restore)
        self.root.protocol('WM_DELETE_WINDOW',self.close)

    def poll(self):
        if self.closing: return
        status=self.session.poll()
        if self.diagnosing:
            self.root.after(350,self.poll);return
        running=self.session.running
        if not running and time.monotonic()-self.last_refresh>3: self.refresh_windows()
        mode=status.get('mode','idle')
        previous_mode=self.mode
        self.mode=mode
        self.apply_theme(mode)
        reason=interruption_text(status)
        self.reason_text.set(reason)
        if reason:
            self.reason_label.pack(fill='x',pady=(12,0))
            if self.collapsed: self.expand()
        else: self.reason_label.pack_forget()
        labels={'idle':'待机','starting':'准备中','countdown':'准备中','running':'运行中','paused':'已暂停','stopped':'已停止','complete':'已完成','error':'需检查'}
        self.mode_text.set(labels.get(mode,'准备中'))
        if mode in {'idle','stopped','complete'}:
            phase='本次已结束' if mode!='idle' else '可以开始' if self._hwnd else '等待游戏连接'
        elif mode in {'starting','countdown'}: phase='切回游戏，3 秒后开始'
        elif mode=='paused': phase='点击继续恢复'
        elif mode=='error': phase='本次运行已中断'
        else:
            target=status.get('pending') or status.get('phase')
            phase={'detect':'识别游戏画面','unready':'准备赠送','prepare1':'准备赠送','prepare2':'准备赠送',
                   'gift1':'打开赠送','gift2':'确认赠送','giving':'等待领取','done':'本次已结束'}.get(target,'准备赠送')
        self.phase_text.set(phase)
        total=status.get('max_rounds',self.config['max_rounds'])
        self.count_text.set(f"{status.get('rounds',0)} / {total if total else '∞'}")
        self.primary_text.set('▶  继续' if running and mode=='paused' else 'Ⅱ  暂停' if running else '▶  开始赠送')
        self.primary_button.configure(state='disabled' if self.session.stopping and running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        self.rounds_entry.configure(state='disabled' if running else 'normal')
        self.window_box.configure(state='disabled' if running else 'readonly')
        if mode!=previous_mode and mode in {'running','stopped','complete'}: self.notice.set('')
        if mode in {'error','paused'}: self.log_button.pack(fill='x',pady=(8,0))
        elif self.log_button.winfo_manager(): self.log_button.pack_forget()
        if not self.collapsed: self.apply_window_settings()
        try:
            stamp=(PROJECT_ROOT/'logs/show_panel.request').stat().st_mtime
            if stamp>self.show_stamp:
                self.show_stamp=stamp
                self.expand()
        except FileNotFoundError: pass
        self.root.after(350,self.poll)

    def close(self):
        self.closing=True
        if self.session.running: self.session.command('stop')
        deadline=time.monotonic()+4
        def finish():
            if not self.session.running:
                self.session.poll()
                self.root.destroy()
            elif time.monotonic()>=deadline:
                import threading
                threading.Thread(target=self.session.force_stop,daemon=False).start()
                self.root.destroy()
            else: self.root.after(100,finish)
        finish()


def main():
    from windows_io import _api
    from run_lease import single_run
    _api()
    try:
        with single_run(PROJECT_ROOT/'logs/sidebar.lock'):
            root=tk.Tk()
            Sidebar(root)
            root.mainloop()
    except RuntimeError as exc:
        if '已有脚本' not in str(exc): raise
        (PROJECT_ROOT/'logs/show_panel.request').write_text(str(time.time()),encoding='utf-8')


if __name__=='__main__':
    try: main()
    except Exception:
        import traceback
        (PROJECT_ROOT/'logs/sidebar_error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
