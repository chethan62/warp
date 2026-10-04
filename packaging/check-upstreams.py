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

import json
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


def pbs_pin() -> tuple[str, str]:
    """(tag, asset pattern) as the AppImage build script pins them."""
    src = (ROOT / "packaging" / "build-appimage.sh").read_text()
    tag = re.search(r'PBS_TAG="\$\{PBS_TAG:-([^}]+)\}"', src)
    match = re.search(r'PBS_MATCH="\$\{PBS_MATCH:-([^}]+)\}"', src)
    if not tag or not match:
        raise SystemExit(
            "could not read PBS_TAG/PBS_MATCH from packaging/build-appimage.sh — "
            "the pin moved, so this check is no longer checking anything"
        )
    return tag.group(1), match.group(1)


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

    tag, pattern = pbs_pin()
    print(f"\npython-build-standalone {tag} — asset matching {pattern}:")
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{tag}",
            headers=UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            release = json.load(resp)
        regex = re.compile(pattern)
        found = [a["name"] for a in release.get("assets", []) if regex.search(a["name"])]
        checked += 1
        if found:
            status, detail = head(
                f"https://github.com/astral-sh/python-build-standalone/releases/download/{tag}/{found[0]}")
            if status not in (200, 302):
                failures.append(f"pbs {found[0]}: {status} {detail}")
            print(f"  {'ok  ' if status in (200,302) else 'FAIL'} {found[0]}  {status} {detail}")
        else:
            failures.append(f"pbs {tag}: no asset matches {pattern}")
            print(f"  FAIL no asset in release {tag} matches the pattern")
    except urllib.error.HTTPError as exc:
        failures.append(f"pbs release {tag}: {exc.code} {exc.reason}")
        print(f"  FAIL release {tag}: {exc.code} {exc.reason}")

    print(f"\n{checked} checked, {len(failures)} failed")
    for f in failures:
        print(f"  !! {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
