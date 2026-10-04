# Feasibility: full system-wide (TUN) tunnel from userspace WARP, no root

**Verdict: No.** On Linux a full TUN tunnel needs **CAP_NET_ADMIN** — a privilege
the app cannot grant itself. An installer that never elevates can only *print* the
one-time grant, never perform it; with no grant, host-wide capture is impossible on
every OS. The app's existing ceiling — SOCKS5 + per-user system proxy — is the honest
floor. `docs/limits-and-options.md` reaches this conclusion and is substantively
correct; the README overstates it as "root" (see *Corrections*).

## 1. Exactly what privilege is required

**One capability: `CAP_NET_ADMIN`.** It covers every named step:
- **Create the TUN device** (ioctl `TUNSETIFF`). Kernel: "CAP_NET_ADMIN is required for
  creating network devices" [1]; `drivers/net/tun.c` gates it on `capable(CAP_NET_ADMIN)`
  and `ns_capable(net->user_ns, CAP_NET_ADMIN)` [2].
- **Assign its address / set MTU** ("interface configuration") and **install routes**
  ("modify routing tables") — both listed under `CAP_NET_ADMIN` in capabilities(7) [3].
- **The routing-loop fix** also fits inside it: mark-based routing uses `SO_MARK`, which
  needs `CAP_NET_ADMIN` (or `CAP_NET_RAW` since 5.17) [4]; netfilter/TPROXY rules need
  `CAP_NET_ADMIN` too [5].

**Can it be granted so a non-root user does a full tunnel? Yes — one-time, two ways:**
- **File capability:** `sudo setcap cap_net_admin+ep <binary>` [3][6][7]. Stored in the
  file's `security.capability` xattr; applies to **anyone who can execute the file**;
  **must be re-applied after every upgrade** (xattr dies with the old inode) [3][7].
- **systemd:** `User=<you>` + `AmbientCapabilities=CAP_NET_ADMIN` +
  `CapabilityBoundingSet=CAP_NET_ADMIN` [8][7]. Preferred: the bounding set drops every
  other capability, so a compromise cannot regain them [7].

**What still fails even with CAP_NET_ADMIN:**
- **Write `/etc/resolv.conf`** — a DAC check, not a network one; `CAP_NET_ADMIN` does not
  bypass it. Needs root or `CAP_DAC_OVERRIDE` [3].
- **Change systemd-resolved** — its D-Bus actions (`SetLinkDNS`, `set-dns-servers`)
  require polkit **`auth_admin`** [9]. `CAP_NET_ADMIN` is irrelevant here.
- **Bind a local resolver to port 53** — needs `CAP_NET_BIND_SERVICE` [3].
- **tproxy transparent sockets** — need `CAP_NET_RAW` *in addition* (both are listed for
  "bind to any address for transparent proxying") [3], plus the nft/iptables `TPROXY`
  rules [5].
- **macOS / Windows** — root / Administrator; no capability mechanism exists [7][6].

So `CAP_NET_ADMIN` buys **packet** capture, not **DNS config**.

## 2. Can a "no root at all, ever" installer deliver a full tunnel?

**No.** Every path ends in one privileged operation the installer cannot do unprivileged:
granting a capability (`setcap` itself needs `CAP_SETFCAP` [3]) or installing a root-owned
systemd unit. On macOS/Windows it is Administrator, full stop [7].

**The one-time grant (its whole cost):**
```
sudo setcap cap_net_admin+ep <wireproxy-or-tun-helper>
# or, root-installed unit:  User=%i
#   AmbientCapabilities=CAP_NET_ADMIN
#   CapabilityBoundingSet=CAP_NET_ADMIN
```
An unprivileged installer can only *print* these — exactly what `warp dns` already does
for the resolver step [10].

**Fallback when the grant is absent:** today's behaviour — SOCKS5 listener + per-user
system proxy (proxy-aware apps only), `socks5h://` for individual CLI tools, browser DoH.
Raw-socket apps and all UDP stay outside. Nothing widens this without privilege.

## 3. DNS interception without root

