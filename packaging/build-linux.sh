#!/bin/sh
# Build the Linux artifacts for warp.
#
#   ./packaging/build-linux.sh                 # launcher-style (host python3)
#   ./packaging/build-linux.sh --with-python   # self-contained
#
# Two artifacts come out of one build, from the same payload:
#
#   warp-<ver>-x86_64.AppImage          one portable file (needs FUSE to mount)
#   warp-<ver>-linux-x86_64.tar.gz      a plain ~/.local tree (needs nothing)
#
# The tarball exists because FUSE is not a safe assumption: AppImages mount
# themselves with it, libfuse2 is absent by default on many systems (Ubuntu
# dropped it), and without it the image cannot launch at all. Nothing here needs
# a mount, so the tarball just unpacks:
#
#   tar -C ~/.local -xf warp-linux-x86_64.tar.gz --strip-components=1
#
# --with-python downloads a python-build-standalone release and trims what an
# interpreter needs but a CLI does not. Override with PYTHON_BUNDLE=/path/to/python.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/dist"
WITH_PYTHON=0
for arg in "$@"; do
    case "$arg" in
        --with-python) WITH_PYTHON=1 ;;
        *) OUT_DIR="$arg" ;;
    esac
done

APPIMAGE_TOOL="${APPIMAGE_TOOL:-appimagetool}"
PBS_REPO="astral-sh/python-build-standalone"
# Pinned, not "latest": the floating tag moves under you, so rebuilding an old
# release would silently pick a different interpreter. PBS_TAG=latest floats.
PBS_TAG="${PBS_TAG:-20261003}"
PBS_MATCH="${PBS_MATCH:-cpython-3.12.*-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz}"

WORK="$(mktemp -d)"
APPDIR="$WORK/warp.AppDir"
trap 'rm -rf "$WORK"' EXIT INT TERM

command -v "$APPIMAGE_TOOL" >/dev/null || { echo "need appimagetool on PATH" >&2; exit 1; }
command -v rsvg-convert >/dev/null || { echo "need rsvg-convert (librsvg) on PATH" >&2; exit 1; }

