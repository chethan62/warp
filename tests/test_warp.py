"""Runnable checks: `python3 tests/test_warp.py` (no pytest required).

Covers the logic that would silently break the tunnel: key derivation, the
generated wireproxy config, and the chunked-response decoder.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from warp import cloudflare, dnsproxy, provision, sysproxy, window, x25519  # noqa: E402
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


def test_sysproxy_backend_is_detected_per_platform():
    """The backend must actually be FOUND on macOS and Windows.

    test_sysproxy_status_shape only asserts the four keys exist, which
    "supported: False" also satisfies — so the macOS and Windows CI jobs would
    have gone green while detecting nothing at all. On macOS in particular,
    backend() returns None unless networksetup is on PATH.
    """
    st = sysproxy.status()
    if sys.platform == "darwin":
        assert st["backend"] == "macos", f"macOS backend not detected: {st}"
    elif sys.platform == "win32":
        assert st["backend"] == "windows", f"Windows backend not detected: {st}"
    else:
        # a CI runner has no desktop session, so there may legitimately be no
        # backend here; only that nothing impossible is reported
        assert st["backend"] in (None, "kde", "gnome"), st


def test_window_opens_in_app_mode():
    cmd = window.command(["chromium"], "http://127.0.0.1:8787/", "/tmp/prof")
    assert cmd[0] == "chromium"
    assert "--app=http://127.0.0.1:8787/" in cmd, cmd
    assert "--class=warp" in cmd, cmd
    assert "--user-data-dir=/tmp/prof" in cmd, cmd
    # a fresh profile shows Chrome's first-run flow without these
    assert "--no-first-run" in cmd and "--no-default-browser-check" in cmd, cmd


def test_flatpak_profile_goes_where_the_sandbox_can_write():
    """~/.config does not exist inside a Flatpak sandbox.

    Verified with `flatpak run --command=sh com.google.Chrome -c 'ls ~/.config/warp'`
    -> "No such file or directory", because Flatpak exposes only ~/.var/app/<id>.
    A host path there is silently ignored and the browser falls back to its own
    profile, which is what happened before this was fixed.
    """
    host = Path("/home/x/.config/warp/browser")
    flat = window.profile_for(["flatpak", "run", "com.google.Chrome"], host)
    expected = str(Path.home() / ".var" / "app" / "com.google.Chrome"
                   / "config" / "warp-browser")
    assert flat == expected, flat
    # a normal launcher keeps the host path. Compare Path-to-Path: str(Path)
    # uses backslashes on Windows, so a "/tmp/p" literal would only pass on POSIX
    # (it did not, and the Windows job is the only one that could tell).
    assert window.profile_for(["chromium"], host) == str(host)
    assert window.profile_for(["chromium"], None) is None


def test_no_browser_falls_back_to_a_tab():
    from unittest import mock
    with mock.patch.object(window, "find", return_value=None), \
         mock.patch("webbrowser.open", return_value=True) as opened:
        assert window.open_ui("http://127.0.0.1:8787/") == "tab"
        opened.assert_called_once()


def test_prefer_tab_skips_the_window_entirely():
    from unittest import mock
    with mock.patch.object(window, "find") as find, \
         mock.patch("webbrowser.open", return_value=True):
        assert window.open_ui("http://127.0.0.1:8787/", prefer_tab=True) == "tab"
        find.assert_not_called()


def test_installed_wireproxy_is_used_before_downloading():
    """The README promised a PATH lookup that the code never did.

    On a BSD it is not a preference but the only route: upstream publishes no
    BSD binary, so a package-manager copy is the sole way to have one.
    """
    from unittest import mock
    with mock.patch.object(provision, "bin_path", return_value="/nonexistent/wireproxy"), \
         mock.patch.object(provision, "system_wireproxy",
                           return_value="/usr/local/bin/wireproxy"), \
         mock.patch.object(provision, "_is_runnable", return_value=True):
        assert provision.ensure() == "/usr/local/bin/wireproxy"


def test_bsd_reports_what_to_actually_do():
    """'no wireproxy build for freebsd13' reads as a bug, not an instruction."""
    from unittest import mock
    with mock.patch.object(sys, "platform", "freebsd13"), \
         mock.patch.object(platform, "machine", return_value="amd64"), \
         mock.patch.object(provision, "bin_path", return_value="/nonexistent/wireproxy"), \
         mock.patch.object(provision, "system_wireproxy", return_value=None):
        try:
            provision.ensure()
        except RuntimeError as exc:
            msg = str(exc)
            assert "no freebsd13 build" in msg, msg
            assert "pkg install wireproxy" in msg, msg
            assert "go install" in msg, msg
        else:
            raise AssertionError("expected RuntimeError on a BSD with nothing installed")


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


def test_windows_arm64_uses_the_emulated_asset():
    # upstream publishes no windows/arm64 build; Windows 11 on ARM runs amd64
    from unittest import mock
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(platform, "machine", return_value="ARM64"):
        assert provision.asset_name() == "wireproxy_windows_amd64.tar.gz", provision.asset_name()


def test_ui_contrast_tokens():
    """Every colour pair the UI paints clears its WCAG floor.

    Text needs 4.5:1; non-text UI — the switch track, the off glyph, the state
    dot — needs 3:1 (WCAG 1.4.11). The card is translucent, so it is composited
    over the gradient's first stop before measuring.

    This exists because a dark-only screenshot pass left the light theme
    unverified, and the light off-state was measurably invisible (1.65:1).
    """
    import re

    src = (Path(__file__).resolve().parent.parent / "warp" / "ui" / "index.html").read_text()

    def tokens(pattern):
        m = re.search(pattern, src, re.S)
        assert m, "token block not found — did the CSS move?"
        return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", m.group(1)))

    def rgb(c):
        if c.startswith("#"):
            h = c[1:]
            if len(h) == 3:
                h = "".join(x * 2 for x in h)
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
        p = [x.strip() for x in re.match(r"rgba?\(([^)]+)\)", c).group(1).split(",")]
        return (int(p[0]), int(p[1]), int(p[2]), float(p[3]) if len(p) > 3 else 1.0)

    def over(fg, bg):
        return tuple(round(fg[i] * fg[3] + bg[i] * (1 - fg[3])) for i in range(3))

    def lum(c):
        def f(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

    def ratio(x, y):
        lx, ly = lum(x), lum(y)
        return (max(lx, ly) + 0.05) / (min(lx, ly) + 0.05)

    pairs = [("text", 4.5), ("text-2", 4.5), ("text-3", 4.5), ("accent", 4.5),
             ("err", 4.5), ("ok", 3.0), ("idle-2", 3.0), ("idle", 3.0)]
    themes = {
        "light": tokens(r":root\{(.*?)\}"),
        "dark": tokens(r"prefers-color-scheme:dark\)\{:root\{(.*?)\}\}"),
    }
    for name, toks in themes.items():
        assert toks, f"{name} token block empty"
        card = over(rgb(toks["card"]), rgb(re.search(r"#[0-9a-f]{6}", toks["bg"]).group(0)))
        for tok, need in pairs:
            got = ratio(rgb(toks[tok]), card)
            assert got >= need, f"{name}: --{tok} is {got:.2f}:1 on the card, needs {need}:1"


def test_dechunk():
    assert _dechunk(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n") == b"Wikipedia"
    assert _dechunk(b"plain, not chunked") == b"plain, not chunked"


def test_the_ui_server_stops_when_its_window_closes():
    """Closing the app window must not leave the server holding the port.

    The page sends POST /api/quit from a pagehide beacon. The server honours it
    only if the route exists AND shutdown() runs off the serving thread, so both
    halves are worth pinning.
    """
    import threading as _threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from warp import webui
    from warp.tunnel import Tunnel

    # a socks port nothing listens on, so status() answers instantly
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(Tunnel(1)))
    port = httpd.server_address[1]
    serving = _threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    try:
        assert urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10).status == 200

        # control: a stray GET must not stop it, or any page could end the server
        try:
            urlopen(f"http://127.0.0.1:{port}/api/quit", timeout=10)
            raise AssertionError("GET /api/quit did not 404")
        except HTTPError as exc:
            assert exc.code == 404, exc.code
        assert serving.is_alive(), "a GET /api/quit stopped the server"

        req = Request(f"http://127.0.0.1:{port}/api/quit", method="POST")
        assert urlopen(req, timeout=10).status == 200
        serving.join(timeout=10)
        assert not serving.is_alive(), "the server survived the close beacon"
    finally:
        httpd.server_close()


def test_a_stale_system_proxy_is_detected_and_repaired():
    """A proxy that outlives its tunnel breaks the machine rather than routing it.

    It has to be undone - but only when it is OURS and only while the tunnel is
    down, or the repair itself becomes the fault. Neither control is theoretical:
    clobbering another tool's proxy setting, or tearing down a route that is
    working, would both be worse than the stale state.
    """
    from warp import sysproxy
    from warp.tunnel import Tunnel

    t = Tunnel(1)                        # nothing listens on port 1
    real_status, real_clear = sysproxy.status, sysproxy.clear
    cleared = []

    def fake_status(**kwargs):
        return {"supported": True, "backend": "kde", "enabled": True,
                "proxy": fake_status.proxy}

    try:
        sysproxy.clear = lambda: (cleared.append(True), {"ok": True})[1]

        # ours, tunnel down -> stale, and repaired
        fake_status.proxy = f"socks://127.0.0.1:{t.socks_port}"
        sysproxy.status = fake_status
        assert t.status(probe=False)["system_proxy_stale"] is True
        assert t.repair_stale_proxy() is True
        assert cleared == [True], "the stale proxy was left in place"

        # control 1: a proxy that is not ours is none of our business
        cleared.clear()
        fake_status.proxy = "socks://127.0.0.1:9999"
        assert t.status(probe=False)["system_proxy_stale"] is False
        assert t.repair_stale_proxy() is False
        assert cleared == [], "cleared a proxy that was not ours"

        # control 2: ours with the tunnel up is a working route, not stale
        cleared.clear()
        fake_status.proxy = f"socks://127.0.0.1:{t.socks_port}"
        t.is_up = lambda: True
        assert t.status(probe=False)["system_proxy_stale"] is False
        assert t.repair_stale_proxy() is False
        assert cleared == [], "tore down a route that was working"
    finally:
        sysproxy.status, sysproxy.clear = real_status, real_clear


def test_the_module_stack_stays_acyclic_and_leaves_first():
    """warp is layered leaves-first: no cycles, and the low-level modules never
    reach back up.

    A cycle here is not a style complaint. It is the thing that turns a small fix
    into "understand the whole package first", which is exactly what makes this
    harder to debug than it needs to be - so it is worth failing a build over.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "warp"
    names = sorted(p.stem for p in root.glob("*.py") if p.stem != "__init__")

    graph: dict[str, set[str]] = {}
    for path in sorted(root.glob("*.py")):
        if path.stem == "__init__":
            continue
        deps: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level:
                if node.module:
                    deps.add(node.module.split(".")[0])
                else:
                    deps.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                deps.update(a.name.split(".")[1] for a in node.names
                            if a.name.startswith("warp."))
        graph[path.stem] = deps & set(names)

    # no cycles: a topological order must exist and must consume every module
    order, seen = [], {}

    def visit(node, stack):
        if seen.get(node) == 1:
            raise AssertionError(f"import cycle: {' -> '.join(stack + [node])}")
        if seen.get(node) == 2:
            return
        seen[node] = 1
        for dep in sorted(graph[node]):
            visit(dep, stack + [node])
        seen[node] = 2
        order.append(node)

    for module in names:
        visit(module, [])
    assert sorted(order) == names, "the graph is not fully walkable"

    # the bottom of the stack stays the bottom: these must not grow a dependency
    # on anything else in the package, or every layer above them gets muddier
    for leaf in ("netprobe", "paths", "sysproxy", "window", "x25519"):
        assert leaf in graph, f"{leaf} disappeared"
        assert not graph[leaf], f"{leaf} now imports {sorted(graph[leaf])} - it is a leaf"


