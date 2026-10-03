#!/bin/sh
# Сборка RPM-пакета: dist/rpm/x86_64/sphaera-commander-<версия>-1.*.rpm
#
# Пакет самодостаточен (PyInstaller-сборка в /opt/sphaera-commander).
# Требуется rpmbuild (Fedora: sudo dnf install rpm-build).
#
#   sh scripts/build_rpm.sh
#
set -e
cd "$(dirname "$0")/.."

VER="$(.venv/bin/python -c 'import sphaera_commander; print(sphaera_commander.__version__)')"

if ! command -v rpmbuild >/dev/null 2>&1; then
    echo "rpmbuild не найден (Fedora: sudo dnf install rpm-build)" >&2
    exit 1
fi
if [ ! -x dist/sphaera-commander/sphaera-commander ]; then
    sh scripts/build_standalone.sh
fi

TOP="build/rpm"
rm -rf "$TOP"
mkdir -p "$TOP/SPECS" "$TOP/BUILD" "$TOP/RPMS" "$TOP/SRPMS" "$TOP/SOURCES"

sed "s/^Version:.*/Version:        ${VER}/" \
    scripts/sphaera-commander.spec > "$TOP/SPECS/sphaera-commander.spec"

RPM_FLAGS=()
# локальный rpm-build без установки (sudo не требуется): распаковать пакет
# rpm-build в префикс и указать пути к его скриптам, например:
#   dnf download rpm-build && rpm2cpio rpm-build-*.rpm | cpio -idm
#   RPM_BUILD_PREFIX=/tmp/rpmb sh scripts/build_rpm.sh
if [ -n "${RPM_BUILD_PREFIX:-}" ] && [ -d "${RPM_BUILD_PREFIX}/usr/lib/rpm" ]; then
    LIB="${RPM_BUILD_PREFIX}/usr/lib/rpm"
    for helper in brp-compress brp-strip brp-strip-comment-note \
                  brp-strip-static-archive brp-remove-la-files; do
        name="__brp_$(echo "$helper" | sed 's/^brp-//; s/-/_/g')"
        RPM_FLAGS+=(--define "${name} ${LIB}/${helper}")
    done
    RPM_FLAGS+=(--define "__check_files ${LIB}/check-files")
fi

rpmbuild -bb \
    --define "_topdir $(pwd)/$TOP" \
    --define "_sph_src $(pwd)" \
    --define "_rpmdir $(pwd)/dist/rpm" \
    ${RPM_FLAGS+"${RPM_FLAGS[@]}"} \
    "$TOP/SPECS/sphaera-commander.spec"

echo "Готово: dist/rpm/*/sphaera-commander-${VER}-1.*.rpm"
