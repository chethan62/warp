"""Cloudflare WARP account registration and wireproxy config generation.

Registration is a single HTTP call to Cloudflare's client API — no root, no
third-party binary. The account's private key is generated locally (see
`x25519`), so Cloudflare never sees anything but our public key.
"""

from __future__ import annotations

import base64
import datetime
import json
import urllib.error
import urllib.request

from . import paths
from .x25519 import generate_private_key, public_key, to_b64

REGISTER_URL = "https://api.cloudflareclient.com/v0a2158/reg"

# WARP's consumer API accepts the mobile client's identity.
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "WARP for Android",
    "CF-Client-Version": "a-6.30-2158",
}


def register(timeout: int = 45) -> dict:
    """Create a WARP account; returns the normalised account record."""
    priv = generate_private_key()
    pub = to_b64(public_key(priv))
    body = {
        "key": pub,
        "install_id": "",
        "fcm_token": "",
        "tos": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "model": "PC",
        "serial_number": "",
        "locale": "en_US",
    }
    req = urllib.request.Request(REGISTER_URL, data=json.dumps(body).encode(), headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"WARP registration failed: HTTP {exc.code} {exc.read().decode()[:200]}") from exc

    cfg = data["config"]
    peer = cfg["peers"][0]
    client_id = cfg.get("client_id", "")
    return {
        "id": data.get("id"),
        "client_id": client_id,
        # Cloudflare needs the first 3 bytes of the client id as the peer's
        # reserved value; wireproxy takes them on the peer line.
        "reserved": list(base64.b64decode(client_id))[:3] if client_id else [],
        "private_key": to_b64(priv),
        "public_key": pub,
        "peer_public_key": peer["public_key"],
        "endpoint": peer["endpoint"]["host"],
        "address_v4": cfg["interface"]["addresses"].get("v4"),
        "address_v6": cfg["interface"]["addresses"].get("v6"),
    }


def load() -> dict | None:
    try:
        return json.loads(paths.account_path().read_text())
    except (OSError, ValueError):
        return None


def save(account: dict) -> None:
    p = paths.account_path()
    p.write_text(json.dumps(account, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass  # Windows has no POSIX modes; the file is per-user anyway


def ensure_account(timeout: int = 45) -> dict:
    acct = load()
    if acct and acct.get("private_key") and acct.get("peer_public_key"):
        return acct
    acct = register(timeout=timeout)
    save(acct)
    return acct


def reset() -> None:
    paths.account_path().unlink(missing_ok=True)


def render_config(account: dict, socks_port: int) -> str:
    """A wireproxy config: WireGuard interface + a local SOCKS5 listener.

    SOCKS5 only. An `[http]` section is accepted by `wireproxy -n` and even binds
    its port, but the binary does not actually serve it (measured: 0/3 requests
    answered, while 3/3 SOCKS5 requests succeeded) — pointing the OS at a dead
    listener is worse than not offering one. Every platform backend below can
    drive SOCKS, so one listener is enough.

    No `DNS =` line either: wireproxy is a proxy, not a TUN device, so it would
    be inert and would wrongly imply the system resolver moves into the tunnel.
    """
    reserved = ""
    if account.get("reserved"):
        reserved = "\nReserved = " + ",".join(str(b) for b in account["reserved"])
    return f"""[Interface]
PrivateKey = {account['private_key']}
Address = {account['address_v4']}/32

[Peer]
PublicKey = {account['peer_public_key']}
Endpoint = {account['endpoint']}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25{reserved}

[Socks5]
BindAddress = 127.0.0.1:{socks_port}
"""


def write_config(account: dict, socks_port: int) -> str:
    path = paths.wireproxy_conf_path()
    path.write_text(render_config(account, socks_port))
    try:
        path.chmod(0o600)  # contains the private key
    except OSError:
        pass
    return str(path)
