"""wireproxy lifecycle — start, stop, and report the tunnel.

wireproxy is a userspace WireGuard client, which is what makes this work with no
root: an in-kernel WireGuard interface (or the official warp-cli daemon) would
need elevated privileges, a userspace SOCKS5 listener does not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import cloudflare, netprobe, paths, provision, sysproxy

DEFAULT_SOCKS_PORT = 25344
_EXE = "wireproxy.exe" if sys.platform == "win32" else "wireproxy"


def find_wireproxy() -> str | None:
    found = shutil.which("wireproxy")
    if found:
        return found
    candidates = [
        paths.app_dir().parent / "bin" / _EXE,   # bundled beside the app
        paths.config_dir() / _EXE,               # downloaded on first run
        Path.home() / ".local" / "bin" / _EXE,
        Path("/usr/bin") / _EXE,
        Path("/usr/local/bin") / _EXE,
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _spawn_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


class Tunnel:
    def __init__(self, socks_port: int = DEFAULT_SOCKS_PORT):
        self.socks_port = socks_port
        self.proxy = ("127.0.0.1", socks_port)

    # ── state ────────────────────────────────────────────────────────────────
    def is_up(self) -> bool:
        return netprobe.port_open(*self.proxy)

    def status(self, probe: bool = True) -> dict:
        running = self.is_up()
        sp = sysproxy.status()
        # The system proxy points at this tunnel. With the tunnel not running it
        # is not routing the machine, it is breaking it - nothing is listening on
        # the port every proxy-aware app was just told to use. And because it is
        # a persistent OS setting it outlives the process: a reboot, a crash or a
        # kill leaves the machine pointed at nothing.
        stale = bool(sp.get("enabled") and not running
                     and f":{self.socks_port}" in str(sp.get("proxy") or ""))
        info = {
            "running": running,
            "socks_port": self.socks_port,
            "system_proxy": sp,
            "system_proxy_stale": stale,
            "wireproxy": find_wireproxy(),
            "connected": False,
            "ip": None,
            "warp": None,
            "loc": None,
            "error": None,
        }
        if running and probe:
            try:
                facts = netprobe.egress(self.proxy)
                info.update(ip=facts["ip"], warp=facts["warp"], loc=facts["loc"])
                info["connected"] = facts.get("warp") == "on"
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                info["error"] = str(exc)
        return info

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> dict:
        if self.is_up():
            return {"ok": True, "already": True, **self.status()}

        binary = find_wireproxy()
        if not binary:
            try:
                binary = provision.ensure()   # fetches the release asset on first use
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                return {"ok": False, "error": f"could not obtain wireproxy: {exc}"}

        account = cloudflare.ensure_account()
        conf = cloudflare.write_config(account, self.socks_port)

        proc = subprocess.Popen(
            [binary, "-c", conf],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            **_spawn_kwargs(),
        )
        (paths.config_dir() / "wireproxy.pid").write_text(str(proc.pid))

        for _ in range(60):  # up to ~12s for handshake + listener
            if proc.poll() is not None:
                return {"ok": False, "error": f"wireproxy exited (code {proc.returncode})"}
            if self.is_up():
                time.sleep(0.4)  # let the handshake settle before probing
                return {"ok": True, **self.status()}
            time.sleep(0.2)
        return {"ok": False, "error": "wireproxy did not open its SOCKS port"}

    def stop(self) -> dict:
        # Undo the system proxy BEFORE killing the tunnel: leaving the OS pointed
        # at a dead proxy takes the machine offline until someone notices.
        sp = sysproxy.status()
        if sp.get("enabled") and sp.get("proxy") and f":{self.socks_port}" in str(sp["proxy"]):
            sysproxy.clear()
        pidfile = paths.config_dir() / "wireproxy.pid"
        pid = None
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
            except ValueError:
                pid = None
        if pid:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.kill(pid, 15)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        pidfile.unlink(missing_ok=True)
        deadline = time.time() + 5
        while time.time() < deadline and self.is_up():
            time.sleep(0.2)
        return {"ok": not self.is_up(), **self.status(probe=False)}

    # ── whole-machine routing ────────────────────────────────────────────────
    def repair_stale_proxy(self) -> bool:
        """Undo a system proxy left pointing at a tunnel that is not running.

        Deliberately narrow: only a proxy aimed at *our* port, and only while the
        tunnel is down. So it cannot disable somebody else's setting, and cannot
        break a route that is actually working. Returns True if it cleared one.
        """
        if not self.status(probe=False).get("system_proxy_stale"):
            return False
        sysproxy.clear()
        return True

    def route_system(self, enable: bool = True) -> dict:
        """Point the OS proxy at this tunnel (and start it if needed)."""
        if not enable:
            return sysproxy.clear()
        if not self.is_up():
            started = self.start()
            if not started.get("ok"):
                return started
        result = sysproxy.apply("127.0.0.1", self.socks_port)
        return {**result, **self.status(probe=False)}
