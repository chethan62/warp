"""Fetch the wireproxy binary when it isn't installed.

Asking the user to install a second program by hand is not "cross-platform" — so
the app downloads the release asset for the current OS/arch on first run into
its own config dir. No root, no package manager, works the same on Linux, macOS
and Windows.
"""

from __future__ import annotations

import io
import os
import platform
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request

from . import paths

# Pin a known-good release; override with WARP_WIREPROXY_VERSION.
VERSION = os.environ.get("WARP_WIREPROXY_VERSION", "1.1.3")
_BASE = "https://github.com/pufferffish/wireproxy/releases/download"

_PLATFORM = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
_ARCH = {
    "x86_64": "amd64", "amd64": "amd64",
    "aarch64": "arm64", "arm64": "arm64",
    "i386": "386", "i686": "386",
    "armv7l": "arm", "armv6l": "arm",
}


# Upstream publishes no windows/arm64 build. Windows 11 on ARM runs x64 binaries
# under emulation, so ask for the amd64 asset rather than failing on a platform
# that otherwise works. (Verified that the asset exists; not run on ARM hardware.)
_EMULATED = {("win32", "arm64"): "amd64"}


def asset_name() -> str | None:
    plat = _PLATFORM.get(sys.platform)
    arch = _ARCH.get(platform.machine().lower())
    if not plat or not arch:
        return None
    arch = _EMULATED.get((sys.platform, arch), arch)
    return f"wireproxy_{plat}_{arch}.tar.gz"


def binary_name() -> str:
    return "wireproxy.exe" if sys.platform == "win32" else "wireproxy"


def bin_path() -> str:
    return str(paths.config_dir() / binary_name())


def _download(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "warp"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _extract_binary(archive: bytes, dest: str) -> None:
    """Pull just the wireproxy member out of the tarball.

    Reads the member's bytes and writes them directly rather than calling
    extractall() — nothing from the archive can then escape the destination.
    """
    wanted = {binary_name(), "wireproxy"}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and os.path.basename(member.name) in wanted:
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                with open(dest, "wb") as out:
                    out.write(handle.read())
                break
        else:
            raise RuntimeError("wireproxy binary not found inside the release archive")


def _is_runnable(path: str) -> bool:
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        return proc.returncode == 0 and "wireproxy" in (proc.stdout + proc.stderr).lower()
    except (OSError, subprocess.SubprocessError):
        return False


def ensure() -> str:
    """Return a usable wireproxy path, downloading it on first use."""
    dest = bin_path()
    if os.path.isfile(dest) and _is_runnable(dest):
        return dest

    asset = asset_name()
    if not asset:
        raise RuntimeError(f"no wireproxy build for {sys.platform}/{platform.machine()}")

    url = f"{_BASE}/v{VERSION}/{asset}"
    try:
        blob = _download(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                f"wireproxy v{VERSION} has no build for {sys.platform}/"
                f"{platform.machine()} (looked for {asset}); set "
                f"WARP_WIREPROXY_VERSION to a release that does") from exc
        raise
    _extract_binary(blob, dest)
    if sys.platform != "win32":
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if not _is_runnable(dest):
        os.unlink(dest)
        raise RuntimeError(f"downloaded wireproxy from {url} did not run")
    return dest
