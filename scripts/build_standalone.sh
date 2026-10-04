#!/bin/sh
# Сборка standalone-каталога dist/sphaera-commander (PyInstaller, onedir)
set -e
cd "$(dirname "$0")/.."
.venv/bin/pip install --quiet pyinstaller
.venv/bin/pyinstaller --noconfirm --windowed --name sphaera-commander \
    --paths . \
    --add-data "sphaera_commander/assets:sphaera_commander/assets" \
    --add-data "sphaera_commander/plugins:sphaera_commander/plugins" \
    scripts/entry.py
echo "Готово: dist/sphaera-commander/sphaera-commander"
