#!/bin/sh
# Установка ярлыка и иконки в профиль пользователя (~/.local)
# (основная установка — scripts/install.sh; этот скрипт только ярлык)
set -e
cd "$(dirname "$0")/.."
ICON_DIR="$HOME/.local/share/icons/hicolor"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$ICON_DIR/128x128/apps" "$ICON_DIR/256x256/apps" \
         "$ICON_DIR/scalable/apps" "$APPS_DIR"
cp sphaera_commander/assets/icon.svg "$ICON_DIR/scalable/apps/sphaera-commander.svg"
cp sphaera_commander/assets/icon-128.png "$ICON_DIR/128x128/apps/sphaera-commander.png"
cp sphaera_commander/assets/icon-256.png "$ICON_DIR/256x256/apps/sphaera-commander.png"
cp sphaera_commander/assets/sphaera-commander.desktop "$APPS_DIR/sphaera-commander.desktop"
echo "Установлено:"
echo "  $APPS_DIR/sphaera-commander.desktop"
echo "  $ICON_DIR/128x128/apps/sphaera-commander.png"
echo "  $ICON_DIR/256x256/apps/sphaera-commander.png"
echo "Команда Exec=sphaera-commander должна быть в PATH (.venv/bin или pipx)."
