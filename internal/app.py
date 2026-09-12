"""Frozen executable entry: UI, supervisor, worker and offline diagnostics."""
import json
import os
from pathlib import Path
import sys
from paths import PROJECT_ROOT, RESOURCE_ROOT, initialize_data


def main(arguments=None):
    args=list(sys.argv[1:] if arguments is None else arguments)
    initialize_data()
    # Windowed builds have no console; regular log files still receive diagnostics.
    if sys.stdout is None:sys.stdout=open(os.devnull,'w',encoding='utf-8')
    if sys.stderr is None:sys.stderr=open(os.devnull,'w',encoding='utf-8')
    if args and args[0]=='--worker':
        from bot import entry
        return entry(args[1:])
    if args and args[0]=='--supervisor':
        from safe_launcher import entry
        return entry(args[1:])
    if args and args[0] in ('--probe','--install-driver'):
        from application_health import run_probe
        return run_probe(Path(args[1]),install=args[0]=='--install-driver')
    if args and args[0]=='--verify-images':
        import cv2
        import numpy as np
        from vision import Detector
        from application_health import save_report
        detector=Detector(); observations=[]
        for file in sorted(Path(args[1]).glob('*.png')):
            frame=cv2.imdecode(np.fromfile(str(file),np.uint8),cv2.IMREAD_COLOR)
            if frame.shape[:2]==(1127,1924):frame=frame[45:1125,2:1922]
            observations.append({'file':file.name,**detector.analyze(frame)})
        save_report(args[2],{'observations':observations})
        return 0
    if args:
        raise ValueError('未知启动参数')
    from windows_io import _api
    from run_lease import single_run
    from first_run import FirstRun
    from sidebar import Sidebar
    import tkinter as tk
    _api()
    try:
        with single_run(PROJECT_ROOT/'logs/sidebar.lock'):
            root=tk.Tk()
            FirstRun(root,on_ready=lambda:Sidebar(root))
            root.mainloop()
    except RuntimeError as exc:
        if '已有脚本' not in str(exc):raise
        (PROJECT_ROOT/'logs/show_panel.request').write_text('show',encoding='utf-8')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception:
        import traceback
        detail=traceback.format_exc()
        try:
            (PROJECT_ROOT/'logs').mkdir(parents=True,exist_ok=True)
            (PROJECT_ROOT/'logs/application_error.log').write_text(detail,encoding='utf-8')
        except OSError:pass
        if len(sys.argv)==1:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'软件未能启动。请保持整个软件文件夹完整，并放在可写入的位置（例如桌面）。','自动送球',0x10)
        sys.exit(1)