def test_every_backend_reports_a_proxy_identity_we_can_check():
    """The stale/clear guard keys on status()['proxy'], so that string has to
    carry the port on EVERY backend.

    It did not. GNOME returned the host alone and macOS returned a placeholder
    sentence, so on those two the proxy could never be recognised as ours:
    stop() left it in place, nothing was ever reported stale, and the startup
    repair was a no-op - the machine stayed pointed at a dead port with no way
    back. Only the KDE shape was ever exercised, which is why it shipped.
    """
    from warp import sysproxy

    real_run, real_backend, real_first = sysproxy._run, sysproxy.backend, sysproxy._first
    port = 25344

    def fakery(answers, tool):
        def _run(cmd, timeout=25):
            joined = " ".join(cmd)
            for needle, value in answers:
                if needle in joined:
                    return 0, value
            return 0, ""
        return _run, (lambda *a: tool)

    macos_out = "Enabled: Yes" + chr(10) + "Server: 127.0.0.1" + chr(10) + "Port: " + str(port)
    cases = {
        "kde": ([("--key httpProxy", "socks://127.0.0.1:" + str(port)),
                 ("--key ProxyType", "1")], "kreadconfig6"),
        "gnome": ([("proxy mode", "manual"), ("socks host", "'127.0.0.1'"),
                   ("socks port", str(port))], "gsettings"),
        # the real command prints a header line first, and _macos_service()
        # skips it - a fake without one finds no service at all
        "macos": ([("-listallnetworkservices",
                    "An asterisk (*) denotes that a network service is disabled." + chr(10) + "Wi-Fi"),
                   ("-getsocksfirewallproxy", macos_out)], "networksetup"),
        "windows": ([("ProxyEnable", "ProxyEnable REG_DWORD 0x1"),
                     ("ProxyServer", "ProxyServer REG_SZ socks=127.0.0.1:" + str(port))], "reg"),
    }
    try:
        for name, (answers, tool) in cases.items():
            sysproxy.backend = lambda n=name: n
            sysproxy._run, sysproxy._first = fakery(answers, tool)
            info = sysproxy.status()
            assert info["enabled"], name + ": status did not report the proxy as enabled"
            assert sysproxy.is_ours(info["proxy"], port), (
                name + ": status reported " + repr(info["proxy"]) + ", which the guard "
                "cannot recognise as ours - so it would never be cleared")
    finally:
        sysproxy._run, sysproxy.backend, sysproxy._first = real_run, real_backend, real_first


def test_the_guard_rejects_what_is_not_ours():
    """The other half: a proxy we did not set must not be claimed or cleared."""
    from warp import sysproxy

    assert not sysproxy.is_ours(None, 25344)
    assert not sysproxy.is_ours("", 25344)
    assert not sysproxy.is_ours("(see networksetup -getsocksfirewallproxy)", 25344)
    assert not sysproxy.is_ours("socks://10.0.0.5:25344", 25344)   # right port, wrong host
    assert not sysproxy.is_ours("socks://127.0.0.1:4096", 25344)   # right host, wrong port
    assert sysproxy.is_ours("socks://127.0.0.1:25344", 25344)
    assert sysproxy.is_ours("127.0.0.1:25344", 25344)
    assert sysproxy.is_ours("socks=127.0.0.1:25344", 25344)
    assert sysproxy.is_ours("http://localhost:25344", 25344)



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
