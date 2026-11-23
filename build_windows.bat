@echo off
REM Gera "XPS Sample Studio.exe" (rode no Windows com Python 3.10+ instalado)
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean packaging\XPSSampleStudio.spec
powershell -Command "Compress-Archive -Force -Path 'dist\XPS Sample Studio\*' -DestinationPath 'dist\XPS-Sample-Studio-Windows.zip'"
echo Pronto: dist\XPS Sample Studio\XPS Sample Studio.exe
pause
