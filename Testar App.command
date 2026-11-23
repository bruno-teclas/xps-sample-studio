#!/bin/bash
# Abre o app compilado e grava qualquer erro em erro.log (para diagnóstico)
cd "$(dirname "$0")"
"dist/XPS Sample Studio.app/Contents/MacOS/XPS Sample Studio" > erro.log 2>&1
echo "codigo de saida: $?" >> erro.log
