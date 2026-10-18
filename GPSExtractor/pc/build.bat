@echo off
setlocal
cd /d "%~dp0"

if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt pyinstaller pytest

python tools\fetch_models.py || exit /b 1
pytest -m "not integration" -q || exit /b 1

rem One onedir build for the window, one for the console; the console exe is copied
rem next to the window exe so both share _internal\ (same as DashCam Merger).
pyinstaller --noconfirm --onedir --windowed --name DashGPS ^
  --add-data "dashgps\models;dashgps\models" main.py || exit /b 1
pyinstaller --noconfirm --onedir --console --name DashGPS-cli ^
  --add-data "dashgps\models;dashgps\models" main.py || exit /b 1

copy /y dist\DashGPS-cli\DashGPS-cli.exe dist\DashGPS\ >nul
copy /y README.md dist\DashGPS\ >nul

powershell -NoProfile -Command "Compress-Archive -Force -Path dist\DashGPS\* -DestinationPath DashGPS_v1.0_win64.zip"
powershell -NoProfile -Command "(Get-FileHash DashGPS_v1.0_win64.zip -Algorithm SHA256).Hash" > DashGPS_v1.0_win64.sha256

echo.
echo Built dist\DashGPS\ and DashGPS_v1.0_win64.zip
type DashGPS_v1.0_win64.sha256
endlocal
