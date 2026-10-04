import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ["GH_TOKEN"]
API = "https://api.github.com"
REPO = "chethan62/warp"
_src = open(os.path.join(os.path.dirname(__file__), "..", "warp", "__init__.py")).read()
VERSION = _src.split('__version__ = "')[1].split('"')[0]
TAG = f"v{VERSION}"
# Everything the release carries. The stable-named tarball is what the README's
# one-liner fetches, so it must be on the latest release or that command 404s.
ARTIFACTS = [
    f"dist/warp-{VERSION}-x86_64.AppImage",
    f"dist/warp-{VERSION}-linux-x86_64.tar.gz",
    "dist/warp-linux-x86_64.tar.gz",
]

BODY = f"""A Cloudflare WARP client in the shape of the 1.1.1.1 app: one page, one big
toggle, your egress IP. Cross-platform, **no root**, stdlib Python only.

### Linux

```sh
curl -fsSL https://github.com/chethan62/warp/releases/latest/download/warp-linux-x86_64.tar.gz \
  | tar -xz -C ~/.local --strip-components=1
```

Self-contained (bundled Python), no root, and **no FUSE** — it is a plain tree,
not an image that has to be mounted. `warp` lands in `~/.local/bin`.

A single-file AppImage is also attached for anyone who prefers it, or has FUSE:
`warp-{VERSION}-x86_64.AppImage`.

### Verified from this exact image

- `selftest` — X25519 against the RFC 7748 vectors
- `up` -> `status` reports `connected: true`, `warp: on`, WARP egress IP
- `route on` / `route off` — KDE system proxy set and cleared
- the UI it serves renders the state, the routing toggle and the details

### Honest limits

- **Not a TUN device.** It is a SOCKS5 proxy plus a system-proxy setting, so
  proxy-aware apps follow it; apps that open raw sockets do not. A TUN interface
  would need root.
- **DNS is not tunnelled by default.** UDP/53 cannot cross a SOCKS proxy. There
  is a `warp dns serve` bridge (DoH over the tunnel) and `warp dns` prints the
  exact root command for your resolver — but the system-wide step is yours to run.
- **No Cloudflare managed challenge was defeated**, and nothing here claims to.
- `wireproxy` is downloaded from its own releases on first connect.

MIT. Not affiliated with Cloudflare, Inc.
"""


def req(url, data=None, method=None, ctype="application/json", raw=False):
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "token " + TOKEN)
    r.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


# create the release (this also creates the tag)
status, rel = req(f"{API}/repos/{REPO}/releases", json.dumps({
    "tag_name": TAG,
    "name": f"warp {TAG}",
    "body": BODY,
    "draft": False,
    "prerelease": False,
}).encode())
if status not in (200, 201):
    print("release create failed:", status, rel)
    sys.exit(1)

print("release:", rel["html_url"], "| id", rel["id"])

# upload the assets
for path in ARTIFACTS:
    with open(path, "rb") as fh:
        blob = fh.read()
    name = os.path.basename(path)
    status, out = req(f"{rel['upload_url'].split('{')[0]}?name={name}",
                      blob, method="POST", ctype="application/octet-stream")
    ok = isinstance(out, dict) and out.get("state") == "uploaded"
    print(f"  upload {name}: {status} {'ok' if ok else out}")

# verify by reading back what GitHub actually has
status, rel2 = req(f"{API}/repos/{REPO}/releases/tags/{TAG}")
print("\n--- read back ---")
print("tag:", rel2.get("tag_name"), "| published:", rel2.get("published_at"))
for a in rel2.get("assets", []):
    print(f"  asset: {a['name']}  {a['size']:,} bytes  state={a['state']}  downloads={a['download_count']}")
    print("  url:", a["browser_download_url"])
for path in ARTIFACTS:
    print(f"  local {os.path.basename(path)}: {os.path.getsize(path):,} bytes")