- **UDP/53 cannot cross SOCKS.** SOCKS5 has `UDP ASSOCIATE`, but wireproxy's usable path
  is TCP `CONNECT` [10]; any app opening its own UDP:53 socket ignores the proxy entirely.
- **Browser DoH** (`https://cloudflare-dns.com/dns-query`) reroutes that one browser only.
- **`socks5h://` / `--socks5-hostname`** hands the hostname to the proxy so *that* lookup
  resolves in-tunnel — one CLI tool per invocation, TCP only.
- **LD_PRELOAD shim** (proxychains-ng) hooks `getaddrinfo`/`connect` in dynamically linked
  apps only; static/Go binaries and **all UDP** escape it [15].
- **tproxy is *not* a no-root option** — it needs `CAP_NET_ADMIN`/`CAP_NET_RAW` plus
  `TPROXY` rules and `IP_TRANSPARENT` on the listener [5][3].
- **Whole-system DNS** = TUN + a resolver bound to :53 (`CAP_NET_BIND_SERVICE`), or root
  edits to `/etc/resolv.conf` / `resolvectl` (polkit) [3][9].

**Which actually reroute command-line tools?** Only `socks5h` (per tool, TCP) and the
LD_PRELOAD shim (per dynamically linked tool, TCP). **Neither catches UDP/53**, so `dig`
and anything doing its own UDP resolution still leak. Real WARP sidesteps this by running
as root: it binds a local DNS proxy on `127.0.2.2:53` / `127.0.2.3:53` and rewrites the OS
resolver [11].

## 4. The routing-loop problem

The host sends traffic **into** a TUN it also **terminates**. The tunnel's outer packets
are IP packets too: with a default route pointing at the TUN, the encrypted WireGuard UDP
to `engage.cloudflareclient.com:2408` is itself routed back into the TUN → re-encrypted →
loop, and the handshake never leaves the box. mitmproxy documents the same for its
userspace WireGuard server: it cannot proxy the traffic of the host it runs on [12].

**How real clients avoid it:**
- **Exclude the endpoint** — a host route via the physical gateway
  (`ip route add <endpoint>/32 via <gw> dev eth0`) [13].
- **Mark the tunnel's own packets, policy-route the unmarked ones** — `wg-quick` does
  `wg set <if> fwmark N`, `ip rule add not fwmark N table N`,
  `ip route add default dev <if> table N`, `ip rule add table main suppress_prefixlength 0`,
  plus CONNMARK save/restore so replies keep the mark [14][13]. `SO_MARK` needs
  `CAP_NET_ADMIN` [4].
- **Namespace split** — physical NIC in one netns, tunnel in another (the WireGuard socket
  keeps its birth-namespace) [13]. WARP's real client does the equivalent: a virtual
  interface + a dedicated routing table (Linux `table 65743`) + a firewall blocking
  tunnel-destined traffic on every other interface [11].
- **A pure-userspace client (wireproxy over SOCKS) dodges it** by never creating a TUN —
  only the host's own outbound connections are in scope and the app decides per-connection.
  Wrap wireproxy in a TUN (tun2socks style) and the loop must be re-solved as above.

## Corrections to the two repo docs

