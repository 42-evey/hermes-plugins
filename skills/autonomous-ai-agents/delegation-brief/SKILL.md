---
name: delegation-brief
description: "Writing a delegate_task brief and checking what comes back."
version: 1.0.0
author: hermes-plugins contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: autonomous-ai-agents
    tags: [delegation, subagents, delegate_task, briefing, parallel, orchestration]
    related_skills: [verify-before-done, plan-before-building, work-ledger, requesting-code-review]
---

# Delegation Brief

A subagent starts with a fresh conversation. It knows only the `goal` and `context` you pass to `delegate_task`, plus the workspace's project context files (AGENTS.md and similar). Its final summary is the only thing that comes back.

## When to Use

- Before any `delegate_task` call, single or batch
- Before handing work to another agent or tool (kanban task, external coding CLI)
- When a subagent result comes back and you are about to act on it or relay it

## Decide Whether to Delegate

- **Delegate**: independent tasks on disjoint files; research or sweeps that would flood your context; an independent review.
- **Do it yourself**: a handful of tool calls; a lookup whose target you know; work that needs this conversation's history to judge.
- Parallelize only disjoint work. Two agents never edit the same working tree; give each its own git worktree or keep edits serial.
- Fewer agents beats more. Each one costs tokens and leaves a result you must check.
- If task B needs A's output, dispatch A, wait for it, then dispatch B with A's result in its `context`.

## Brief Template

Put the end state in `goal` and the rest in `context`:

```
GOAL: <one-sentence end state>
KNOWN / RULED OUT: <findings and failed attempts, so it does not repeat them>
READ FIRST: <exact files, docs, commands>
SCOPE: may change <files/dirs>; must not touch <named exclusions>
DONE MEANS: <observable criteria>
VERIFY WITH: <exact command> -> <expected result>
LIMITS: stop after <N> minutes or when blocked; no commit/push/deploy unless stated
REPORT: end with DONE (each with evidence), STUCK (what was tried), NEXT (in order)
```

For machine-readable results, add `output_schema` to the task and require only the fields you will read.

## Procedure

1. **Decide** using the section above. Done when no two concurrent tasks touch the same file.
2. **Fill the template** for each task. Use repo-relative or absolute paths and copy-pasteable commands. Quote the user's words verbatim when they define the requirement; the child cannot see the conversation. Done when the brief alone is enough to start.
3. **For an independent review, give the code, not your conclusion.** Point at the diff or files and the requirements and ask what is wrong. A subagent handed your hypothesis tends to return it confirmed.
4. **Dispatch.** `delegate_task(goal="...", context="...")`, or `delegate_task(tasks=[{"goal": ..., "context": ...}, ...])` for parallel work. Children run in the background: finish the work that does not depend on them, then end the turn instead of polling.
5. **Check the result.** The report is a claim. For each DONE item, run its VERIFY WITH command yourself or inspect the end state (`terminal("git diff --stat")`, `read_file`, a query). `exit_reason: max_iterations` or `truncated: true` means the child ran out of budget, not that it finished. Run `terminal("git status")` and look for files outside SCOPE.
6. **Integrate.** Merge or apply the results, then run the full test suite once after everything lands; pieces that pass alone can fail together.

## Pitfalls

- "Fix the tests" with no file, error message or done criteria. The child guesses.
- Omitting what is already ruled out. The child spends its budget rediscovering it.
- Pasting your theory into a review brief.
- Relaying a child's "all tests pass" without running them.
- Passing `toolsets` or a per-task model. Children inherit the parent's toolsets; with the default backend every child runs on `delegation.model` from config.yaml, and the iteration cap is the global `delegation.max_iterations`. Put a time or step cap in the brief text instead.
- Treating STUCK as failure to hide. A STUCK list with what was tried is the input for the next brief.

## Verification

- Every brief has GOAL, SCOPE, DONE MEANS, VERIFY WITH and REPORT.
- Each child's DONE items were checked by you; your report names the command or state you checked.
- `git status` shows no changes outside the briefs' scopes.
