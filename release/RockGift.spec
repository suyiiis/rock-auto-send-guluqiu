# -*- mode: python ; coding: utf-8 -*-
import json
from pathlib import Path
import sys

ROOT=Path(SPECPATH).parent
STAGE=ROOT/'.build-stage'
datas=[(str(ROOT/'assets'),'assets'),(str(STAGE/'default_config.json'),'.'),(str(STAGE/'licenses'),'licenses')]
vendor=ROOT/'vendor/interception'
for relative in ('SOURCE.json','Interception/library/x64/interception.dll','Interception/command line installer/install-interception.exe'):
    path=vendor/relative
    datas.append((str(path),str(Path('vendor/interception')/Path(relative).parent)))
datas.append((str(vendor/'Interception/licenses'),'vendor/interception/Interception/licenses'))

native=[]
for name in ('ffi.dll','tcl86t.dll','tk86t.dll','libmpdec-4.dll'):
    path=Path(sys.base_prefix)/'Library/bin'/name
    if path.exists():native.append((str(path),'.'))
a=Analysis([str(ROOT/'internal/app.py')],pathex=[str(ROOT/'internal')],binaries=native,datas=datas,
    hiddenimports=['mss.windows'],
    excludes=['pip','setuptools','wheel','PyInstaller','pytest','unittest','test','pydoc','doctest',
              'numpy.testing','numpy.f2py','numpy.distutils','numpy._core.tests','numpy._core._multiarray_tests',
              'cv2.data','matplotlib','scipy','pandas','IPython','jupyter','PIL','PyQt5','PyQt6','PySide6',
              'tkinter.test','tkinter.tix','tkinter.dnd','tkinter.colorchooser',
              'ssl','_ssl','_hashlib','http','urllib','email','lzma','_lzma','bz2','_bz2'],
    noarchive=False,optimize=2)

# The bot uses image codecs and template matching; it never opens a video stream.
# Keep the native image-processing core unchanged and remove the optional FFmpeg plugin.
removed=[]
for name in ('binaries','datas'):
    keep=[]
    for entry in getattr(a,name):
        target=entry[0].replace('\\','/').lower()
        if 'opencv_videoio_ffmpeg' in target or '/haarcascades/' in target or target.endswith(('.pdb','.lib','.a','.pyi')):
            removed.append({'file':entry[0],'bytes':Path(entry[1]).stat().st_size})
        else:keep.append(entry)
    setattr(a,name,keep)
(Path(SPECPATH)/'removed_components.json').write_text(json.dumps(removed,indent=2),encoding='utf-8')
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='自动送球',debug=False,strip=False,upx=False,
        console=False,disable_windowed_traceback=False,icon=str(ROOT/'assets/app.ico'),
        version=str(ROOT/'release/version.txt'),uac_admin=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='自动送球')
