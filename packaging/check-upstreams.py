#!/usr/bin/env python3
"""Check that the third-party artifacts warp is pinned to still exist upstream.

warp stands on two things it does not control: a wireproxy release and a
python-build-standalone release. Both are pinned by name, so a yank, a rename,
or an upstream dropping a Python version breaks a first run or a build with no
warning from this repository at all. This asks upstream directly.

Run it in CI on a schedule — that is the point. A push-triggered job only ever
tells you about code you just changed; this tells you when the ground moved.

Exit code is non-zero if anything is missing or unresolvable.

    python3 packaging/check-upstreams.py
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from warp import provision  # noqa: E402  (needs the sys.path line above)

UA = {"User-Agent": "warp-upstream-check"}


def head(url: str) -> tuple[int | None, str]:
    """Return (status, detail) for a URL, following redirects."""
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            size = resp.headers.get("Content-Length")
            return resp.status, f"{size} bytes" if size else "ok"
    except urllib.error.HTTPError as exc:
        return exc.code, exc.reason
    except Exception as exc:  # noqa: BLE001 - any failure is a failed check
        return None, type(exc).__name__


# The combinations a real machine can actually report. Deliberately NOT the
# cross product of provision's tables: darwin/386 and windows/arm cannot occur
# (32-bit macOS is long dead, Windows-on-ARM is 64-bit), and checking them would
# mean three permanent false alarms that hide a real miss.
def wireproxy_assets() -> list[str]:
    combos = [("linux", a) for a in ("386", "amd64", "arm", "arm64")]
    combos += [("darwin", "amd64"), ("darwin", "arm64")]
    combos += [("windows", a) for a in ("386", "amd64", "arm64")]
    # windows/arm64 resolves through provision's emulation fallback
    combos = [(p, provision._EMULATED.get(("win32", a), a)) for p, a in combos]
    return [f"wireproxy_{p}_{a}.tar.gz" for p, a in combos]


def _pinned(src: str, name: str) -> str | None:
    # PBS_TAG="${PBS_TAG:-20261003}"  ->  20261003
    # Plain string work, not a regex: the escaped pattern for this was wrong
    # twice and silently disabled the check both times, which is a bad property
    # for the one thing whose only job is to notice.
    prefix = f'{name}="${{{name}:-'
    for line in src.splitlines():
        line = line.strip()
        if line.startswith(prefix) and line.endswith('}"'):
            return line[len(prefix):-2]
    return None


def build_pins() -> dict:
    """Every upstream artifact the build pins, read from the build script."""
    path = ROOT / "packaging" / "build-linux.sh"
    try:
        src = path.read_text()
    except OSError as exc:
        # a rename here silently disables the whole check, so say so loudly
        raise SystemExit(f"cannot read {path}: {exc} — the pin moved, so this "
                         f"check is no longer checking anything") from exc

    pins = {}
    for key in ("PBS_TAG", "PBS_ASSET", "URUNTIME_TAG", "URUNTIME_ASSET"):
        value = _pinned(src, key)
        if not value:
            raise SystemExit(
                f"could not read {key} from {path} — the pin moved, so this "
                "check is no longer checking anything"
            )
        pins[key] = value
    # the script relies on the shell expanding this, so expand it here too
    pins["PBS_ASSET"] = pins["PBS_ASSET"].replace("${PBS_TAG}", pins["PBS_TAG"])
    return pins


def main() -> int:
    failures: list[str] = []
    checked = 0

    print(f"wireproxy {provision.VERSION} — every platform asset:")
    for asset in wireproxy_assets():
        url = f"{provision._BASE}/v{provision.VERSION}/{asset}"
        status, detail = head(url)
        ok = status in (200, 302)
        checked += 1
        if not ok:
            failures.append(f"wireproxy {asset}: {status} {detail}")
        print(f"  {'ok  ' if ok else 'FAIL'} {asset:34} {status} {detail}")

    pins = build_pins()
    # HEAD the URLs the build itself uses. Resolving these through the API would
    # need the very quota the build stopped needing, and would check a name
    # rather than the URL - and the URL is the part that can rot.
    for label, repo, tag, asset in (
        ("python-build-standalone", "astral-sh/python-build-standalone",
         pins["PBS_TAG"], pins["PBS_ASSET"]),
        ("uruntime", "VHSgunzo/uruntime",
         pins["URUNTIME_TAG"], pins["URUNTIME_ASSET"]),
    ):
        url = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
        print(f"\n{label} {tag} — the pinned artifact:")
        status, detail = head(url)
        checked += 1
        ok = status in (200, 302)
        if not ok:
            failures.append(f"{label} {asset}: {status} {detail}")
        print(f"  {'ok  ' if ok else 'FAIL'} {asset}  {status} {detail}")

    print(f"\n{checked} checked, {len(failures)} failed")
    for f in failures:
        print(f"  !! {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
