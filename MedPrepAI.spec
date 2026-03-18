# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(__file__).resolve().parent
app_entry = project_root / "run_app.py"

# Include knowledge base samples; avoid bundling local interview recordings.
datas = [
    (str(project_root / "data" / "knowledge_bases"), "data/knowledge_bases"),
]

# Optional/late imports used at runtime.
hiddenimports = [
    "sounddevice",
    "soundfile",
    "cv2",
    "faster_whisper",
    "sentence_transformers",
]

block_cipher = None


a = Analysis(
    [str(app_entry)],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MedPrepAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MedPrepAI",
)
