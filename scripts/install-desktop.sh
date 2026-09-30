#!/bin/sh
# Установка ярлыка и иконки в профиль пользователя (~/.local)
set -e
cd "$(dirname "$0")/.."
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$ICON_DIR" "$APPS_DIR"
cp sphaera_commander/assets/icon.svg "$ICON_DIR/sphaera-commander.svg"
cp sphaera_commander/assets/sphaera-commander.desktop "$APPS_DIR/sphaera-commander.desktop"
echo "Установлено:"
echo "  $APPS_DIR/sphaera-commander.desktop"
echo "  $ICON_DIR/sphaera-commander.svg"
echo "Команда Exec=sphaera-commander должна быть в PATH (.venv/bin или pipx)."
