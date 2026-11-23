@echo off
setlocal
cd /d "%~dp0"

if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install --quiet --upgrade pip
pip install --quiet pyinstaller pytest

rem The version lives in one place: src\dashcam_merger\__init__.py. It is stamped
rem into both exes (Properties > Details) and used for the zip name.
set VERSION=
for /f %%v in ('python tools\version_info.py') do set VERSION=%%v
if "%VERSION%"=="" exit /b 1
set ZIP=DashCamMerger_v%VERSION%_win64.zip
echo Building DashCam Merger %VERSION%

pytest -m "not integration" -q || exit /b 1
if exist ffmpeg\ffmpeg.exe pytest -m integration -q || exit /b 1

rem PyInstaller 6 collects --add-data under _internal\, but app_dir() and the
rem Spec section 4 layout want ffmpeg\ and config.json beside the exe - and
rem config.json has to stay editable in Notepad. So they are copied, not bundled.
pyinstaller --noconfirm --onedir --windowed --name DashCamMerger ^
  --version-file build\version_info.txt --paths src src\main.py || exit /b 1
pyinstaller --noconfirm --onedir --console --name DashCamMerger-cli ^
  --version-file build\version_info.txt --paths src src\main.py || exit /b 1

copy /y dist\DashCamMerger-cli\DashCamMerger-cli.exe dist\DashCamMerger\ >nul
xcopy /y /i /q ffmpeg dist\DashCamMerger\ffmpeg >nul
copy /y config.json dist\DashCamMerger\ >nul
copy /y README.txt dist\DashCamMerger\ >nul
copy /y CHANGELOG.md dist\DashCamMerger\ >nul
if not exist dist\DashCamMerger\logs mkdir dist\DashCamMerger\logs

powershell -NoProfile -Command "Compress-Archive -Force -Path dist\DashCamMerger\* -DestinationPath %ZIP%"
powershell -NoProfile -Command "(Get-FileHash %ZIP% -Algorithm SHA256).Hash" > DashCamMerger_v%VERSION%_win64.sha256

echo.
echo Built dist\DashCamMerger\ and %ZIP%
type DashCamMerger_v%VERSION%_win64.sha256
endlocal
