---
type: doc
status: active
tags: [cadre, hostnames, firm-dash, firm-chat, hosts-file, reverse-proxy, setup, runbook]
relatedTo: [cadre, chat-rail-setup, tailscale-setup]
---

# Friendly Hostnames — firm.dash + firm.chat on any OS

Turn `http://127.0.0.1:8484` and `http://127.0.0.1:7787` into **http://firm.dash** and
**http://firm.chat** on the machine that runs cadre.

**The fastest path: hand this file to Claude Code on that machine** — *"read
docs/HOSTNAMES-SETUP.md and set up my cadre hostnames"*. Everything below is written so Claude
can detect the OS and execute it end to end; it also works as a manual walkthrough.

## How it works (two levels)

1. **Names (required)** — a hosts-file entry maps the names to `127.0.0.1`. You get
   `http://firm.dash:8484` and `http://firm.chat:7787`. Ports still show.
2. **Portless (optional, recommended)** — a reverse proxy on port 80 routes by hostname, so the
   URLs lose their ports: `http://firm.dash`, `http://firm.chat`.

Phones can't read hosts files — for phone access see `docs/TAILSCALE-SETUP.md`.

## Level 1 — hosts entries per OS

Append one line to the hosts file (needs admin/root):

```
127.0.0.1 firm.dash firm.chat
```

| OS | Hosts file | Edit command |
|---|---|---|
| Linux / macOS | `/etc/hosts` | `echo "127.0.0.1 firm.dash firm.chat" \| sudo tee -a /etc/hosts` |
| Windows (native) | `C:\Windows\System32\drivers\etc\hosts` | Run terminal **as Administrator**: `Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "127.0.0.1 firm.dash firm.chat"` |
| **WSL2** (cadre inside WSL, browser on Windows) | BOTH files — see below | below |

### The WSL2 case (read this if cadre runs in WSL)

Windows can't reach WSL services through `127.0.0.1` on all ports, and the WSL VM's IP **changes
every reboot**. The pattern:

1. Inside WSL: `IP=$(hostname -I | awk '{print $1}')` — the current VM address.
2. Windows hosts file gets `$IP firm.dash firm.chat` (edit
   `/mnt/c/Windows/System32/drivers/etc/hosts` from WSL — wrap the lines in marker comments so a
   re-run can replace them cleanly).
3. WSL's own `/etc/hosts` gets the same line (so tools inside WSL resolve the names too).
4. Put steps 1–2 in a small script and call it from `.bashrc` (fast-path exit when the IP is
   unchanged) — the mapping heals itself after every reboot.

One trap for script authors: under `set -euo pipefail`, a probe like
`grep name hosts | awk …` inside `$( )` **aborts the script when the name isn't there yet**.
Wrap it: `(grep … || true) | awk …`.

## Level 2 — portless via a reverse proxy on :80

Any proxy works. **Caddy is the least-friction choice** (one binary, every OS, auto-starts):

```
# Caddyfile
http://firm.dash {
    reverse_proxy 127.0.0.1:8484
}
http://firm.chat {
    reverse_proxy 127.0.0.1:7787
}
```

`caddy start --config Caddyfile` (or install it as a service: `caddy add-package` docs). Apache
equivalent (name-based vhosts + `ProxyPass`) and nginx (`server_name` + `proxy_pass`) both work
identically — route by hostname to the two local ports. Keep the proxy bound to localhost (or
your tailscale interface — see the tailscale doc) — never a public interface.

Note for the chat rail behind a proxy: tell the dashboard's 💬 link the pretty URL —
`cadre` config key `link_url` via the dashboard seam or directly:
`~/.cadre/rail/chat/config.json` → `"link_url": "http://firm.chat"`.

## Verify

```sh
# names resolve
ping -c1 firm.dash        # → 127.0.0.1 (or the WSL IP on Windows)
# services answer through the names
curl -s -o /dev/null -w "%{http_code}\n" http://firm.dash:8484/   # 200 (or :80 with a proxy)
curl -s -o /dev/null -w "%{http_code}\n" http://firm.chat:7787/   # 200
```

Browser: `http://firm.dash` lands on the hub, `http://firm.chat` on the Co-Board chat. Done.
