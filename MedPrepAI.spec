# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

project_root = Path(__file__).resolve().parent
app_entry = project_root / "run_app.py"

# Include knowledge base samples; avoid bundling local interview recordings.
datas = [
    (str(project_root / "data" / "knowledge_bases"), "data/knowledge_bases"),
]

fw_datas, fw_binaries, fw_hidden = collect_all("faster_whisper")
ct_datas, ct_binaries, ct_hidden = collect_all("ctranslate2")
sd_datas, sd_binaries, sd_hidden = collect_all("sounddevice")
sf_datas, sf_binaries, sf_hidden = collect_all("soundfile")
cv_datas, cv_binaries, cv_hidden = collect_all("cv2")
datas += fw_datas + ct_datas + sd_datas + sf_datas + cv_datas

# Optional/late imports used at runtime.
hiddenimports = [
    "sounddevice",
    "soundfile",
    "cv2",
    "faster_whisper",
    "sentence_transformers",
]
hiddenimports += fw_hidden + ct_hidden + sd_hidden + sf_hidden + cv_hidden

block_cipher = None


a = Analysis(
    [str(app_entry)],
    pathex=[str(project_root)],
    binaries=fw_binaries + ct_binaries + sd_binaries + sf_binaries + cv_binaries,
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
