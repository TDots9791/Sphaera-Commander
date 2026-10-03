#!/bin/sh
# Установка Sphaera Commander в систему.
#
#   ./scripts/install.sh                 — в профиль пользователя (~/.local)
#   sudo ./scripts/install.sh --system   — общесистемно (/opt + /usr/local/bin)
#   ./scripts/install.sh --uninstall     — удалить пользовательскую установку
#
# После установки ярлык появляется в меню, и папки в Nautilus/на рабочем
# столе открываются Sphaera Commander'ом (можно вернуть Nautilus:
#   xdg-mime default org.gnome.Nautilus.desktop inode/directory
# ).
set -e
cd "$(dirname "$0")/.."

MODE="user"
UNINSTALL=0
SET_DEFAULT=1
for arg in "$@"; do
    case "$arg" in
        --system) MODE="system" ;;
        --uninstall) UNINSTALL=1 ;;
        --no-default) SET_DEFAULT=0 ;;
        *) echo "неизвестный аргумент: $arg"; exit 1 ;;
    esac
done

if [ "$MODE" = "system" ]; then
    SUDO="sudo"
    PREFIX="/opt/sphaera-commander"
    BINDIR="/usr/local/bin"
    DATA_DIR="/usr/share"
else
    SUDO=""
    PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/sphaera-commander"
    BINDIR="$HOME/.local/bin"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
fi
DESKTOP_DIR="$DATA_DIR/applications"
ICON_DIR="$DATA_DIR/icons/hicolor"

if [ "$UNINSTALL" = "1" ]; then
    $SUDO rm -rf "$PREFIX"
    $SUDO rm -f "$BINDIR/sphaera-commander"
    $SUDO rm -f "$DESKTOP_DIR/sphaera-commander.desktop"
    $SUDO rm -f "$ICON_DIR/scalable/apps/sphaera-commander.svg" \
                "$ICON_DIR/128x128/apps/sphaera-commander.png" \
                "$ICON_DIR/256x256/apps/sphaera-commander.png"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    echo "Удалено. Обработчиком папок можно вернуть Nautilus:"
    echo "  xdg-mime default org.gnome.Nautilus.desktop inode/directory"
    exit 0
fi

echo "==> сборка standalone-версии (PyInstaller)"
[ -x .venv/bin/python ] || python3 -m venv .venv
sh scripts/build_standalone.sh

echo "==> установка в $PREFIX"
$SUDO rm -rf "$PREFIX"
$SUDO mkdir -p "$PREFIX"
$SUDO cp -r dist/sphaera-commander "$PREFIX/app"

echo "==> запускатор $BINDIR/sphaera-commander"
$SUDO mkdir -p "$BINDIR"
$SUDO tee "$BINDIR/sphaera-commander" >/dev/null <<WRAPPER
#!/bin/sh
exec "$PREFIX/app/sphaera-commander" "\$@"
WRAPPER
$SUDO chmod +x "$BINDIR/sphaera-commander"

echo "==> ярлык и иконки"
$SUDO mkdir -p "$DESKTOP_DIR" \
              "$ICON_DIR/128x128/apps" \
              "$ICON_DIR/256x256/apps"
$SUDO cp sphaera_commander/assets/sphaera-commander.desktop \
         "$DESKTOP_DIR/sphaera-commander.desktop"
# фирменная иконка — весы Iustitia (растровые; устаревший SVG убираем)
$SUDO rm -f "$ICON_DIR/scalable/apps/sphaera-commander.svg"
$SUDO cp sphaera_commander/assets/icon-128.png \
         "$ICON_DIR/128x128/apps/sphaera-commander.png"
$SUDO cp sphaera_commander/assets/icon-256.png \
         "$ICON_DIR/256x256/apps/sphaera-commander.png"

command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -qtf "$ICON_DIR" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

if [ "$SET_DEFAULT" = "1" ] && command -v xdg-mime >/dev/null 2>&1; then
    echo "==> назначение обработчиком папок (inode/directory)"
    xdg-mime default sphaera-commander.desktop inode/directory
fi

echo
echo "Установлено: Sphaera Commander"
echo "  команда:    $BINDIR/sphaera-commander [левый_каталог [правый_каталог]]"
echo "  ярлык:      $DESKTOP_DIR/sphaera-commander.desktop"
echo "  файлы:      $PREFIX"
if [ "$MODE" = "user" ]; then
    echo "(если ~/.local/bin нет в PATH — добавьте: "
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc)"
fi
echo "Удаление: $0 $([ "$MODE" = "system" ] && echo --system) --uninstall"
