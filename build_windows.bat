@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name SplitTask main.py
if errorlevel 1 goto fail
echo Built dist\SplitTask\SplitTask.exe. Keep the entire SplitTask folder together.
pause
exit /b 0
:fail
echo Build failed. See the error above.
pause
exit /b 1
