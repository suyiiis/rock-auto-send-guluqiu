"""One brief startup check, with a single first-use action when the driver is absent."""
import json
import os
import subprocess
import time
import tkinter as tk
from tkinter import ttk
import uuid
from paths import PROJECT_ROOT, RESOURCE_ROOT, process_command


class FirstRun:
    def __init__(self,root,on_ready=None,can_install=lambda:True,on_close=None):
        self.root=root; self.on_ready=on_ready; self.can_install=can_install
        self.on_close=on_close
        self.process=None; self.started=0; self.installing=False; self.closed=False
        self.report=None
        root.title('自动送球' if on_ready else '自动送球 · 诊断')
        root.configure(bg='white'); root.resizable(False,False); root.attributes('-topmost',False)
        root.protocol('WM_DELETE_WINDOW',self.close)
        icon=RESOURCE_ROOT/'assets/app.ico'
        if icon.exists(): root.iconbitmap(str(icon))
        self.frame=tk.Frame(root,bg='white',padx=24,pady=24)
        self.frame.pack(fill='both',expand=True)
        tk.Label(self.frame,text='自动送球',font=('Microsoft YaHei UI',18,'bold'),fg='#243C30',bg='white').pack(anchor='w')
        self.title=tk.StringVar(value='正在准备…')
        self.detail=tk.StringVar(value='首次启动稍候片刻')
        tk.Label(self.frame,textvariable=self.title,font=('Microsoft YaHei UI',12),fg='#347453',bg='white',anchor='w').pack(fill='x',pady=(24,7))
        tk.Label(self.frame,textvariable=self.detail,font=('Microsoft YaHei UI',10),fg='#76877C',bg='white',wraplength=315,justify='left').pack(anchor='w')
        self.progress=ttk.Progressbar(self.frame,mode='indeterminate',length=310)
        self.progress.pack(fill='x',pady=(18,10));self.progress.start(20)
        self.action=tk.Button(self.frame,text='完成初始化',command=self.install,bg='#D9EFDF',fg='#347453',
                              activebackground='#CBE6D3',relief='flat',bd=0,font=('Microsoft YaHei UI',11),pady=11)
        row=tk.Frame(self.frame,bg='white');row.pack(fill='x',pady=(14,0))
        self.retry=tk.Button(row,text='重新检查',command=self.check,bg='white',fg='#347453',relief='flat',bd=0,cursor='hand2')
        self.retry.pack(side='left')
        tk.Button(row,text='查看日志',command=lambda:os.startfile(str(PROJECT_ROOT/'logs')),bg='white',fg='#76877C',relief='flat',bd=0,cursor='hand2').pack(side='right')
        if on_close:
            tk.Button(self.frame,text='返回',command=self.close,bg='#D9EFDF',fg='#347453',relief='flat',bd=0,pady=8).pack(fill='x',pady=(12,0))
        self.fit()
        self.check()

    def fit(self):
        self.root.update_idletasks()
        self.root.geometry(f'370x{max(280,self.frame.winfo_reqheight()+8)}')

    def start(self,install=False):
        if self.process: return
        self.installing=install
        self.output=PROJECT_ROOT/'logs'/('health_'+uuid.uuid4().hex+'.json')
        self.started=time.monotonic()
        self.title.set('请允许系统安装提示…' if install else '正在准备…')
        self.detail.set('安装完成后会自动检查' if install else '正在检查运行状态')
        self.action.pack_forget();self.retry.configure(state='disabled')
        self.progress.start(20)
        try:
            self.process=subprocess.Popen(process_command('install-driver' if install else 'probe',self.output),
                cwd=PROJECT_ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except Exception as exc:
            self.title.set('启动检查未完成');self.detail.set(str(exc));self.retry.configure(state='normal');self.progress.stop()
            return
        self.root.after(150,self.poll)

    def check(self): self.start()

    def install(self):
        if not self.can_install():
            self.detail.set('请先停止当前赠送任务');return
        self.start(True)

    def poll(self):
        if self.closed or not self.process:return
        if self.process.poll() is None:
            if not self.installing and time.monotonic()-self.started>20:
                self.process.kill();self.process.wait(timeout=3);self.process=None
                self.title.set('检查超时');self.detail.set('点击重新检查');self.retry.configure(state='normal');self.progress.stop()
            else:self.root.after(150,self.poll)
            return
        self.process=None;self.progress.stop();self.retry.configure(state='normal')
        try:self.report=json.loads(self.output.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            self.title.set('程序启动失败');self.detail.set('请重新复制完整的软件文件夹');return
        if self.report.get('ready'):
            self.title.set('✓  准备就绪');self.detail.set('所有运行组件正常')
            if self.on_ready:
                self.closed=True;self.frame.destroy();self.on_ready()
            return
        driver=self.report.get('driver',{})
        if self.report.get('failure'):
            self.title.set('初始化未完成');self.detail.set(self.report['failure'])
        elif self.report.get('runtime',{}).get('status')!='ready':
            self.title.set('程序需要修复');self.detail.set('请重新复制程序文件，保留 data 文件夹');return
        elif driver.get('status')=='restart':
            self.title.set('初始化完成，等待重启');self.detail.set('请手动重启电脑，再打开自动送球');return
        elif driver.get('status')=='unavailable':
            self.title.set('等待键鼠连接');self.detail.set('连接键盘和鼠标后重新检查');return
        else:
            self.title.set('首次使用，完成一次初始化')
            self.detail.set('需要安装键鼠组件，请允许 Windows 授权提示。完成后可能需要重启。')
        self.action.pack(fill='x',before=self.retry.master,pady=(8,0))
        self.fit()

    def close(self):
        self.closed=True
        if self.process and not self.installing and self.process.poll() is None:
            self.process.terminate()
        # A driver installer already approved by the user can finish independently.
        if self.on_close:
            self.frame.destroy();self.on_close()
        else:self.root.destroy()
