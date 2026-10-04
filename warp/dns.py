"""DNS through the tunnel: what it would take, printed rather than done.

The app never elevates on its own, and every step that makes DNS system-wide is
root-level. So this detects the actual resolver chain on the machine and prints
the exact commands for *that* chain.

The reason it is not just "set DNS = 1.1.1.1": the tunnel is a SOCKS proxy and
SOCKS carries TCP, not datagrams, so plain UDP/53 cannot cross it. DoH can —
it is HTTPS. `warp dns serve` runs that bridge; the printed commands point the
machine's resolver at it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import netprobe
from .dnsproxy import DEFAULT_LISTEN

# Local resolver daemons that can own port 53, with the config files worth
# looking at. Only ever read; edits are printed for the user to run.
RESOLVER_SERVICES = ("blocky", "systemd-resolved", "dnsmasq", "unbound",
                     "dnscrypt-proxy", "adguardhome")
CONFIG_PATHS = {
    "blocky": ("/etc/blocky/blocky.yml", "/etc/blocky/config.yml"),
    "dnsmasq": ("/etc/dnsmasq.conf",),
    "unbound": ("/etc/unbound/unbound.conf",),
    "dnscrypt-proxy": ("/etc/dnscrypt-proxy/dnscrypt-proxy.toml",),
}
_SERVICE_RESTART = {
    "blocky": "sudo systemctl restart blocky",
    "dnsmasq": "sudo systemctl restart dnsmasq",
    "unbound": "sudo systemctl restart unbound",
    "dnscrypt-proxy": "sudo systemctl restart dnscrypt-proxy",
    "adguardhome": "sudo systemctl restart AdGuardHome",
    "systemd-resolved": "sudo systemctl restart systemd-resolved",
}


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return 127, ""


def resolv_conf_servers() -> list[str]:
    servers = []
    try:
        for line in Path("/etc/resolv.conf").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver":
                servers.append(parts[1])
    except OSError:
        pass
    return servers


def active_services() -> list[str]:
    found = []
    for name in RESOLVER_SERVICES:
        code, out = _run(["systemctl", "is-active", name])
        if code == 0 and out.strip() == "active":
            found.append(name)
    return found


def upstream_of(service: str) -> tuple[str | None, str | None]:
    """(config path, first upstream line) for a service we can read."""
    for path in CONFIG_PATHS.get(service, ()):
        p = Path(path)
        try:
            text = p.read_text()
        except OSError:
            continue
        # Take the first URL line. Prefer one that is labelled upstream/server,
        # but blocky's YAML puts the URL on its own unlabelled line, so fall back
        # to any URL rather than reporting "no upstream found".
        url_line = None
        for line in text.splitlines():
            if "http://" in line or "https://" in line:
                url_line = line
                if any(k in line.lower() for k in ("upstream", "server", "nameserver")):
                    break
        if url_line:
            return path, url_line.strip().strip('"-, ')
    for path in CONFIG_PATHS.get(service, ()):
        if Path(path).exists():
            return path, None
    return None, None


def detect() -> dict:
    servers = resolv_conf_servers()
    services = active_services()
    tunnel = netprobe.port_open("127.0.0.1", 25344)
    local = [s for s in services if s != "systemd-resolved"]
    return {
        "resolv_conf": servers,
        "stub": any(s.startswith("127.0.0.53") for s in servers),
        "services": services,
        "local_resolver": local[0] if local else None,
        "tunnel_up": tunnel,
    }


def render(info: dict, listen: tuple[str, int] = DEFAULT_LISTEN) -> str:
    out: list[str] = ["DNS on this machine", ""]
    out.append(f"  /etc/resolv.conf : {', '.join(info['resolv_conf']) or '(none)'}"
               + ("  (systemd-resolved stub)" if info["stub"] else ""))
    if info["services"]:
        out.append(f"  active resolvers : {', '.join(info['services'])}")
    svc = info["local_resolver"]
    if svc:
        path, upstream = upstream_of(svc)
        if path:
            out.append(f"  {svc + ' config':17}: {path}")
        if upstream:
            out.append(f"  {svc + ' upstream':17}: {upstream}")
    out.append(f"  tunnel           : {'up' if info['tunnel_up'] else 'DOWN — run: warp up'}")
    out += [
        "",
        "DNS is NOT tunnelled. The tunnel is a SOCKS proxy and carries TCP only, so",
        "plain UDP/53 cannot cross it. DoH can — it is HTTPS — so the bridge is a",
        "local DoH forwarder plus one resolver change.",
        "",
        f"  1. no root   run the forwarder in its own terminal:",
        f"       warp dns serve                    # udp://{listen[0]}:{listen[1]} -> DoH via the tunnel",
    ]
    if svc and svc in _SERVICE_RESTART:
        path, upstream = upstream_of(svc)
        out += ["", f"  2. root      point {svc} at the forwarder:"]
        if path and upstream and "http" in upstream:
            out += [
                f"       sudo cp {path} {path}.bak",
                f"       sudo sed -i 's|{upstream}|{listen[0]}:{listen[1]}|' {path}",
                f"       {_SERVICE_RESTART[svc]}",
                "",
                "     undo:",
                f"       sudo cp {path}.bak {path} && {_SERVICE_RESTART[svc]}",
            ]
        elif path:
            out += [
                f"       # set {svc}'s upstream to {listen[0]}:{listen[1]} in {path}",
                f"       {_SERVICE_RESTART[svc]}",
            ]
    else:
        out += [
            "",
            "  2. root      point the machine's resolver at the forwarder. With no local",
            "     resolver owning port 53, the forwarder itself must, so run it elevated:",
            f"       sudo python3 -m warp dns serve --listen {listen[0]}:53",
            f"       (or point an existing resolver's upstream at {listen[0]}:{listen[1]})",
        ]
    out += [
        "",
        "  caveat: once the resolver depends on the forwarder, DNS depends on the",
        "  tunnel. If warp stops, lookups fail — keep the undo line handy.",
        "",
        "  no root at all? browsers can use DoH directly "
        "(https://cloudflare-dns.com/dns-query).",
    ]
    return "\n".join(out)
