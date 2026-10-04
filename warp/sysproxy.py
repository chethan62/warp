"""Make the OS route through the tunnel.

A userspace tunnel can't take over the network stack (that needs a TUN device
and root), so the honest no-root equivalent is the system proxy: one setting
every proxy-aware app — browsers, app stores, most GUI toolkits — obeys.

Pointed at the **SOCKS5** listener, because that is the one wireproxy actually
serves (its `[http]` section binds but never answers — measured 0/3 vs 3/3).
Every platform below can drive SOCKS, so one listener covers all of them:

  Linux/KDE   kwriteconfig6  kioslaverc  (httpProxy=socks://… routes everything)
  Linux/GNOME gsettings      org.gnome.system.proxy.socks
  macOS       networksetup   -setsocksfirewallproxy
  Windows     reg            HKCU\\...\\Internet Settings  (ProxyServer=socks=…)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_NO_PROXY = "localhost,127.0.0.1,::1"


def _run(cmd: list[str], timeout: int = 25) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.SubprocessError as exc:
        return 1, str(exc)


def _first(*names: str) -> str | None:
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    return None


# ── backend detection ───────────────────────────────────────────────────────
def backend() -> str | None:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos" if _first("networksetup") else None
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
    if _first("kwriteconfig6", "kwriteconfig5") and ("KDE" in desktop or not desktop):
        return "kde"
    if _first("gsettings") and "GNOME" in desktop:
        return "gnome"
    if _first("kwriteconfig6", "kwriteconfig5"):
        return "kde"
    if _first("gsettings"):
        return "gnome"
    return None


# ── KDE ─────────────────────────────────────────────────────────────────────
def _kde(endpoint: tuple[str, int] | None):
    kw = _first("kwriteconfig6", "kwriteconfig5")
    if not kw:
        return "kwriteconfig not found"
    group = "Proxy Settings"
    if endpoint:
        host, port = endpoint
        # KDE's documented all-protocol form: a socks:// URL in httpProxy makes
        # KIO route every scheme through SOCKS, not just HTTP.
        socks = f"socks://{host}:{port}"
        for key in ("httpProxy", "httpsProxy", "ftpProxy"):
            _run([kw, "--file", "kioslaverc", "--group", group, "--key", key, socks])
        _run([kw, "--file", "kioslaverc", "--group", group, "--key", "NoProxyFor", _NO_PROXY])
        _run([kw, "--file", "kioslaverc", "--group", group, "--key", "ProxyType", "1"])
    else:
        _run([kw, "--file", "kioslaverc", "--group", group, "--key", "ProxyType", "0"])
    return None


# ── GNOME ───────────────────────────────────────────────────────────────────
def _gnome(endpoint: tuple[str, int] | None):
    gs = _first("gsettings")
    if not gs:
        return "gsettings not found"
    if endpoint:
        host, port = endpoint
        # libproxy (what GNOME apps use) treats the socks entry as the proxy for
        # every scheme, so this one pair is enough.
        _run([gs, "set", "org.gnome.system.proxy.socks", "host", host])
        _run([gs, "set", "org.gnome.system.proxy.socks", "port", str(port)])
        _run([gs, "set", "org.gnome.system.proxy", "ignore-hosts",
              "['localhost', '127.0.0.0/8', '::1']"])
        _run([gs, "set", "org.gnome.system.proxy", "mode", "manual"])
    else:
        _run([gs, "set", "org.gnome.system.proxy", "mode", "none"])
    return None


# ── macOS ───────────────────────────────────────────────────────────────────
def _macos_service() -> str | None:
    code, out = _run(["networksetup", "-listallnetworkservices"])
    if code != 0:
        return None
    for line in out.splitlines()[1:]:
        name = line.strip()
        if name and not name.startswith("*"):   # '*' marks a disabled service
            return name
    return None


def _macos(endpoint: tuple[str, int] | None):
    svc = _macos_service()
    if not svc:
        return "no network service found"
    if endpoint:
        _run(["networksetup", "-setsocksfirewallproxy", svc, endpoint[0], str(endpoint[1])])
    else:
        _run(["networksetup", "-setsocksfirewallproxystate", svc, "off"])
    return None


# ── Windows ─────────────────────────────────────────────────────────────────
_WIN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _windows(endpoint: tuple[str, int] | None):
    if endpoint:
        # WinINET's "socks=" prefix routes all protocols through the SOCKS proxy.
        _run(["reg", "add", _WIN_KEY, "/v", "ProxyServer", "/t", "REG_SZ",
              "/d", f"socks={endpoint[0]}:{endpoint[1]}", "/f"])
        _run(["reg", "add", _WIN_KEY, "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "1", "/f"])
    else:
        _run(["reg", "add", _WIN_KEY, "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "0", "/f"])
    _wininet_refresh()
    return None


def _wininet_refresh():
    """Tell running WinINET apps the settings changed (best effort)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        internet = ctypes.windll.wininet
        for opt in (39, 37):  # INTERNET_OPTION_SETTINGS_CHANGED, REFRESH
            internet.InternetSetOptionW(None, opt, 0, 0)
    except Exception:  # noqa: BLE001 - purely cosmetic for already-running apps
        pass


_BACKENDS = {"kde": _kde, "gnome": _gnome, "macos": _macos, "windows": _windows}


# ── public API ──────────────────────────────────────────────────────────────
def apply(host: str, port: int) -> dict:
    name = backend()
    if not name:
        return {"ok": False, "error": "no supported system-proxy backend on this platform"}
    err = _BACKENDS[name]((host, port))
    if err:
        return {"ok": False, "backend": name, "error": err}
    return {"ok": True, "backend": name, **status()}


def clear() -> dict:
    name = backend()
    if not name:
        return {"ok": False, "error": "no supported system-proxy backend on this platform"}
    err = _BACKENDS[name](None)
    if err:
        return {"ok": False, "backend": name, "error": err}
    return {"ok": True, "backend": name, **status()}


def status() -> dict:
    name = backend()
    info = {"supported": name is not None, "backend": name, "enabled": False, "proxy": None}
    if name == "kde":
        kr = _first("kreadconfig6", "kreadconfig5")
        if kr:
            # ProxyType is the switch: 1 = manual. httpProxy keeps its value even
            # after routing is turned off, so keying on it reports a stale "on".
            code, ptype = _run([kr, "--file", "kioslaverc",
                                "--group", "Proxy Settings", "--key", "ProxyType"])
            if code == 0 and ptype.strip() == "1":
                _, proxy = _run([kr, "--file", "kioslaverc",
                                 "--group", "Proxy Settings", "--key", "httpProxy"])
                info.update(enabled=True, proxy=proxy or None)
    elif name == "gnome":
        gs = _first("gsettings")
        if gs:
            code, out = _run([gs, "get", "org.gnome.system.proxy", "mode"])
            if code == 0 and "manual" in out:
                _, host = _run([gs, "get", "org.gnome.system.proxy.socks", "host"])
                info.update(enabled=True, proxy=host.strip("'\n "))
    elif name == "macos":
        svc = _macos_service()
        if svc:
            code, out = _run(["networksetup", "-getsocksfirewallproxy", svc])
            if code == 0 and "Enabled: Yes" in out:
                info.update(enabled=True, proxy="(see networksetup -getsocksfirewallproxy)")
    elif name == "windows":
        code, out = _run(["reg", "query", _WIN_KEY, "/v", "ProxyEnable"])
        if code == 0 and "0x1" in out:
            _, server = _run(["reg", "query", _WIN_KEY, "/v", "ProxyServer"])
            info.update(enabled=True, proxy=server.split()[-1] if server else None)
    return info
