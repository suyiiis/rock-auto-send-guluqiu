"""Reproducible minimal Windows application build. Produces no ZIP archive."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from prepare_vendor import prepare as prepare_vendor

ROOT=Path(__file__).resolve().parents[1]
RELEASE=Path(__file__).resolve().parent
STAGE=ROOT/'.build-stage'
PORTABLE=ROOT/'dist/自动送球'


def create_icon():
    import cv2
    import numpy as np
    entries=[]
    for size in (32,48,64,128,256):
        s=size/64
        canvas=np.zeros((size,size,4),np.uint8)
        def rect(a,b,color):cv2.rectangle(canvas,tuple(round(v*s) for v in a),tuple(round(v*s) for v in b),color,-1)
        # Small, flat gift mark; no external raster assets or icon font.
        rect((3,3),(60,60),(237,248,241,255))
        rect((14,28),(50,52),(191,229,203,255))
        rect((12,23),(52,31),(169,214,185,255))
        green=(83,116,52,255)
        rect((30,23),(33,52),green)
        for center in ((25,19),(39,19)):
            cv2.ellipse(canvas,tuple(round(v*s) for v in center),(round(8*s),round(5*s)),25 if center[0]<32 else -25,0,360,green,max(1,round(2*s)),cv2.LINE_AA)
        entries.append((size,cv2.imencode('.png',canvas)[1].tobytes()))
    offset=6+16*len(entries)
    data=bytearray(struct.pack('<HHH',0,1,len(entries)))
    for size,png in entries:
        data+=struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(png),offset)
        offset+=len(png)
    for _,png in entries:data+=png
    (ROOT/'assets/app.ico').write_bytes(data)


def prepare():
    if sys.platform!='win32' or struct.calcsize('P')!=8:
        raise RuntimeError('请在 Windows x64 上使用 64 位 Python 构建。')
    import tkinter
    prepare_vendor()
    STAGE.mkdir(exist_ok=True)
    create_icon()
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    config['interception'].update(keyboard_id=None,mouse_id=None,monitor_keyboard=False)
    config.update(max_rounds=0,save_live_trace=False)
    (STAGE/'default_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    licenses=STAGE/'licenses';licenses.mkdir(exist_ok=True)
    for distname in ('numpy','opencv-python-headless','mss','pyinstaller'):
        dist=importlib.metadata.distribution(distname)
        for file in dist.files or []:
            if any(p.lower() in ('licenses','license','license.txt','license-3rd-party.txt','copying.txt','copying') for p in file.parts):
                source=dist.locate_file(file)
                if source.is_file():shutil.copy2(source,licenses/(distname+'_'+file.name))
    base=Path(sys.base_prefix)
    for name in ('LICENSE.txt','LICENSE','LICENSE_PYTHON.txt'):
        if (base/name).exists():shutil.copy2(base/name,licenses/'Python_LICENSE.txt');break
    for component in ('tk','libffi','libmpdec'):
        for package in (base/'pkgs').glob(component+'-*'):
            for source in (package/'info/licenses').rglob('*'):
                if source.is_file():shutil.copy2(source,licenses/(component+'_'+source.name))
    # The official Python installer stores Tcl/Tk licenses below tcl/.
    for source in (base/'tcl').rglob('license.terms'):
        shutil.copy2(source,licenses/(source.parent.name+'_license.terms'))
    (licenses/'NOTICE.txt').write_text('自动送球使用 Python、Tcl/Tk、NumPy、OpenCV、MSS、PyInstaller 和 Interception。\nInterception 的许可证与来源保留于 vendor/interception。\n原始组件未作二进制修改。\n',encoding='utf-8')


if __name__=='__main__':
    prepare()
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',
        '--distpath',str(ROOT/'dist'), '--workpath',str(ROOT/'.build-pyinstaller'),str(RELEASE/'RockGift.spec')],check=True,cwd=ROOT)
    folder=PORTABLE
    shutil.copy2(RELEASE/'portable_readme.txt',PORTABLE/'使用说明.txt')
    files=[p for p in folder.rglob('*') if p.is_file()]
    size=sum(p.stat().st_size for p in files)
    result={'version':'2026.09.12-gift8-portable','portable_folder':str(PORTABLE),'logical_bytes':size,'files':len(files),
            'largest':[{'file':str(p.relative_to(folder)),'bytes':p.stat().st_size} for p in sorted(files,key=lambda p:p.stat().st_size,reverse=True)[:20]]}
    (RELEASE/'size_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
