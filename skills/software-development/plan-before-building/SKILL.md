---
name: plan-before-building
description: "Before multi-step builds: design, plan, one approval round."
version: 1.0.0
author: 42-evey
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: software-development
    tags: [planning, design, spec, requirements, approval, tasks]
    related_skills: [spike, test-driven-development, verify-before-done, delegation-brief, work-ledger]
---

# Plan Before Building

Turn a request into a written spec and a task plan, decide every detail you can yourself, and get the user's approval in one round. When the open question is whether something is feasible at all, use `spike` first.

## When to Use

- A request that adds a feature, changes behavior, touches more than a few files, or spans sessions
- The user asks for a design, a plan, or to think it through first
- Work you will hand to subagents, which need tasks with exact files
- Don't use for: one-line fixes, a task the user already specified file by file, debugging (`systematic-debugging`)

## Procedure

1. **Read before asking.** Read the relevant code, docs and recent history (`terminal("git log --oneline -20")`), and find conventions with `search_files`. Done when you can name the files the change touches.
2. **Sort the open decisions.** List them all. Mark each one:
   - **Architecture**: data model, interfaces, storage, external dependencies, what the user sees, irreversible operations, recurring cost.
   - **Detail**: names, file layout, a library within the existing stack, error text.
   Decide every detail yourself, following the project's conventions.
3. **Ask the architecture questions once.** Send them together in one message (`clarify` or plain text). Give each your recommended answer, the alternatives, and what each choice changes. With no architecture questions, skip to step 4. Done when each has an answer.
4. **Write the spec** to `docs/specs/YYYY-MM-DD-<topic>.md`, or wherever the project keeps specs:
   - **Request**: the user's words quoted verbatim from their messages. Paraphrase is where scope drifts.
   - **Goal**: the end state in one or two sentences.
   - **Out of scope**: what this work does not do.
   - **Decisions**: each call you made, with a one-line reason.
   - **Design**: components, data flow, interfaces, error handling.
   - **Done means**: observable checks that prove it works on the user's path (`verify-before-done`).
5. **Write the plan** to `docs/plans/YYYY-MM-DD-<topic>.md`. One task per change that can be finished and verified in one sitting. Each task names Files, Change, Proof and Depends on (template below). Order tasks so each leaves the project building and testable; put tests with or before the code they cover.
6. **Review the plan against the spec.** Every spec requirement maps to a task. No task says "TBD", "handle edge cases" or "add error handling" without naming the cases. Names and signatures match across tasks. Every Proof command can fail. Fix gaps in place.
7. **Ask for one approval.** Send the spec and plan paths and a short list of the calls you made for the user ("Decided: SQLite, not Postgres, because the app is single-user; ..."). Done when the user approves. Write any correction into the spec, not only the chat.
8. **Hand off.** Execute task by task, or brief one subagent per task with `delegation-brief`. For work that spans sessions, copy the tasks into the ledger (`work-ledger`).

## Plan Task Template

```markdown
### Task 3: Persist upload metadata
- Files: src/uploads/store.py (modify), tests/test_store.py (create)
- Change: add `save_meta(upload_id: str, meta: dict) -> None`, writing to the `uploads` table
- Proof: `pytest tests/test_store.py -q` -> 3 passed; after one test upload,
  `sqlite3 dev.db "select count(*) from uploads"` -> 1
- Depends on: 1
```

Tasks with `Depends on: none` and disjoint Files can run in parallel.

## Pitfalls

- Asking questions one per message. Batch the architecture questions; decide the rest.
- Asking the user what the code already answers.
- Restating the request in your words in the spec, then building to your words.
- A Proof of "tests pass". Name the test file and the user-path check.
- Starting implementation before approval, or approving a design that exists only in chat.
- Scope growth ("while I'm here"). Put extras under Out of scope or a follow-up list.

## Verification

- The spec file exists and holds a verbatim quote of the request, Decisions, Out of scope and Done means.
- The plan file exists; every task has Files, Change and Proof; no placeholders remain.
- The user's approval reply covers the listed decisions.
