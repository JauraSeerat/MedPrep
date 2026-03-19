$ErrorActionPreference = "Stop"

Write-Host "[1/5] Creating venv"
python -m venv .venv

Write-Host "[2/5] Activating venv"
.\.venv\Scripts\Activate.ps1

Write-Host "[3/5] Installing dependencies"
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

Write-Host "[4/5] Building app"
pyinstaller --noconfirm --windowed --name MedPrepAI run_app.py --collect-all faster_whisper --collect-all ctranslate2 --collect-all sounddevice --collect-all soundfile --collect-all cv2

Write-Host "[5/5] Done"
Write-Host "App is in dist\MedPrepAI\MedPrepAI.exe"