1. **README overstates "root" — flag.** README lines 212–214 ("would need a TUN interface,
   which does require root") and 227–228 ("Creating a TUN interface needs root") contradict
   both `docs/limits-and-options.md` §1 and the README's own line 70 ("`wg-quick up` needs
   `CAP_NET_ADMIN`"). On Linux it needs **`CAP_NET_ADMIN`**, a grantable capability — not
   root. ("root/Administrator" is right only for macOS/Windows.) Suggested wording:
   "needs CAP_NET_ADMIN on Linux; Administrator on macOS and Windows".
2. **limits-and-options.md Finding 1 is true but incomplete.** "One capability covers
   tun + address + MTU + routes" is correct, but it silently omits that DNS *configuration*
   is not covered: `/etc/resolv.conf` (root / `CAP_DAC_OVERRIDE`) and systemd-resolved
   (polkit `auth_admin` [9]) still need privilege. Whole-system *packet* coverage ≠
   whole-system DNS wiring. Add the caveat.
3. **limits-and-options.md "What the research does not support" — wording gap, not error.**
   "TUN mode explicitly requires root or CAP_NET_ADMIN" holds for the **host** network
   namespace. Unprivileged user namespaces let any user create a netns and a TUN *inside
   it* for free (`ns_capable(net->user_ns, CAP_NET_ADMIN)` [2]; rootless containers rely on
   this [16]). That captures only processes in that namespace, so the conclusion stands —
   but the sentence should say "in the host namespace".
4. **limits-and-options.md Finding 4 (resolved does DoT, not DoH) is CORRECT and current.**
   Re-checked 2026-10-04: systemd issue #8639 is still open (#31537, #43065 unmerged) [17],
   and `resolved.conf` from v255–v258 and `main` exposes `DNSOverTLS=` but **no** DoH option
   [18]. No change needed — the doc's cited RFE is still an RFE.

## Could NOT verify / caveats
- **No live full-tunnel run.** This box has no `CAP_NET_ADMIN` grant, so §1–2 are verified
  from kernel/man sources and upstream docs, not from an end-to-end connection. The repo's
  limits doc says the same.
- **File-capability behaviour** under `no_new_privs`, nosuid mounts, SELinux/AppArmor and
  inside containers is inferred from general capability semantics [3]; not tested here.
- **tun2socks loop handling** is stated at the level of "it must bind/mark its upstream
  socket"; the specific flag it uses was not verified against its source.

## Sources
1. Linux kernel, `Documentation/networking/tuntap.rst` — https://docs.kernel.org/networking/tuntap.html
2. Linux kernel, `drivers/net/tun.c` (`tun_set_iff`, `tun_not_capable`) — https://raw.githubusercontent.com/torvalds/linux/master/drivers/net/tun.c
3. capabilities(7) — https://man7.org/linux/man-pages/man7/capabilities.7.html
4. socket(7) — `SO_MARK` — https://man7.org/linux/man-pages/man7/socket.7.html
5. Linux kernel, Transparent proxy support (TPROXY) — https://docs.kernel.org/networking/tproxy.html
6. OTel OBI security (`setcap cap_net_admin,cap_net_raw`) — https://opentelemetry.io/docs/zero-code/obi/security
7. Nebula — Running as non-root (CAP_NET_ADMIN / setcap caveats) — https://nebula.defined.net/docs/guides/running-nebula-as-non-root
8. systemd.exec(5) — `AmbientCapabilities=`, `CapabilityBoundingSet=` — https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.exec.xml
9. systemd, `org.freedesktop.resolve1.policy` (set-dns-servers = `auth_admin`) — https://raw.githubusercontent.com/systemd/systemd/main/src/resolve/org.freedesktop.resolve1.policy
10. repo: `README.md` (DNS section), `docs/limits-and-options.md` — https://github.com/chethan62/warp
11. Cloudflare One Client architecture (virtual iface, table 65743, DNS proxy :53) — https://developers.cloudflare.com/cloudflare-one/team-and-resources/devices/cloudflare-one-client/configure/route-traffic/client-architecture/
12. mitmproxy proxy modes (userspace WireGuard cannot proxy its own host) — https://docs.mitmproxy.org/stable/concepts/modes
13. WireGuard — Routing & Network Namespaces — https://www.wireguard.com/netns/
14. wireguard-tools, `src/wg-quick/linux.bash` (`add_default`) — https://git.zx2c4.com/wireguard-tools/tree/src/wg-quick/linux.bash
15. proxychains-ng (LD_PRELOAD limits) — https://github.com/rofl0r/proxychains-ng
16. slirp4netns — unprivileged network namespaces — https://github.com/rootless-containers/slirp4netns
17. systemd issue #8639 — DNS-over-HTTPS RFE (open) — https://github.com/systemd/systemd/issues/8639
18. systemd `man/resolved.conf.xml` v255–main (`DNSOverTLS=` only) — https://raw.githubusercontent.com/systemd/systemd/main/man/resolved.conf.xml
