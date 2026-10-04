"""DNS-over-HTTPS forwarder that rides the SOCKS5 tunnel.

Plain UDP/53 cannot cross a SOCKS proxy — SOCKS carries TCP streams, not
datagrams. DoH can: it is an ordinary HTTPS POST, and HTTPS crosses SOCKS fine.
So this listens for normal DNS queries on UDP and relays the **raw wire bytes**
to Cloudflare's DoH endpoint through the tunnel, returning the raw answer.

No DNS parsing anywhere: the message format is identical on both sides of DoH.

    warp dns serve                 # 127.0.0.1:5053, needs the tunnel up
    dig @127.0.0.1 -p 5053 example.com
"""

from __future__ import annotations

import socket
import sys
import threading

from . import netprobe

DOH_HOST = "cloudflare-dns.com"
DOH_PATH = "/dns-query"
DEFAULT_LISTEN = ("127.0.0.1", 5053)
MAX_DATAGRAM = 4096


def query(payload: bytes, proxy: tuple[str, int], timeout: float = 10) -> bytes:
    """Relay raw DNS wire bytes over DoH (through `proxy`); return wire bytes."""
    status, body = netprobe.https_request(
        DOH_HOST, DOH_PATH, proxy, method="POST", body=payload,
        headers={"Content-Type": "application/dns-message",
                 "Accept": "application/dns-message"},
        timeout=timeout,
    )
    if status != 200 or not body:
        raise RuntimeError(f"DoH upstream returned HTTP {status}")
    return body


def servfail(query_bytes: bytes) -> bytes:
    """A minimal SERVFAIL echoing the question.

    Returning nothing would make every failure look like the 5-second client
    timeout; an immediate SERVFAIL is both faster and diagnosable.
    """
    if len(query_bytes) < 12:
        return b""
    header = query_bytes[:2] + b"\x81\x82" + b"\x00\x01" + b"\x00" * 6
    i = 12
    while i < len(query_bytes) and query_bytes[i] != 0:
        i += query_bytes[i] + 1
    end = min(i + 5, len(query_bytes))
    return header + query_bytes[12:end]


def _handle(sock: socket.socket, data: bytes, addr, proxy, on_query) -> None:
    try:
        answer = query(data, proxy)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a SERVFAIL
        if on_query:
            on_query(f"upstream failed: {exc}")
        answer = servfail(data)
    if answer:
        try:
            sock.sendto(answer, addr)
        except OSError:
            pass


def serve(listen: tuple[str, int] = DEFAULT_LISTEN, proxy: tuple[str, int] = ("127.0.0.1", 25344),
          on_query=None) -> None:
    """Serve DNS on UDP until interrupted. Returns when stop() is signalled."""
    if not netprobe.port_open(*proxy):
        raise RuntimeError(
            f"tunnel is not up on {proxy[0]}:{proxy[1]} — run 'warp up' first, "
            "otherwise every lookup would fail")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(listen)
    print(f"warp dns: serving on udp://{listen[0]}:{listen[1]} → DoH via {proxy[0]}:{proxy[1]}")
    try:
        while True:
            data, addr = sock.recvfrom(MAX_DATAGRAM)
            threading.Thread(target=_handle, args=(sock, data, addr, proxy, on_query),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        sock.close()


if __name__ == "__main__":
    try:
        serve()
    except RuntimeError as err:
        print(f"warp dns: {err}", file=sys.stderr)
        sys.exit(1)
