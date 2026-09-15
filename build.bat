@echo off
setlocal
cd /d "%~dp0"

if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install --quiet --upgrade pip
pip install --quiet pyinstaller pytest

pytest -m "not integration" -q || exit /b 1
if exist ffmpeg\ffmpeg.exe pytest -m integration -q || exit /b 1

rem PyInstaller 6 collects --add-data under _internal\, but app_dir() and the
rem Spec section 4 layout want ffmpeg\ and config.json beside the exe - and
rem config.json has to stay editable in Notepad. So they are copied, not bundled.
pyinstaller --noconfirm --onedir --windowed --name DashCamMerger ^
  --paths src src\main.py || exit /b 1
pyinstaller --noconfirm --onedir --console --name DashCamMerger-cli ^
  --paths src src\main.py || exit /b 1

copy /y dist\DashCamMerger-cli\DashCamMerger-cli.exe dist\DashCamMerger\ >nul
xcopy /y /i /q ffmpeg dist\DashCamMerger\ffmpeg >nul
copy /y config.json dist\DashCamMerger\ >nul
copy /y README.txt dist\DashCamMerger\ >nul
copy /y CHANGELOG.md dist\DashCamMerger\ >nul
if not exist dist\DashCamMerger\logs mkdir dist\DashCamMerger\logs

powershell -NoProfile -Command "Compress-Archive -Force -Path dist\DashCamMerger\* -DestinationPath DashCamMerger_v1.0_win64.zip"
powershell -NoProfile -Command "(Get-FileHash DashCamMerger_v1.0_win64.zip -Algorithm SHA256).Hash" > DashCamMerger_v1.0_win64.sha256

echo.
echo Built dist\DashCamMerger\ and DashCamMerger_v1.0_win64.zip
type DashCamMerger_v1.0_win64.sha256
endlocal
