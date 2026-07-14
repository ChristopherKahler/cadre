---
type: doc
status: active
tags: [cadre, chat, rail, boardroom, setup, runbook]
relatedTo: [cadre, chat-rail-design, slack-rail-setup]
---

# Chat Rail — Setup Runbook

The boardroom in your browser: cadre's own chat interface where every conversation is a headless
Co-Board session on your machine. Same engine as the Slack rail — no Slack app, no tokens, no
third-party surface. Localhost only.

## Setup (one command, ~20 seconds)

```
cadre chat setup        # firms root + port + permission mode
cadre chat serve        # run it in the foreground (first shakedown)
cadre chat open         # http://127.0.0.1:7787
```

When you're happy:

```
cadre chat enable       # systemd user service — always on while the machine is up
cadre chat status       # service state, config, conversation count
```

## Using it

- **New conversation** → opens a fresh `/boardroom` session at the firms root. First brief takes
  a few minutes; the ticker under the conversation shows what the session is doing live.
- **Reply** → resumes the same session (`--resume`), full context.
- **Reply while a turn is running** → steered straight into the live session via the relay
  (you'll see a "steered into the live turn" note); its answer folds your message in.
- **`@<firm-id> …`** as the first message scopes that conversation to one firm's boardroom.
- **Approve mode** → every side-effect renders as an Allow/Deny card in the conversation.
  Unanswered past the timeout = denied. Fail closed, always.

## Verbs

| Verb | Does |
|---|---|
| `cadre chat setup` | Wizard: firms root, port, permission mode |
| `cadre chat serve` | Foreground daemon (UI + API) |
| `cadre chat open` | Print/launch the UI URL |
| `cadre chat enable` / `disable` | Own systemd user unit (`cadre-rail-chat`) |
| `cadre chat status` | Service + config + conversation map |
| `cadre chat mode [approve\|skip]` | Permission posture (restarts service) |
| `cadre chat model [id\|default]` | Model override for board turns |
| `cadre chat updates [on\|off]` | In-turn proactive narration on/off |
| `cadre chat test` | Round-trip the daemon's state endpoint |
| `cadre chat say "<text>"` | The session's mid-turn voice (routing from env) |

## Friendly names + phone, the guided way

Two companion runbooks are written so you can hand them straight to Claude Code on the machine:

- [HOSTNAMES-SETUP.md](HOSTNAMES-SETUP.md) — `http://firm.chat` + `http://firm.dash` on any OS
  (hosts entries, optional portless reverse proxy, the WSL2 reboot-healing pattern).
- [TAILSCALE-SETUP.md](TAILSCALE-SETUP.md) — every device type (iOS/Android/mac/Windows/Linux),
  the `cadre chat host tailscale` bind, split DNS for real names on the phone, Add to Home
  Screen.

## Phone access (tailscale)

```
cadre chat host tailscale   # rebind to this machine's tailnet IP (auto-detected)
cadre chat host             # show the current bind + URL
cadre chat host local       # back to 127.0.0.1
```

Open the printed `http://100.x.y.z:7787` on your phone (same tailnet), then **Add to Home
Screen** — the UI ships a PWA manifest, so it installs as a standalone dark app. The layout is
mobile-first below 720px: conversations live in a ☰ drawer, Enter makes a newline on soft
keyboards (SEND sends), and the composer respects the home-bar safe area.

## Security

Default bind is `127.0.0.1` — whoever can reach the port already owns the OS session, the same
trust as your terminal. That's why there is no token and no pairing step. `host tailscale` binds
the tailnet interface only: the tailnet's own device authentication + WireGuard encryption is the
boundary, and every device on your tailnet has full Board access — treat tailnet membership as
Board membership. Binding a public interface is unsupported on purpose.

## State

`~/.cadre/rail/chat/` — `config.json` (no secrets), `threads.json` (conversation ⇄ session map),
`conversations/*.json` (message history). All 0600, all plain JSON you can read.
