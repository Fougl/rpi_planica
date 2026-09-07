#!/bin/bash
# Build and install gatttool.
#
# The Planica Pi has /usr/bin/gatttool because Raspbian stretch shipped bluez
# 5.43. Upstream made gatttool build-only (noinst_PROGRAMS) right after that
# release, so Debian stopped installing it -- every OS new enough to drive the
# UB500 dongle is also new enough to be missing the tool py_new.py and
# run_gatttool_all.py both shell out to. So we build it ourselves.
#
# Only the attrib/gatttool target is built, not the whole stack: the system
# keeps its own packaged bluez/bluetoothd, and we add nothing but the one
# binary.
#
# Usage:  sudo bash install_gatttool.sh

set -euo pipefail

BLUEZ_VERSION="${BLUEZ_VERSION:-5.55}"
SRC_DIR="/usr/local/src"
DEST="/usr/local/bin/gatttool"
# Cron runs with PATH=/usr/bin:/bin, so /usr/local/bin alone is not enough for
# run_gatttool_all.py's subprocess call to find it.
LINK="/usr/bin/gatttool"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo." >&2
    exit 1
fi

if command -v gatttool >/dev/null 2>&1; then
    echo "gatttool already present at $(command -v gatttool) -- nothing to do."
    gatttool --help 2>&1 | head -2 || true
    exit 0
fi

echo "=== [1/5] Build dependencies ==="
apt-get update
apt-get install -y --no-install-recommends \
    build-essential wget xz-utils pkg-config \
    libglib2.0-dev libdbus-1-dev libudev-dev libical-dev libreadline-dev

echo "=== [2/5] Fetching bluez ${BLUEZ_VERSION} source ==="
mkdir -p "$SRC_DIR"
cd "$SRC_DIR"
TARBALL="bluez-${BLUEZ_VERSION}.tar.xz"
if [ ! -f "$TARBALL" ]; then
    wget -q --show-progress "https://www.kernel.org/pub/linux/bluetooth/${TARBALL}"
fi
rm -rf "bluez-${BLUEZ_VERSION}"
tar xf "$TARBALL"
cd "bluez-${BLUEZ_VERSION}"

if [ ! -f attrib/gatttool.c ]; then
    echo "ERROR: attrib/gatttool.c missing from bluez ${BLUEZ_VERSION}." >&2
    echo "gatttool was dropped from this release; retry with an older one:" >&2
    echo "  sudo BLUEZ_VERSION=5.50 bash $0" >&2
    exit 1
fi

echo "=== [3/5] Configuring (deprecated tools enabled) ==="
# --enable-deprecated is what puts gatttool back in the build.
# --enable-library keeps libbluetooth available to the build; nothing is
# installed system-wide, we only take the one binary out of the tree.
./configure \
    --enable-deprecated \
    --enable-library \
    --disable-systemd \
    --disable-udev \
    --disable-cups \
    --disable-obex \
    --disable-manpages \
    --disable-testing \
    >/dev/null

echo "=== [4/5] Building attrib/gatttool ==="
# -j2, not -j4: the Zero 2 has 512MB and the linker is the memory-hungry part.
make -j2 attrib/gatttool

echo "=== [5/5] Installing ==="
install -m 0755 attrib/gatttool "$DEST"
ln -sf "$DEST" "$LINK"

echo
echo "Installed:"
ls -la "$DEST" "$LINK"
echo
echo "Linked against:"
ldd "$DEST"
echo
echo "Version check:"
"$DEST" --version 2>&1 | head -2 || true
echo
echo "=== gatttool ready ==="
