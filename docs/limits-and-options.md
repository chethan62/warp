# Limits, and the options for lifting them

warp is a SOCKS5 proxy with a system-proxy setting, not a TUN device. That
choice is what makes it work with no root, and it is also the source of both
limits below. This records what was actually found when the options for lifting
them were researched, so the decision is not re-litigated from memory.

Every claim is cited; the numbering is generated from a source ledger, not
typed. **External facts checked 2026-10-04** — capability defaults, resolver
features and upstream projects all move, so re-check the sources before relying
on a detail.

## The two limits

**Coverage.** Only proxy-aware applications follow a system proxy. Anything that
opens raw sockets ignores it.

**DNS.** UDP/53 cannot cross a SOCKS proxy, because SOCKS carries TCP streams,
not datagrams.

> Fuller, fully-cited write-up of this section: [`no-root-tun.md`](no-root-tun.md)
> — capability grants, the DNS-config caveat, and the TUN routing loop.

## Finding 1 — "whole system" needs CAP_NET_ADMIN, not root

On Linux the privileged work happens to be exactly what one capability covers:
creating the tun device, configuring its address and MTU, and installing routes
are all `CAP_NET_ADMIN`.[1][7] So a process running as an ordinary user can drive
a **full** tunnel if it is granted that single capability.[1][14] There are two
one-time ways to grant it, both documented by a project that ships this in
production.[1] The systemd route sets `User=` alongside
`AmbientCapabilities=CAP_NET_ADMIN` and `CapabilityBoundingSet=CAP_NET_ADMIN`,
and it is the recommended one because the bounding set drops every other
capability so a compromise cannot regain them.[1] The alternative is a file
capability on the binary — `sudo setcap cap_net_admin+ep <binary>` — which is the
same pattern independent projects use.[1][12][13]

The caveats are the interesting part, and they are the sources' warnings rather
than opinion. File capabilities **apply to anyone who can execute the
binary**, so the capability should be scoped to a dedicated user or the exec
permission restricted.[1] The capability lives in the file's extended
attributes, so it **must be re-applied after every upgrade or replace**.[1] And
this is **Linux-only**: on macOS and Windows creating the tun device requires
root or Administrator, with no equivalent mechanism.[1] You can confirm the grant
with `grep CapEff /proc/<pid>/status`.[1]

So the honest framing is not "rootless versus root". A one-time, narrowly scoped
grant buys whole-system coverage; running as root buys the same coverage with far
more blast radius.[1]

## Finding 2 — even with a TUN, there is a routing loop to solve

mitmproxy runs a userspace WireGuard server with no administrative
privileges,[2] but documents that it **cannot proxy the traffic of the host it
runs on**, because the outgoing WireGuard packets would be routed back into the
tunnel.[2] So any whole-system design has to exclude its own endpoint from the
routes.[2] The DNS bridge here already has this problem in miniature, since the
DoH request must not be resolved through the resolver it is serving.

## Finding 3 — rootless widening is real but bounded

The rootless way to widen coverage is an LD_PRELOAD shim that hooks socket calls
in dynamically linked programs.[3] Its documented limits are firm: it does not
work on statically linked binaries, which includes many Go programs, and it does
not intercept raw sockets or UDP by default, so ICMP/ping and UDP DNS escape
it.[4] That yields "most apps, never UDP" — useful, but not the goal.[3][4]

## Finding 4 — for DNS the resolver does DoT, not DoH

systemd-resolved provides DNSSEC and DNS over TLS,[5][15] but **not** DoH, which
is still an open feature request.[6] The distinction matters here: DoT is reached
by the resolver itself, outside any SOCKS proxy, so it encrypts DNS without
putting it *inside* the tunnel.[5] The DoH bridge is what moves DNS into the
tunnel, precisely because DoH is HTTP and HTTP crosses SOCKS.[2]

## Finding 5 — the stack choice is already right

An independent project, wiretap, has the same architecture: a transparent,
VPN-like proxy tunnelling over WireGuard that requires no special privileges and
exposes SOCKS5.[10] The alternatives are the privileged class. tun2socks drives a
gVisor TCP/IP stack behind a TUN device,[11] and dae does eBPF-based
interception.[9] Swapping wireproxy for either would trade the rootless property
for nothing needed here.[10][11]

## Options, ranked

1. **Make CAP_NET_ADMIN an opt-in "everything" mode.** It is the only route
   to whole-system coverage that does not need root, and it is a one-time
   grant.[1] It would ship the way `warp dns` already ships its root step: print
   the exact `setcap`/systemd lines and never run them.[1][12] Not built —
   exercising it needs a real TUN device, which needs the very privilege this
   box does not have, so it could only be shipped untested here.
2. **Cover the browser explicitly**, at zero privilege — launching it against
   the local SOCKS5 listener completes "browse and download" immediately.[10]
3. **Keep the SOCKS path as the default**, since it is the only mode that works
   with no grant on every platform.[10]
4. **Offer the LD_PRELOAD shim** as an optional widening step for CLI tools,
   labelled with its real limits: static and Go binaries, and all UDP, still
   escape.[3][4]
5. **Keep the DoH bridge for DNS**, and offer DoT separately for encryption
   without tunnelling, saying plainly which of the two it is.[5][6][15]

## What the research does not support

No source found shows every app's traffic being captured transparently with *no*
privilege on Linux. The kernel's transparent proxy support is an admin-level
facility,[8] TUN mode explicitly requires root or CAP_NET_ADMIN,[2] and eBPF
interception is driven by dedicated privileged tooling.[9] Anything advertised as
system-wide without privilege is doing per-app proxying under that name.[3][4]

## Sources

[1] https://nebula.defined.net/docs/guides/running-nebula-as-non-root — Running Nebula as a non-root user (CAP_NET_ADMIN / setcap)
[2] https://docs.mitmproxy.org/stable/concepts/modes — mitmproxy - Proxy Modes (WireGuard, TUN, Local Capture)
[3] https://github.com/rofl0r/proxychains-ng — proxychains-ng - LD_PRELOAD, dynamically linked programs only
[4] https://bigmike.help/en/devops/proxychains-and-proxychains-ng-a-tool-for-anonymizing-traffic-and-network — ProxyChains - fails on static binaries, no UDP/raw sockets
[5] https://wiki.archlinux.org/title/Systemd-resolved — systemd-resolved - DNSSEC and DNS over TLS
[6] https://github.com/systemd/systemd/issues/8639 — systemd RFE 8639 - Add DNS-over-HTTPS support
[7] https://man7.org/linux/man-pages/man7/capabilities.7.html — capabilities(7) - Linux man page
[8] https://docs.kernel.org/networking/tproxy.html — Linux kernel - Transparent proxy support (TPROXY)
[9] https://github.com/daeuniverse/dae — dae - eBPF-based transparent proxy
[10] https://github.com/sandialabs/wiretap — wiretap - WireGuard proxy, no special privileges
[11] https://github.com/xjasonlyu/tun2socks — tun2socks - gVisor TCP/IP stack
[12] https://github.com/cloudflare/boringtun — boringtun - userspace WireGuard, setcap cap_net_admin
[13] https://opentelemetry.io/docs/zero-code/obi/security — OTel OBI - setcap cap_net_admin,cap_net_raw
[14] https://unix.stackexchange.com/questions/743893/allowing-a-non-root-user-to-create-tun-tap-interfaces — Allowing a non-root user to create TUN/TAP interfaces
[15] https://fedoramagazine.org/enabling-system-wide-dns-over-tls — Fedora Magazine - system-wide DNS over TLS
