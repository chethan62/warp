"""Entry point — `python3 -m warp [ui|up|down|status|selftest]` on any platform."""

from __future__ import annotations

import argparse
import json
import sys

from . import dns, dnsproxy, webui, x25519
from .tunnel import DEFAULT_SOCKS_PORT, Tunnel


def main(argv=None) -> int:
    # Shared flags: defined as a parent so they work before OR after the
    # subcommand (`warp ui --no-browser`, not just `warp --no-browser ui`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=8787, help="UI port (default 8787)")
    common.add_argument("--socks-port", type=int, default=DEFAULT_SOCKS_PORT,
                        help=f"SOCKS5 port (default {DEFAULT_SOCKS_PORT})")
    common.add_argument("--no-browser", action="store_true", help="don't open a UI window")
    common.add_argument("--tab", action="store_true",
                        help="open the UI in a browser tab instead of an app window")

    parser = argparse.ArgumentParser(
        prog="warp", description="Cloudflare WARP client (userspace, no root required)",
        parents=[common])
    sub = parser.add_subparsers(dest="cmd")
    for name, text in (("ui", "serve the UI (default)"),
                       ("up", "connect"),
                       ("down", "disconnect"),
                       ("status", "print status as JSON"),
                       ("selftest", "run the X25519 vectors"),
                       ("route", "route this computer: warp route on|off"),
                       ("dns", "show the DNS plan, or 'dns serve' to bridge DoH over the tunnel")):
        sub.add_parser(name, parents=[common], help=text)
    sub.choices["route"].add_argument("action", nargs="?", default="on", choices=["on", "off"])
    sub.choices["dns"].add_argument("action", nargs="?", default="show", choices=["show", "serve"])
    sub.choices["dns"].add_argument("--listen", default="127.0.0.1:5053",
                                    help="forwarder bind address (default 127.0.0.1:5053)")
    args = parser.parse_args(argv)

    cmd = args.cmd or "ui"
    tunnel = Tunnel(socks_port=args.socks_port)

    if cmd == "selftest":
        x25519.selftest()
        return 0
    if cmd == "status":
        print(json.dumps(tunnel.status(), indent=2))
        return 0
    if cmd == "up":
        result = tunnel.start()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if cmd == "down":
        print(json.dumps(tunnel.stop(), indent=2))
        return 0
    if cmd == "dns":
        if args.action == "serve":
            host, _, port = args.listen.partition(":")
            try:
                dnsproxy.serve(listen=(host, int(port)), proxy=("127.0.0.1", args.socks_port))
            except RuntimeError as exc:
                print(f"warp dns: {exc}", file=sys.stderr)
                return 1
            return 0
        print(dns.render(dns.detect()))
        return 0
    if cmd == "route":
        result = tunnel.route_system(args.action == "on")
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    webui.serve(port=args.port, open_browser=not args.no_browser,
                prefer_tab=args.tab)
    return 0


if __name__ == "__main__":
    sys.exit(main())
