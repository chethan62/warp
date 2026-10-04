#!/bin/sh
# warp — one-line install for Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/chethan62/warp/main/packaging/install.sh | sh
#
# Installs the self-contained AppImage to ~/Applications, adds a desktop entry
# with the icon taken from inside the image, and then runs the thing to prove it
# works. Nothing needs root and nothing is installed system-wide.
#
#   WARP_REPO=owner/name   install from a fork
#   WARP_APPDIR=...        install somewhere else
set -eu

REPO="${WARP_REPO:-chethan62/warp}"
APP_DIR="${WARP_APPDIR:-$HOME/Applications}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_DIR="$DATA/applications"
ICON_DIR="$DATA/icons/hicolor/512x512/apps"
TARGET="$APP_DIR/warp.AppImage"

say() { printf '  %s\n' "$*"; }

command -v curl >/dev/null || { echo "warp: curl is required" >&2; exit 1; }

say "finding the latest release of $REPO"
URL=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | python3 -c '
import json, sys
rel = json.load(sys.stdin)
for a in rel.get("assets", []):
    if a["name"].endswith("-x86_64.AppImage"):
        print(a["browser_download_url"]); break
else:
    sys.exit("no x86_64 AppImage in the latest release")
' 2>/dev/null) || {
    echo "warp: could not find an AppImage in the latest release of $REPO" >&2
    exit 1
}

mkdir -p "$APP_DIR" "$DESKTOP_DIR" "$ICON_DIR"
say "downloading $(basename "$URL")"
curl -fsSL "$URL" -o "$TARGET"
chmod +x "$TARGET"

# The icon lives inside the image; --appimage-extract needs no FUSE.
say "extracting the icon"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
( cd "$WORK" && "$TARGET" --appimage-extract 'usr/share/icons/*' >/dev/null 2>&1 ) || true
PNG=$(find "$WORK/squashfs-root" -name '*.png' 2>/dev/null | head -1 || true)
if [ -n "$PNG" ]; then
    cp "$PNG" "$ICON_DIR/warp.png"
else
    say "no icon found in the image — the entry will use a generic one"
fi

cat > "$DESKTOP_DIR/warp.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=warp
GenericName=Cloudflare WARP client
Comment=Route this computer through Cloudflare WARP, without root
Exec="$TARGET" %U
Icon=warp
Terminal=false
Categories=Network;
Keywords=vpn;warp;cloudflare;wireguard;socks5;proxy;
StartupWMClass=warp
EOF

# Both are best-effort: they are absent on a minimal install and their failure
# says nothing about whether warp itself works.
command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
# gtk-update-icon-cache refuses without an index.theme, and the user-level
# hicolor theme usually has none — write a minimal one rather than failing.
if [ ! -f "$DATA/icons/hicolor/index.theme" ]; then
    cat > "$DATA/icons/hicolor/index.theme" <<'EOF'
[Icon Theme]
Name=Hicolor
Comment=Fallback icon theme
Directories=512x512/apps

[512x512/apps]
Size=512
Context=Applications
Type=Threshold
EOF
fi
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true

say "installed: $TARGET"
say "launcher:  $DESKTOP_DIR/warp.desktop (also in your app menu)"
say "checking it actually runs"
"$TARGET" selftest
say "run it with: $TARGET    (or 'warp' from the app menu)"
