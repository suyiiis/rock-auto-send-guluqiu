"""Small offline health check and explicit first-use driver installation."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time
from paths import PROJECT_ROOT

PENDING = PROJECT_ROOT/'driver_pending.json'


def save_report(path, report):
    from panel_service import write_json
    write_json(path, report)


def check_driver():
    from interception_io import NativeContext
    try:
        context=NativeContext()
        try: devices=context.devices()
        finally: context.close()
        if {d['kind'] for d in devices} >= {'keyboard','mouse'}:
            return {'status':'ready','detail':'键鼠组件正常','devices':devices}
        return {'status':'unavailable','detail':'请连接键盘和鼠标'}
    except Exception as exc:
        status='missing'
        try:
            pending=json.loads(PENDING.read_text(encoding='utf-8'))
            kernel=ctypes.WinDLL('kernel32')
            kernel.GetTickCount64.restype=ctypes.c_ulonglong
            boot=time.time()-kernel.GetTickCount64()/1000
            status='restart' if pending['installed_at']>=boot-5 else 'failed'
        except (OSError,ValueError,KeyError): pass
        return {'status':status,'detail':'请重启电脑后重新打开' if status=='restart' else '需要初始化键鼠组件',
                'error':str(exc)}


def health_report():
    from bot import VERSION
    report={'version':VERSION,'ready':False,'runtime':{'status':'error'},'driver':{'status':'unknown'}}
    try:
        import cv2
        import numpy as np
        import mss
        from vision import Detector
        from windows_io import _api
        from interception_io import choose_device
        _api()
        detector=Detector()
        if detector.analyze(np.zeros((1080,1920,3),dtype=np.uint8))['state']!='unknown':
            raise RuntimeError('识别组件自检失败')
        # Opening the capture backend validates its native resources without reading the screen.
        with mss.mss(): pass
        report['runtime']={'status':'ready','detail':'运行组件正常','opencv':cv2.__version__,'numpy':np.__version__}
        report['driver']=check_driver()
        if report['driver']['status']=='ready':
            devices=report['driver']['devices']
            report['selected_devices']={kind:choose_device(devices,kind)['id'] for kind in ('keyboard','mouse')}
        report['ready']=report['driver']['status']=='ready'
    except Exception as exc:
        report['runtime']={'status':'error','detail':'程序文件不完整，请重新复制完整的软件文件夹','error':str(exc)}
    return report


def install_driver():
    from interception_io import verified_file
    from run_lease import single_run
    current=check_driver()
    if current['status'] in {'ready','restart'}: return health_report()
    installer=verified_file('Interception/command line installer/install-interception.exe')
    class ShellInfo(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('fMask',wintypes.ULONG),('hwnd',wintypes.HWND),
                  ('lpVerb',wintypes.LPCWSTR),('lpFile',wintypes.LPCWSTR),('lpParameters',wintypes.LPCWSTR),
                  ('lpDirectory',wintypes.LPCWSTR),('nShow',ctypes.c_int),('hInstApp',wintypes.HINSTANCE),
                  ('lpIDList',ctypes.c_void_p),('lpClass',wintypes.LPCWSTR),('hkeyClass',wintypes.HKEY),
                  ('dwHotKey',wintypes.DWORD),('hIcon',wintypes.HANDLE),('hProcess',wintypes.HANDLE)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ShellInfo)]
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.SetDllDirectoryW.argtypes=[wintypes.LPCWSTR]
    kernel.GetDllDirectoryW.argtypes=[wintypes.DWORD,wintypes.LPWSTR]
    with single_run(PROJECT_ROOT/'logs/driver_install.lock'), single_run():
        info=ShellInfo(cbSize=ctypes.sizeof(ShellInfo),fMask=0x40,lpVerb='runas',lpFile=str(installer),
                       lpParameters='/install',lpDirectory=str(installer.parent),nShow=0)
        previous=ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(previous),previous)
        kernel.SetDllDirectoryW(None)
        try:
            if not shell.ShellExecuteExW(ctypes.byref(info)):
                code=ctypes.get_last_error()
                raise RuntimeError('安装已取消，可稍后重试' if code==1223 else f'无法启动安装（{code}）')
        finally: kernel.SetDllDirectoryW(previous.value or None)
        if not info.hProcess: raise RuntimeError('未获得安装状态，请重新检查')
        try:
            kernel.WaitForSingleObject(info.hProcess,0xFFFFFFFF)
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(info.hProcess,ctypes.byref(code)) or code.value not in (0,3010):
                raise RuntimeError('组件安装未完成，请重试')
        finally: kernel.CloseHandle(info.hProcess)
        save_report(PENDING,{'installed_at':time.time()})
    return health_report()


def run_probe(output, install=False):
    try: report=install_driver() if install else health_report()
    except Exception as exc:
        report=health_report()
        report['failure']=str(exc)
    save_report(output,report)
    return 0 if report['ready'] else 1
