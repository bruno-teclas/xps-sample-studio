#!/bin/bash
# Dê dois cliques neste arquivo para gerar o "XPS Sample Studio.app" com o ícone personalizado.
cd "$(dirname "$0")"
echo "=== XPS Sample Studio – criando o app para Mac ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "O Python 3 não está instalado. Baixe em https://www.python.org/downloads/macos/ e rode de novo."
  read -p "Pressione Enter para fechar"; exit 1
fi
python3 -m venv .venv || { echo "Falha ao criar ambiente Python"; read -p "Enter para fechar"; exit 1; }
source .venv/bin/activate
pip install --upgrade pip -q
pip install -q -r requirements.txt pyinstaller || { echo "Falha ao instalar dependências (verifique a internet)"; read -p "Enter"; exit 1; }
pyinstaller --noconfirm --clean packaging/XPSSampleStudio.spec || { read -p "Falha no build. Enter para fechar"; exit 1; }
xattr -cr "dist/XPS Sample Studio.app" 2>/dev/null
codesign --force --deep -s - "dist/XPS Sample Studio.app" 2>/dev/null
hdiutil create -volname "XPS Sample Studio" -srcfolder "dist/XPS Sample Studio.app" -ov -format UDZO "dist/XPS-Sample-Studio-macOS.dmg" >/dev/null
rm -rf "$HOME/Applications/XPS Sample Studio.app"; mkdir -p "$HOME/Applications"
cp -R "dist/XPS Sample Studio.app" "$HOME/Applications/"
echo ""
echo "Pronto! O app foi instalado em ~/Applications/XPS Sample Studio.app"
echo "Também foi gerado: dist/XPS-Sample-Studio-macOS.dmg"
open "$HOME/Applications"
read -p "Pressione Enter para fechar"
