---
allowed-tools: Bash(/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/watch.py:*)
argument-hint: [label]
description: Push Slack pings when this session is ready (Orby watch)
---

## Enrollment result

!`/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/watch.py enroll --label "$ARGUMENTS"`

## Standing instructions for this session (now watched)

The user has stepped away. Every time you stop, Orby posts a Slack ping built from
your last message. To make pings crisp, at every genuine stopping point — task
complete, PR created, findings ready, or blocked on the user — use the Write tool
to write the sentinel file at the exact "Sentinel path" shown in the enrollment
result above, containing a single JSON object:

{"category": "<pr_ready|findings|input|done>", "summary": "<one crisp line>", "link": "<URL, or omit the key>"}

- `pr_ready` — you created or updated a PR; link = the PR URL
- `findings` — an investigation/analysis finished; summary = the core ideas in one line
- `input` — you are blocked on a question or decision; summary = exactly what you need
- `done` — any other completed chunk of work

Rules:
- Write the sentinel BEFORE ending the turn it applies to.
- Only at genuine stopping points — skip routine mid-task turns (the fallback
  uses your last message, which is fine).
- summary is plain text, one line, no markdown.

Acknowledge in one short sentence that watch mode is active, then continue any
pending work.
