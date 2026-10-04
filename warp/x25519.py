"""X25519 (RFC 7748) in pure Python — stdlib only.

WireGuard keys are X25519. On Linux we could shell out to `wg genkey`, but that
binary doesn't exist on Windows and may be missing on macOS, so the app generates
keys itself. This is a public-key scalarmult, not a cipher: it is short, fully
specified, and checked against the RFC 7748 vectors in `selftest()`.

WireGuard's key format is standard base64 of the 32 raw bytes:
  private key = 32 random bytes (clamped inside the ladder)
  public key  = scalarmult(private, 9)
"""

from __future__ import annotations

import base64
import os

P = (1 << 255) - 19
_A24 = 121665
_BASE_U = b"\x09" + b"\x00" * 31


def _clamp(k: bytes) -> bytearray:
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return k


def _decode_u(u: bytes) -> int:
    return (int.from_bytes(u, "little") & ((1 << 255) - 1)) % P


def scalarmult(k: bytes, u: bytes) -> bytes:
    """Return the X25519 scalar-mult of `k` and u-coordinate `u` (both 32 bytes)."""
    if len(k) != 32 or len(u) != 32:
        raise ValueError("X25519 inputs must be 32 bytes")
    kk = int.from_bytes(_clamp(k), "little")
    x1 = _decode_u(u)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in reversed(range(255)):
        kt = (kk >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) % P
        x3 = x3 * x3 % P
        z3 = (da - cb) % P
        z3 = z3 * z3 % P
        z3 = z3 * x1 % P
        x2 = aa * bb % P
        z2 = e * ((aa + _A24 * e) % P) % P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return ((x2 * pow(z2, P - 2, P)) % P).to_bytes(32, "little")


def generate_private_key() -> bytes:
    return os.urandom(32)


def public_key(private: bytes) -> bytes:
    return scalarmult(private, _BASE_U)


def to_b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def from_b64(text: str) -> bytes:
    return base64.b64decode(text)


def selftest() -> None:
    """RFC 7748 §5.2 vectors. Raises AssertionError on a broken implementation."""
    def hx(s: str) -> bytes:
        return bytes.fromhex(s)

    out = scalarmult(
        hx("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"),
        hx("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c"),
    )
    assert out.hex() == "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552", out.hex()

    out = scalarmult(
        hx("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d"),
        hx("e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493"),
    )
    assert out.hex() == "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957", out.hex()

    # base point: k = u = 9 → the well-known public key for scalar 9
    assert public_key(_BASE_U).hex() == \
        "422c8e7a6227d7bca1350b3e2bb7279f7897b87bb6854b783c60e80311ae3079"
    print("x25519 selftest OK")


if __name__ == "__main__":
    selftest()
