#!/bin/bash
# Gera "XPS Sample Studio.app" e um .dmg (rode num Mac com Python 3.10+)
set -e
cd "$(dirname "$0")"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean packaging/XPSSampleStudio.spec
xattr -cr "dist/XPS Sample Studio.app" 2>/dev/null; codesign --force --deep -s - "dist/XPS Sample Studio.app" 2>/dev/null
hdiutil create -volname "XPS Sample Studio" -srcfolder "dist/XPS Sample Studio.app" -ov -format UDZO "dist/XPS-Sample-Studio-macOS.dmg"
echo "Pronto: dist/XPS-Sample-Studio-macOS.dmg"
