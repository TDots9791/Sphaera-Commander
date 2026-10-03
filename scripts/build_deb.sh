#!/bin/sh
# Сборка .deb-пакета: dist/sphaera-commander_<версия>_amd64.deb
#
# Пакет самодостаточен (PyInstaller-сборка в /opt/sphaera-commander),
# системные зависимости — только glibc/стандартные библиотеки X/GTK.
# Собирается без dpkg: ar + tar (формат .deb — ar-архив с control/data).
#
#   sh scripts/build_deb.sh            # нужен dist/sphaera-commander или соберёт
#
set -e
cd "$(dirname "$0")/.."

VER="$(.venv/bin/python -c 'import sphaera_commander; print(sphaera_commander.__version__)')"
OUT="dist/sphaera-commander_${VER}_amd64.deb"

if [ ! -x dist/sphaera-commander/sphaera-commander ]; then
    sh scripts/build_standalone.sh
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
DATA="$STAGE/data"
CTRL="$STAGE/control"

# ---- data ----
mkdir -p "$DATA/opt/sphaera-commander" \
         "$DATA/usr/bin" \
         "$DATA/usr/share/applications" \
         "$DATA/usr/share/icons/hicolor/scalable/apps" \
         "$DATA/usr/share/icons/hicolor/128x128/apps" \
         "$DATA/usr/share/icons/hicolor/256x256/apps" \
         "$DATA/usr/share/doc/sphaera-commander"
cp -r dist/sphaera-commander/. "$DATA/opt/sphaera-commander/"

cat > "$DATA/usr/bin/sphaera-commander" <<'WRAPPER'
#!/bin/sh
exec "/opt/sphaera-commander/sphaera-commander" "$@"
WRAPPER
chmod 755 "$DATA/usr/bin/sphaera-commander"

cp sphaera_commander/assets/sphaera-commander.desktop \
   "$DATA/usr/share/applications/"
cp sphaera_commander/assets/icon.svg \
   "$DATA/usr/share/icons/hicolor/scalable/apps/sphaera-commander.svg"
cp sphaera_commander/assets/icon-128.png \
   "$DATA/usr/share/icons/hicolor/128x128/apps/sphaera-commander.png"
cp sphaera_commander/assets/icon-256.png \
   "$DATA/usr/share/icons/hicolor/256x256/apps/sphaera-commander.png"

cat > "$DATA/usr/share/doc/sphaera-commander/copyright" <<'MIT'
Format: https://spdx.org/licenses/MIT.html
Upstream-Name: Sphaera Commander

Sphaera Commander — dual-panel file manager for Linux.
Copyright (c) SphaeraLTI

MIT License. Полный текст — в файле LICENSE репозитория.
MIT
[ -f LICENSE ] && cp LICENSE "$DATA/usr/share/doc/sphaera-commander/LICENSE"

# ---- control ----
mkdir -p "$CTRL/DEBIAN"
INSTALLED_SIZE="$(du -sk "$DATA" | cut -f1)"
cat > "$CTRL/DEBIAN/control" <<DESC
Package: sphaera-commander
Version: ${VER}
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: ${INSTALLED_SIZE}
Maintainer: SphaeraLTI
Description: Sphaera Commander — двухпанельный файловый менеджер для Linux
 Sphaera Commander — двухпанельный файловый менеджер в духе Total Commander:
 панели с колонками, быстрый просмотр (Ctrl+Q), встроенные F3/F4
 (pdf/docx/doc/xlsx/pptx и др.), архивы как каталоги, поиск по содержимому,
 групповое переименование, миниатюры, drag-and-drop.
 Интерфейс: русский, английский, китайский.
DESC

# md5sums содержимого data
(cd "$DATA" && find . -type f ! -path "./opt/sphaera-commander/_internal/*" -exec md5sum {} \; \
    | sed 's|  \./|  |') > "$CTRL/DEBIAN/md5sums"

# ---- упаковка ----
mkdir -p dist
tar --owner=root --group=root -C "$CTRL" -czf "$STAGE/control.tar.gz" .
tar --owner=root --group=root -C "$DATA" -czf "$STAGE/data.tar.gz" .
echo "2.0" > "$STAGE/debian-binary"
ar rc "$OUT" "$STAGE/debian-binary" "$STAGE/control.tar.gz" "$STAGE/data.tar.gz"
echo "Готово: $OUT ($(du -h "$OUT" | cut -f1))"
