---
type: doc
status: active
tags: [cadre, tailscale, mobile, phone, split-dns, dnsmasq, chat-rail, setup, runbook]
relatedTo: [cadre, chat-rail-setup, hostnames-setup]
---

# Tailscale — the Co-Board chat on every device you own

The chat rail binds localhost by default. Tailscale is the supported way to reach it from your
phone, tablet, or laptop: a private WireGuard mesh where **device membership is the auth** — no
tokens, no port-forwarding, nothing public. This doc goes from zero to "the boardroom is an app
icon on my phone."

**Fastest path:** hand this file to Claude Code on the cadre machine — *"read
docs/TAILSCALE-SETUP.md and get my phone connected"* — and do the phone/console steps it can't
do for you when it asks.

## 1. Install tailscale on the cadre machine

| OS | Install | Then |
|---|---|---|
| Linux / **WSL2** | `curl -fsSL https://tailscale.com/install.sh \| sh` | `sudo tailscale up` (prints a login URL — open it, authenticate) |
| macOS | App Store "Tailscale", or `brew install tailscale` | log in from the menu-bar app |
| Windows (native cadre) | https://tailscale.com/download/windows | log in from the tray app |

WSL2 note: install **inside WSL** (the machine that runs cadre), not just on Windows — the
daemon must own a tailscale interface in the same OS as the rail.

## 2. Install tailscale on every device that should reach the board

| Device | Install | Login |
|---|---|---|
| iPhone / iPad | App Store → Tailscale | same account/tailnet, toggle VPN on |
| Android | Play Store → Tailscale | same |
| macOS / Windows / Linux laptop | as in step 1 | same |

Everything logged into your tailnet can now reach the cadre machine. **That is the security
model: tailnet membership = Board membership.** Don't share the tailnet; use a separate one for
anything else.

## 3. Open the chat rail to the tailnet

On the cadre machine:

```sh
cadre chat host tailscale     # rebinds the daemon to this machine's tailnet IP (100.x.y.z)
cadre chat status             # shows the URL, e.g. http://100.x.y.z:7787
```

`host tailscale` auto-detects the interface (`tailscale ip -4`, with a CGNAT-range fallback).
It binds THAT address specifically — never `0.0.0.0`. Restart `cadre chat serve` (or it bounces
the systemd service itself). From any tailnet device: open the printed URL. **This step alone is
a complete phone setup** — the rest of this doc is naming polish.

Running a reverse proxy on :80 instead (see `docs/HOSTNAMES-SETUP.md` level 2)? Keep the daemon
on `127.0.0.1` and let the proxy listen on the tailscale interface — then the phone URL has no
port at all.

## 4. Optional: real names on the phone (`http://firm.chat`)

Phones can't read hosts files. Two options, honest trade-offs:

**A. MagicDNS machine name (zero setup)** — with MagicDNS on (admin console → DNS), every
device reaches the machine as `http://<machine-name>:7787`. Not pretty, works instantly.

**B. Split DNS (the pretty names)** — run a tiny DNS answerer on the cadre machine, point the
tailnet at it for just your firm domains:

1. On the cadre machine (Linux/WSL): `sudo apt install dnsmasq`, then
   `/etc/dnsmasq.d/cadre-firm-dns.conf`:

   ```
   listen-address=<your tailscale IP>
   bind-dynamic
   no-resolv
   no-hosts
   address=/firm.chat/<your tailscale IP>
   address=/firm.dash/<your tailscale IP>
   ```

   `sudo systemctl enable --now dnsmasq`. (macOS: `brew install dnsmasq`, same config.
   `no-resolv` + `no-hosts` matter: no upstream forwarding — it can never be an open resolver —
   and no accidental answers from `/etc/hosts` entries that point at unreachable local IPs.)
2. Verify from the machine: `dig +short firm.chat @<your tailscale IP>` → the tailscale IP.
3. **Admin console** (https://login.tailscale.com/admin/dns): MagicDNS **on** → *Add
   nameserver → Custom* → your tailscale IP → toggle **Restrict to domain** → `firm.chat`.
   Repeat for `firm.dash`. Only those domains route to your resolver; all other DNS is untouched.
4. Phone: toggle tailscale off/on once to pull the new DNS config → `http://firm.chat` opens
   the boardroom.

Names resolve only while the cadre machine is up — the same availability as the chat itself.

## 5. Make it an app: Add to Home Screen

The chat UI ships a PWA manifest, so:

- **iPhone**: Safari → firm.chat (or the IP URL) → Share → *Add to Home Screen*.
- **Android**: Chrome → ⋮ → *Add to Home screen* (or "Install app" when offered).

You get a standalone dark app with the Cadre icon — no browser chrome. (Over plain `http` some
platforms install it as a shortcut rather than a full PWA; identical daily experience.)

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Phone can't reach the IP URL | Tailscale toggled off on the phone, or the daemon still binds localhost — `cadre chat host` shows the current bind. |
| `host tailscale` finds no interface | `tailscaled` isn't up on the cadre machine — `sudo tailscale up`, retry. |
| `firm.chat` fails on phone, IP works | Split DNS not set (step 4.3), or the phone hasn't re-pulled DNS — toggle tailscale off/on. |
| Names died after a reboot | dnsmasq raced the tailscale interface — config uses `bind-dynamic` for exactly this; `systemctl restart dnsmasq` heals it immediately. |
| Works on desktop, not remotely | You're on the LAN assumption — tailscale doesn't care about LANs; check the phone is on the tailnet (`tailscale status` lists devices). |
