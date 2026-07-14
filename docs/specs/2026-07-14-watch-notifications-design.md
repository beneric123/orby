# Orby "watch" — push-on-ready session notifications

**Date:** 2026-07-14
**Status:** Approved design, pending implementation
**Requested by:** Benjamin (many concurrent Claude Code sessions across repos/worktrees; wants a Slack ping when each one is ready for attention)

## Problem

Orby today is pull/attach-first: a Slack thread must be explicitly attached to a session
(`!attach`) before any hook events are forwarded. With `sessions.json` empty, every hook
fires and aborts (`no matching session found`). There is no way for a session the user
walks away from to proactively say "I'm ready — come look."

Desired messages, per session:

- `session x: PR ready (PR link)`
- `session x: ready, input needed`
- `session x: findings (summary of core ideas)`

## Decisions (made during brainstorming)

| Decision | Choice |
|----------|--------|
| Enrollment | **Opt-in per session** via `/watch` — only sessions the user is walking away from notify |
| Destination | **DM, one thread per session** — root message on enroll, events threaded under it |
| Direction | **One-way notify only** (v1) — attach mode remains the separate two-way path |
| Content | **Hybrid** — structured sentinel written by Claude when available, heuristic fallback always |
| Session key | `CLAUDE_CODE_SESSION_ID` env var (verified present in session shells; matches hook `session_id`) — no tmux dependency |

## Key simplification

One-way pings only need Slack Web API `chat.postMessage` with the existing bot token from
`config.env`. **The Socket-Mode `bot.py` process does not need to be running.** Everything
runs inside the already-wired hook script lifecycle.

## Components (all in `~/orby`)

### 1. Watchlist store — `~/.orby/watch.json`

Keyed by Claude session ID. Atomic writes (same `.tmp` + rename pattern as `core/session.py`).

```json
{
  "fcafc3ea-...": {
    "label": "flaky-test-fix",
    "repo": "experiment-framework",
    "branch": "feat/podcast-establishing-multivendor",
    "cwd": "/home/ubuntu/experiment-framework",
    "dm_channel": "D0XXXXXXX",
    "thread_ts": "1720999999.123456",
    "enrolled_at": "2026-07-14T18:00:00",
    "last_ping": "2026-07-14T18:05:00"
  }
}
```

### 2. `watch.py` — enrollment CLI

- `watch.py enroll [--label <label>]`
  - Reads `$CLAUDE_CODE_SESSION_ID`, `$PWD`; derives repo + branch via `git rev-parse
    --show-toplevel` / `git branch --show-current` (best-effort; blank outside a repo).
  - Resolves the DM channel via `conversations.open` with `ORBY_NOTIFY_USER` (cached in
    the watch entry).
  - Posts root message: `👀 Watching *<repo>* @ `<branch>` — <label>` and stores its
    `ts` as `thread_ts`.
  - Writes the watch entry.
- `watch.py unwatch` — removes the entry for `$CLAUDE_CODE_SESSION_ID`, posts a final
  `🛑 Stopped watching` reply in the thread.
- `watch.py list` — prints active watches (for debugging).

### 3. `hooks/notify.py` — new push branch

Inserted **before** the existing attach-mode lookup; attach behavior is untouched.

```
hook fires → session_id in watch.json?
  yes → push mode: classify + post to that session's DM thread → return
  no  → existing attach-mode logic (sessions.json match, etc.)
```

Push mode handles only:

| Event | Behavior |
|-------|----------|
| `Stop` | Classify (below), post labeled reply |
| `Notification` | Post `⏸️ Needs input` + prompt text |
| `SessionEnd` | Post `🏁 Session ended`, remove watch entry |
| `PreToolUse` and everything else | Ignored — this is what keeps watched sessions quiet during work |

`SessionEnd` requires one new hook entry in `~/.claude/settings.json` (same
`notify.py --event SessionEnd` shape as the existing three).

### 4. Enrollment UX — `/watch` and `/unwatch` slash commands

Global commands in `~/.claude/commands/`:

- `watch.md` — runs `watch.py enroll --label "$ARGUMENTS"`, then instructs the session:
  from now on, when finishing a chunk of unattended work (PR created, findings ready,
  blocked on input), write the sentinel file (format below) before ending the turn.
- `unwatch.md` — runs `watch.py unwatch`.

Running `/watch` costs one turn in the session; that same turn installs the sentinel
convention (this is what makes the hybrid content work).

## Classification (hybrid)

On `Stop`, `notify.py` resolves label + summary in priority order:

1. **Sentinel** — if `~/.orby/status/<session_id>.json` exists, use it and delete it
   (consume-once, so a stale status never re-fires):

   ```json
   { "category": "pr_ready | findings | input | done",
     "summary": "one crisp line",
     "link": "https://github.com/.../pull/123" }
   ```

2. **Heuristic fallback** (always available — a forgotten sentinel degrades summary
   quality, never drops a ping):
   - Last assistant message contains a GitHub PR URL (`/pull/\d+`) → `✅ PR ready` +
     extracted link.
   - Otherwise → `📋 Done` + trimmed tail (~500 chars) of `last_assistant_message`
     (falling back to the transcript via the existing `_get_last_response`).

`Notification` events are always `⏸️ Needs input` (no sentinel needed — the hook
payload carries the prompt message).

### Example DM thread

```
👀 Watching experiment-framework @ feat/podcast-establishing-multivendor — flaky-test-fix
  └ ✅ PR ready — https://github.com/heygen-com/experiment-framework/pull/40312
     Routed establishing_video through MultiVendorVideoProvider; 3 tests added.
  └ ⏸️ Needs input — "Run the migration against dev now? (y/n)"
  └ 🏁 Session ended
```

## Config

One new key in `config.env`:

```
ORBY_NOTIFY_USER=U0XXXXXXX   # Slack member ID to DM
```

Existing bot scopes (`im:write`, `chat:write`) are sufficient.

## Error handling & lifecycle

- Hooks remain `async` fire-and-forget; failures log to `hooks.log`, never block a session.
- Slack API errors, malformed sentinels, missing git info → log and fall through
  (heuristic covers sentinel failures; blank repo/branch just shortens the label).
- Watch entries are removed on `SessionEnd` and pruned after 7 days (mirrors
  `SessionManager` pruning) to survive crashed sessions.
- Watched sessions ping on **every** Stop until unwatched — deliberate; enrollment
  means "I have walked away."

## Out of scope (v1)

- Two-way control from the DM thread (reply-to-continue, approve/reject) — attach mode
  already exists for this; the thread-per-session layout leaves room to add it later.
- Per-tool streaming for watched sessions.
- Any new long-running process.

## Testing

**Unit (pytest, mocking `urlopen`):**
- `classify()`: sentinel present/absent/malformed; consume-once deletion; PR-URL
  extraction incl. multiple URLs (last wins); needs-input; plain done with truncation.
- Watchlist: enroll/unwatch round-trip, atomic write, 7-day prune, `SessionEnd` cleanup.
- Push-vs-attach routing: watched session never falls through to attach logic;
  unwatched session behavior unchanged.
- Slack payload shape: channel/thread_ts/text for each event type.

**Manual E2E:**
1. `/watch test-run` in a throwaway session → root message appears in DM.
2. Ask something trivial → `Stop` fires → `📋 Done` reply in thread.
3. Write a sentinel by hand → next Stop uses it → file deleted.
4. Trigger a permission prompt → `⏸️ Needs input` reply.
5. `/unwatch` → `🛑` reply, entry gone from `watch.json`.
