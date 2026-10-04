"""Read our egress facts through the tunnel.

Deliberately dependency-free: Python's stdlib has no SOCKS support, so this is a
minimal SOCKS5 CONNECT client plus a one-shot HTTPS GET. That keeps the app
portable (no curl, no PySocks) and is the only thing needed to answer
"connected, and from which IP?".
"""

from __future__ import annotations

import socket
import ssl


class ProbeError(RuntimeError):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ProbeError("connection closed during handshake")
        buf += chunk
    return buf


def socks5_connect(host: str, port: int, target_host: str, target_port: int, timeout: float = 20) -> socket.socket:
    """Open a TCP stream to target through the SOCKS5 proxy."""
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.sendall(b"\x05\x01\x00")  # version 5, one method, "no auth"
        if _recv_exact(s, 2) != b"\x05\x00":
            raise ProbeError("SOCKS5 proxy refused the no-auth method")
        name = target_host.encode("idna")
        req = b"\x05\x01\x00\x03" + bytes([len(name)]) + name + target_port.to_bytes(2, "big")
        s.sendall(req)
        head = _recv_exact(s, 4)
        if head[1] != 0x00:
            raise ProbeError(f"SOCKS5 connect refused (code {head[1]})")
        atyp = head[3]
        if atyp == 0x01:
            _recv_exact(s, 4)
        elif atyp == 0x03:
            _recv_exact(s, _recv_exact(s, 1)[0])
        elif atyp == 0x04:
            _recv_exact(s, 16)
        _recv_exact(s, 2)  # bound port
        return s
    except Exception:
        s.close()
        raise


def _dechunk(body: bytes) -> bytes:
    out = b""
    while body:
        line, _, rest = body.partition(b"\r\n")
        try:
            size = int(line.split(b";")[0], 16)
        except ValueError:
            return out or body
        if size == 0:
            return out
        out += rest[:size]
        body = rest[size + 2:]
    return out


def https_request(host: str, path: str, proxy: tuple[str, int], method: str = "GET",
                  body: bytes | None = None, headers: dict | None = None,
                  timeout: float = 25) -> tuple[int, bytes]:
    """One HTTPS request through the SOCKS5 proxy. Returns (status, body bytes).

    Generic so the egress probe (GET) and the DoH forwarder (POST) share the
    tunnel plumbing instead of each growing their own copy.
    """
    raw = socks5_connect(proxy[0], proxy[1], host, 443, timeout)
    try:
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise

    hdrs = {"Host": host, "User-Agent": "warp", "Accept": "*/*", "Connection": "close"}
    hdrs.update(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Length", str(len(body)))
    head = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())

    with tls:
        tls.sendall(f"{method} {path} HTTP/1.1\r\n{head}\r\n".encode() + (body or b""))
        data = b""
        while True:
            chunk = tls.recv(4096)
            if not chunk:
                break
            data += chunk

    raw_head, _, payload = data.partition(b"\r\n\r\n")
    lines = raw_head.split(b"\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        status = 0
    if b"transfer-encoding: chunked" in raw_head.lower():
        payload = _dechunk(payload)
    return status, payload


def https_get(host: str, path: str, proxy: tuple[str, int], timeout: float = 25) -> str:
    """GET https://host/path through the SOCKS5 proxy; returns the body text."""
    return https_request(host, path, proxy, timeout=timeout)[1].decode("utf-8", "replace")


def egress(proxy: tuple[str, int], timeout: float = 25) -> dict:
    """Cloudflare's own trace, read through the tunnel: the honest proof of
    whether traffic is exiting via WARP and from which address."""
    text = https_get("www.cloudflare.com", "/cdn-cgi/trace", proxy, timeout)
    facts = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            facts[key.strip()] = value.strip()
    return {"ip": facts.get("ip"), "warp": facts.get("warp"), "loc": facts.get("loc")}


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
