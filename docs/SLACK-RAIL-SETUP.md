---
type: doc
status: active
tags: [cadre, slack, rail, boardroom, setup, runbook]
relatedTo: [cadre, slack-rail-design]
---

# Slack Rail — Setup Runbook

The Co-Board in your pocket: a Slack channel where every message opens a boardroom session on
your machine, and every thread is a running conversation with it. Replies land in the thread;
in `approve` mode every action the Co-Board wants to take asks for your 👍/👎 first.

Everything runs on your machine over an **outbound** Socket Mode connection — no public URL, no
tunnel, works inside WSL2. Computer off = rail off; messages simply wait in Slack.

## Your two manual steps

**0. Make the board channel FIRST** — a **private** channel named `#boardroom` is the usual
choice. Do this before the wizard: Slack only shows a bot private channels it has been invited
to, so a channel created mid-wizard won't appear in the pick list until you `/invite` the bot
and refresh (`r` at the prompt). Creating it first skips that dance.

**1. Create the Slack app** — run `cadre slack manifest`, copy the JSON, then at
[api.slack.com/apps](https://api.slack.com/apps): *Create New App → From a manifest → paste*.
The manifest creates the bot, scopes, and event subscriptions in one shot. Two things it
CANNOT do, in the new app:

- *Settings → Socket Mode* → toggle **Enable**. In the token dialog that appears, the scope list
  is empty — click **Add Scope → `connections:write`**, name it anything (e.g. `socket-mode`),
  **Generate**, copy the `xapp-…` token. (This app-level token is separate from the bot scopes;
  the manifest can never create it — everyone trips here once.)
- *Install App* → install to your workspace — copy the **Bot User OAuth Token** (`xoxb-…`).

Then invite the bot to your board channel: type `/invite @cadre-board` (or whatever you named
the bot) inside the channel.

**2. Run the wizard:**

```sh
cadre slack setup            # --firms-root <path> if your firms don't live at ~/firms
```

It verifies both tokens against Slack, stores them in the encrypted vault (global tier — never
in a file), lists your channels to pick from, invites the bot (or tells you the `/invite` to
type), pairs you (post any message in the channel; that user id becomes the allowlist), and asks
for the permission mode:

| Mode | What it means |
|---|---|
| `approve` (default) | Every side-effect posts *"Co-Board wants to run: …"* in the thread and waits up to 5m for your 👍 (allow) / 👎 (deny). Unanswered = denied. |
| `skip` | Full trust — the session runs like a terminal you opened yourself. |

## Verify, then run it for real

```sh
cadre slack test             # posts a wiring-test message into the channel
cadre slack serve            # foreground once, watch it work
cadre slack enable           # then: systemd user service, restarts on failure
cadre slack status           # service state, mode, thread count, last activity
```

## Using it

- **New message in the channel** → new boardroom session over your whole portfolio.
- **`@<firm-id>` prefix** (e.g. `@downstream approve the hire gate`) scopes that thread to one
  firm.
- **Reply in a thread** → same session continues, context intact. Threads idle 30+ days start
  fresh (and say so).
- **Reply while a turn is running** → steered INTO the live session (📨); its answer folds your
  message in. If the relay can't reach it, the message queues (🕐) as the next turn instead.
- The session narrates load-bearing moments into the thread mid-turn (`updates` toggle);
  the final answer always posts on its own — never rely on silence meaning anything.

**The signal legend** (every message gets honest state, never silence, never fake-green):

| Signal | Meaning |
|---|---|
| ⚙️ reply | received — session opening / resuming |
| 👀 | a turn is working on this message |
| ✅ | answered in the thread |
| ❌ + reason | failed, with why |
| 📨 | steered into the live turn mid-flight |
| 🕐 | queued behind the running turn |

## Ongoing management

| Want | Command |
|---|---|
| Pause the rail | `cadre slack disable` |
| Rotate tokens | `cadre slack setup` again (vault overwrite; config survives — say `Y` to reuse, or paste new ones) |
| Switch mode | `cadre slack mode approve` / `cadre slack mode skip` (restarts the service itself) |
| Pin the model | `cadre slack model opus[1m]` · back to account default: `cadre slack model default` |
| Quiet the narration | `cadre slack updates off` (one answer per turn, nothing between) |
| Health check | `cadre slack status` — `last_activity_age_sec` catches a stalled socket |

## How it holds the line

- **Allowlist is structural** — only the paired user's messages spawn anything or count as an
  approval; everyone else in the channel is ignored in code, not in prompts.
- **Tokens never touch disk** — vault → env → process, same as every Cadre secret.
- **Sessions boot at the firms root**, never inside a firm — the Co-Board seat, not a member
  seat. `/boardroom`'s hard rules (gates only on your explicit verdict, drafts-only for anything
  external) bind every turn.
- The approval gate **fails closed**: misconfiguration, Slack outage, or silence all deny.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `setup` rejects the app token | The `xapp-` token needs `connections:write` — recreate it under Socket Mode settings. |
| Messages ignored | Wrong channel, or you're not the paired user — `cadre slack status` shows both. |
| ❌ `claude failed to exec` under systemd | Bare systemd PATH — re-run `cadre slack enable` (re-captures `CADRE_CLAUDE_BIN`), or set it in the unit. |
| ❌ `turn timed out` | Board turns get 30m by default — raise `turn_timeout_sec` in config for heavyweight agendas. |
| Approval prompts never appear | Mode is `skip`, or the bot lost `reactions:read`/`chat:write` — re-install the app from the manifest. |

WSL2 note: the rail holds the WSL VM open while enabled. If the VM is down, the rail is down —
`cadre slack status` after boot confirms it came back (`Restart=on-failure` handles crashes, not
VM teardown).