# Trim an interpreter tree down to what `python3 -m warp` actually needs.
trim_python() {
    root="$1"
    # libpython3*.so is the embed/extension copy. bin/python3* is statically
    # linked against its own copy of the interpreter (confirmed with ldd: it
    # references no libpython at all), so shipping both is shipping CPython
    # twice — dropping the .so takes the payload from 81 MB to 49 MB and the
    # image from 27 MB to ~17 MB, with the app still running.
    for p in lib/libpython3*.so* \
             lib/python*/site-packages/pip* lib/python*/site-packages/setuptools* \
             lib/python*/ensurepip lib/python*/idlelib lib/python*/test \
             lib/python*/tkinter lib/python*/turtledemo lib/python*/lib2to3 \
             lib/python*/distutils lib/tcl* lib/tk* include share \
             bin/2to3* bin/idle3* bin/pip* bin/pydoc* bin/*-config; do
        rm -rf "$root"/$p 2>/dev/null || true
    done
    find "$root" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "$root" -name '*.pyc' -delete 2>/dev/null || true
}

fetch_python() {
    dest="$WORK/python"
    echo "fetching a python-build-standalone release matching: $PBS_MATCH" >&2
    endpoint="releases/tags/$PBS_TAG"
    if [ "$PBS_TAG" = "latest" ]; then endpoint="releases/latest"; fi
    url="$(curl -sL "https://api.github.com/repos/$PBS_REPO/$endpoint" \
        | python3 -c "
import json,sys,re
pat=re.compile(sys.argv[1])
for a in json.load(sys.stdin).get('assets',[]):
    if pat.search(a['name']): print(a['browser_download_url']); break
" "$PBS_MATCH")"
    [ -n "$url" ] || { echo "no release asset matched $PBS_MATCH" >&2; exit 1; }
    curl -sL "$url" -o "$WORK/python.tar.gz"
    mkdir -p "$dest"
    tar -xzf "$WORK/python.tar.gz" -C "$dest" --strip-components=1
    echo "$dest"
}

mkdir -p "$APPDIR/usr/share/icons/hicolor/512x512/apps" "$OUT_DIR"

# ── payload ─────────────────────────────────────────────────────────────────
cp -r "$ROOT/warp" "$APPDIR/warp"
find "$APPDIR/warp" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/pyproject.toml" "$APPDIR/"

PY_TREE=""
if [ -n "${PYTHON_BUNDLE:-}" ]; then
    PY_TREE="$PYTHON_BUNDLE"
elif [ "$WITH_PYTHON" = "1" ]; then
    PY_TREE="$(fetch_python)"
fi

if [ -n "$PY_TREE" ]; then
    [ -x "$PY_TREE/bin/python3" ] || { echo "no bin/python3 in $PY_TREE" >&2; exit 1; }
    echo "bundling interpreter from $PY_TREE"
    mkdir -p "$APPDIR/python"
    cp -r "$PY_TREE"/. "$APPDIR/python/"
    trim_python "$APPDIR/python"
    du -sh "$APPDIR/python" | sed 's/^/  after trim: /'
else
    echo "launcher-style build — the artifacts will need python3 on the host"
fi

rsvg-convert -w 512 -h 512 "$ROOT/packaging/warp.svg" -o "$APPDIR/warp.png"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/warp/__init__.py")"
VERSION="${VERSION:-0}"

# ── artifact 1: the AppImage ────────────────────────────────────────────────
cp "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/warp.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=warp
GenericName=Cloudflare WARP client
Comment=Route this computer through Cloudflare WARP, without root
Exec=AppRun
Icon=warp
Terminal=false
Categories=Network;
Keywords=vpn;warp;cloudflare;wireguard;socks5;proxy;
StartupNotify=true
DESKTOP

cp "$APPDIR/warp.png" "$APPDIR/.DirIcon"
cp "$ROOT/packaging/warp.svg" "$APPDIR/warp.svg"
cp "$APPDIR/warp.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/warp.png"

OUT="$OUT_DIR/warp-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$APPIMAGE_TOOL" --no-appstream "$APPDIR" "$OUT"
echo "built: $OUT ($(du -h "$OUT" | cut -f1))"

# ── artifact 2: the FUSE-free tree ──────────────────────────────────────────
# Same payload laid out as ~/.local, so a plain `tar -C ~/.local -xf` is the
# whole install. No mount, no FUSE, no runtime to satisfy.
TREE="warp-$VERSION-linux-x86_64"
TARROOT="$WORK/$TREE"
mkdir -p "$TARROOT/bin" "$TARROOT/lib/warp" \
         "$TARROOT/share/applications" "$TARROOT/share/icons/hicolor/512x512/apps"

cp -r "$APPDIR/warp" "$TARROOT/lib/warp/warp"
[ -d "$APPDIR/python" ] && cp -r "$APPDIR/python" "$TARROOT/lib/warp/python"
cp "$ROOT/README.md" "$ROOT/LICENSE" "$TARROOT/lib/warp/"
cp "$APPDIR/warp.png" "$TARROOT/share/icons/hicolor/512x512/apps/warp.png"

# Resolve the tree from the script's own location: the archive has to work from
# any prefix (~/.local, /opt, a USB stick) and for any user, so no absolute path
# is baked in anywhere.
cat > "$TARROOT/bin/warp" <<'LAUNCHER'
#!/bin/sh
set -eu
bin=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname "$bin")
export PYTHONPATH="$root/lib/warp"
PY="$root/lib/warp/python/bin/python3"
if [ ! -x "$PY" ]; then PY=$(command -v python3 || true); fi
[ -x "$PY" ] || { echo "warp: no python3 — install one, or use the bundled build" >&2; exit 1; }
exec "$PY" -m warp "$@"
LAUNCHER
chmod +x "$TARROOT/bin/warp"

# Exec is PATH-relative on purpose: a baked /home/<user>/... path is wrong for
# everyone else who unpacks this, and ~/.local/bin is on PATH by default.
cat > "$TARROOT/share/applications/warp.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=warp
GenericName=Cloudflare WARP client
Comment=Route this computer through Cloudflare WARP, without root
Exec=warp
Icon=warp
Terminal=false
Categories=Network;
Keywords=vpn;warp;cloudflare;wireguard;socks5;proxy;
StartupWMClass=warp
DESKTOP

tar -C "$WORK" -czf "$OUT_DIR/$TREE.tar.gz" "$TREE"
# A stable name too, so `releases/latest/download/warp-linux-x86_64.tar.gz`
# works without asking the API which version is current.
cp "$OUT_DIR/$TREE.tar.gz" "$OUT_DIR/warp-linux-x86_64.tar.gz"
echo "built: $OUT_DIR/$TREE.tar.gz ($(du -h "$OUT_DIR/$TREE.tar.gz" | cut -f1))"
echo "built: $OUT_DIR/warp-linux-x86_64.tar.gz (stable name)"
