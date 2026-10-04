import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ["GH_TOKEN"]
API = "https://api.github.com"
REPO = "chethan62/warp"
TAG = "v1.0.0"
ASSET = "dist/warp-1.0.0-x86_64.AppImage"

BODY = """A Cloudflare WARP client in the shape of the 1.1.1.1 app: one page, one big
toggle, your egress IP. Cross-platform, **no root**, stdlib Python only.

### Download

`warp-1.0.0-x86_64.AppImage` — self-contained, no system Python required.

```sh
chmod +x warp-1.0.0-x86_64.AppImage
./warp-1.0.0-x86_64.AppImage          # UI on 127.0.0.1:8787
./warp-1.0.0-x86_64.AppImage status   # every subcommand passes through
```

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

# upload the asset
with open(ASSET, "rb") as fh:
    blob = fh.read()
name = os.path.basename(ASSET)
status, out = req(f"{rel['upload_url'].split('{')[0]}?name={name}",
                  blob, method="POST", ctype="application/octet-stream")
print("asset upload:", status, (out.get("state"), out.get("size")) if isinstance(out, dict) else out)

# verify by reading back what GitHub actually has
status, rel2 = req(f"{API}/repos/{REPO}/releases/tags/{TAG}")
print("\n--- read back ---")
print("tag:", rel2.get("tag_name"), "| published:", rel2.get("published_at"))
for a in rel2.get("assets", []):
    print(f"  asset: {a['name']}  {a['size']:,} bytes  state={a['state']}  downloads={a['download_count']}")
    print("  url:", a["browser_download_url"])
print("local file:", os.path.getsize(ASSET), "bytes")
