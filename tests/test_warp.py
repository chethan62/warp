"""Runnable checks: `python3 tests/test_warp.py` (no pytest required).

Covers the logic that would silently break the tunnel: key derivation, the
generated wireproxy config, and the chunked-response decoder.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from warp import cloudflare, dnsproxy, provision, sysproxy, x25519  # noqa: E402
from warp.netprobe import _dechunk  # noqa: E402


def test_x25519_vectors():
    x25519.selftest()  # raises on failure


def test_socks_only_listener():
    # wireproxy's [http] section binds but never answers (measured 0/3 vs 3/3),
    # so the OS must be pointed at SOCKS5 and no [http] section may come back.
    acct = {"private_key": "P", "address_v4": "172.16.0.2",
            "peer_public_key": "Q", "endpoint": "e:2408", "reserved": []}
    cfg = cloudflare.render_config(acct, 25344)
    assert "[Socks5]" in cfg and "BindAddress = 127.0.0.1:25344" in cfg, cfg
    assert "[http]" not in cfg, cfg


def test_provision_asset_matches_platform():
    import platform, sys
    name = provision.asset_name()
    assert name is None or name.endswith(".tar.gz"), name
    if sys.platform.startswith("linux") and platform.machine() == "x86_64":
        assert name == "wireproxy_linux_amd64.tar.gz", name
    assert provision.binary_name().startswith("wireproxy")


def test_sysproxy_status_shape():
    st = sysproxy.status()
    for key in ("supported", "backend", "enabled", "proxy"):
        assert key in st, st


def test_config_shape():
    acct = {
        "private_key": "PRIV", "address_v4": "172.16.0.2",
        "peer_public_key": "PEER", "endpoint": "engage.cloudflareclient.com:2408",
        "reserved": [46, 61, 101],
    }
    cfg = cloudflare.render_config(acct, socks_port=25344)
    for needle in ("PrivateKey = PRIV", "Address = 172.16.0.2/32", "PublicKey = PEER",
                   "Endpoint = engage.cloudflareclient.com:2408", "AllowedIPs = 0.0.0.0/0",
                   "Reserved = 46,61,101", "BindAddress = 127.0.0.1:25344"):
        assert needle in cfg, f"config missing {needle!r}:\n{cfg}"
    # no reserved key -> no Reserved line (older accounts)
    assert "Reserved" not in cloudflare.render_config({**acct, "reserved": []}, 1)


def test_no_inert_dns_line():
    # wireproxy is a proxy, not a TUN: a DNS= key here would do nothing and
    # wrongly imply the system resolver moved into the tunnel.
    acct = {"private_key": "P", "address_v4": "172.16.0.2",
            "peer_public_key": "Q", "endpoint": "e:2408", "reserved": []}
    assert "DNS" not in cloudflare.render_config(acct, 25344)


def test_servfail_shape():
    # a SERVFAIL must echo the query id + question, so clients fail fast
    q = bytes.fromhex("123401000001000000000000") + b"\x07example\x03com\x00" + bytes.fromhex("00010001")
    r = dnsproxy.servfail(q)
    assert len(r) >= 12, r
    assert r[:2] == q[:2], "id must be echoed"
    assert r[3] & 0x0F == 2, "rcode must be SERVFAIL"
    assert int.from_bytes(r[4:6], "big") == 1, "qdcount must be preserved"
    assert dnsproxy.servfail(b"short") == b""


def test_dechunk():
    assert _dechunk(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n") == b"Wikipedia"
    assert _dechunk(b"plain, not chunked") == b"plain, not chunked"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print("OK" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
