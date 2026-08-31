# Showcase security & DoS hardening — summary for the supervisor

The concern raised: *"anything on the internet will have something happen to it."*
Correct — so the showcase is built so the exposed surface is small and every
layer that a flood or abuse attempt hits has a defence. Here is exactly what
protects the live demo, from the outside in.

## 1. The front door is a Cloudflare tunnel, not an open port

The link the supervisor uses at the event is a **Cloudflare quick tunnel**
(`https://…trycloudflare.com`). This matters for DoS specifically:

- The origin VM **does not expose the app on a public port** on this path.
  `cloudflared` makes an **outbound** connection to Cloudflare and reaches the
  backend over **loopback** (`127.0.0.1`). There is no inbound port for an
  attacker to point a flood at.
- **Cloudflare's edge absorbs volumetric attacks** (SYN floods, L3/L4 DDoS,
  bandwidth floods) before they ever reach the VM. That is Cloudflare's core
  business and is far beyond what a single VM could withstand.
- Cloudflare also terminates TLS and serves the app over HTTPS, so the traffic
  the audience sees is encrypted.

So on the primary path, the honest answer is: **the origin is not directly
reachable, and Cloudflare stands between the internet and the app.**

## 2. Application-layer protection (both paths)

Inside the backend (`app/main.py`), active whenever a password is set:

- **Per-IP login lockout** — 5 wrong passwords from an IP → that IP is blocked
  (HTTP 403) until the backend restarts. Stops password brute-forcing.
- **Per-IP rate limit** — more than **40 requests / 10 s** from one IP → HTTP
  429. Stops request-hammering / scraping.
- Both use the **real client IP** (`CF-Connecting-IP`, then `X-Forwarded-For`),
  so they work correctly behind the tunnel — not just the tunnel's own address.

These were verified live: 5 wrong logins → locked; a 50-request burst → 40×200
followed by 10×429; a different IP is unaffected.

## 3. Network-layer hardening on the backup direct-IP path (`iptables`)

If the direct `http://IP:PORT` link is used as a fallback, the VM adds firewall
rules (`scripts/showcase-remote.sh`, step 4b) that **do not touch the loopback
tunnel path**:

- **Concurrent-connection cap**: > 40 simultaneous connections from one source
  IP are rejected (defeats socket-exhaustion / slowloris-style floods).
- **New-connection rate cap**: > 60 new connections/min per source IP are
  dropped, with a burst of 40 allowed so a normal page's parallel asset loads
  still pass.
- **Invalid-packet drop**: packets with a bogus TCP/conntrack state are dropped.
- **ICMP echo (ping) rate limit**: 5/s, burst 10.

These are best-effort (skipped cleanly if the instance lacks `iptables`), and
scoped to the backend port only — SSH and the tunnel are untouched.

## 4. What is deliberately NOT claimed

- Instance-level `iptables` rules **cannot** protect the tunnel path (that
  traffic is loopback) — and they don't need to, because Cloudflare fronts it.
- A single VM cannot survive a large volumetric DDoS on its own; that protection
  comes from **using the Cloudflare tunnel as the event link**, which is the
  documented, recommended path (`docs/RUNBOOK-SHOWCASE.md` §4).

## One-line answer for the supervisor

> The event link runs through a Cloudflare tunnel, so the VM has no open port for
> an attacker to flood and Cloudflare absorbs volumetric attacks; on top of that
> the app itself locks out IPs after repeated bad logins and rate-limits requests
> per IP, and the backup direct-IP port additionally has connection-flood and
> rate-limit firewall rules.
