---
name: work-ledger
description: "Multi-session or long work that must survive compaction."
version: 1.0.0
author: 42-evey
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: autonomous-ai-agents
    tags: [ledger, state, long-running, backlog, compaction, handoff, resume]
    related_skills: [verify-before-done, plan-before-building, delegation-brief]
---

# Work Ledger

Keep the state of long work in a markdown file on disk so it survives context compaction, `/new`, restarts and handoffs between agents. The `todo` tool lives in one session and `/goal` keeps one objective alive; the ledger holds every request, what is done and how it was tested, and where to resume.

## When to Use

- Work spanning several sessions or days, or driven by `/loop`, `/goal` or cron
- A backlog of more than a few items, or a user who adds requests while you work
- Before a long autonomous run, or when the context is close to compaction
- Handing work to another agent, or picking it up from one
- Don't use for: a task that fits in one session; `todo` is enough

## Location

- One ledger per project: `LEDGER.md` at the repo root, or outside the tree (for example `~/.hermes/ledgers/<project>.md`) when the repo must stay clean.
- Save the path with `memory` so a new session finds it.
- One agent owns the writes. Other agents report to the owner instead of editing the file.

## Procedure

1. **Read the ledger once at the start of each cycle** with `read_file`. Act from the STOP line and the Doing/Todo lists. Do not rebuild state from code or old sessions; use `session_search` only for something the ledger lacks.
2. **Record every new user request before acting on it.** Add it to Todo with the date and the user's words quoted verbatim. A reply to the current work is not a new request.
3. **Work one item at a time.** Move it to Doing with the date and current state.
4. **Close only with a test note.** Move it to Done with `tested:` naming the exact check and its result (`verify-before-done`). No test note, no close.
5. **Reopen what breaks.** When a Done item turns out broken, move it back to Todo with `reopened: <date> <what failed>`. Keep its ID; do not add a duplicate.
6. **Record blockers and standing instructions.** A blocked item names what it waits on: a user decision, hardware, another item ID. Durable user preferences go under Notes, verbatim.
7. **Log events.** One line per commit, deploy or decision.
8. **End every session with a STOP line** replacing the previous one: the item, the file, the next command, and any half-finished state (uncommitted changes, a running process and its PID file, a pending migration).

With `/goal` or `/loop`, put "read LEDGER.md first; update it before ending the turn" into the goal or loop prompt.

## Template

```markdown
# Ledger: <project>

STOP <YYYY-MM-DD HH:MM>: <item ID, file, next command, uncommitted or running state>

## Notes
- <YYYY-MM-DD> "<standing instruction, verbatim>"

## Doing
- [ ] T7 <title> (started <date>): <current state>

## Todo
- [ ] T8 <title> (user, <date>): "<request, verbatim>"
- [ ] T9 <title> (blocked by: T7 | user decision on <X> | <hardware>)
- [ ] T3 <title> (reopened <date>: <what failed>)

## Done
- [x] T5 <title> (<date>, <commit sha>) tested: <exact check> -> <result>

## Log
- <YYYY-MM-DD HH:MM> <sha or event>: <one line>
```

IDs are never reused, so references to them stay valid after edits.

## Pitfalls

- Acting on a mid-task request first and recording it later. It is lost at the next compaction.
- Closing an item with "done" and no `tested:` note.
- A ledger too long to read every cycle. Move Done items and Log lines older than the current milestone to `LEDGER-archive.md`.
- Keeping state only in `todo`. It is gone after `/new`.
- A vague STOP line ("continue the work"). Name the item and the next command.
- Secrets in the ledger. It may be committed; record where a secret lives, never its value.

## Verification

- In a fresh session, `read_file` the ledger and take the next action from the STOP line alone.
- Every user request from this session appears in it.
- Every Done item has a `tested:` note.
