# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd()

datas = []
for source, target in (
    (ROOT / "dashboard" / "static", "dashboard/static"),
    (ROOT / ".env.example", "."),
):
    if source.exists():
        datas.append((str(source), target))

hiddenimports = []
for package in (
    "ccxt",
    "fastapi",
    "uvicorn",
    "pydantic",
    "sklearn",
    "hmmlearn",
    "xgboost",
    "lightgbm",
    "pandas",
    "numpy",
    "scipy",
    "ta",
    "joblib",
    "requests",
    "dotenv",
    "msgpack",
):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        hiddenimports.append(package)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SniperBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SniperBot",
)
