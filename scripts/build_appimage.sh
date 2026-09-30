#!/bin/sh
# Сборка AppImage: dist/Sphaera_Commander-<версия>-x86_64.AppImage
# Требует: скрипт build_standalone.sh (PyInstaller onedir) и доступ к GitHub
# (linuxdeploy скачивается при первом запуске).
set -e
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
[ -n "$VERSION" ] || { echo "не удалось определить версию"; exit 1; }

echo "==> standalone-сборка (PyInstaller)"
sh scripts/build_standalone.sh

APPDIR=build/appimage/AppDir
rm -rf build/appimage
mkdir -p "$APPDIR/usr/bin" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/scalable/apps"

echo "==> сборка AppDir"
mv dist/sphaera-commander "$APPDIR/usr/bin/sphaera-commander-bin"
cat > "$APPDIR/usr/bin/sphaera-commander" <<WRAPPER
#!/bin/sh
HERE=\$(dirname "\$(readlink -f "\$0")")
exec "\$HERE/sphaera-commander-bin/sphaera-commander" "\$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/sphaera-commander"
cp sphaera_commander/assets/sphaera-commander.desktop \
   "$APPDIR/usr/share/applications/sphaera-commander.desktop"
cp sphaera_commander/assets/icon.svg \
   "$APPDIR/usr/share/icons/hicolor/scalable/apps/sphaera-commander.svg"

TOOLS=build/appimage/tools
mkdir -p "$TOOLS"
if [ ! -x "$TOOLS/linuxdeploy" ]; then
    echo "==> скачивание linuxdeploy"
    curl -L --fail -o "$TOOLS/linuxdeploy" \
        https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
    chmod +x "$TOOLS/linuxdeploy"
fi

# AppImage-инструменты работают и без FUSE через распаковку при запуске
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH=x86_64
export VERSION
export OUTPUT="dist/Sphaera_Commander-${VERSION}-x86_64.AppImage"
mkdir -p dist

echo "==> linuxdeploy -> AppImage"
"$TOOLS/linuxdeploy" \
    --appdir "$APPDIR" \
    -d "$APPDIR/usr/share/applications/sphaera-commander.desktop" \
    -i sphaera_commander/assets/icon.svg \
    --output appimage

echo "Готово: $OUTPUT"
