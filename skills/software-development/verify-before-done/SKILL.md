---
name: verify-before-done
description: "Before claiming work is fixed, done, passing or deployed."
version: 1.0.0
author: hermes-plugins contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: software-development
    tags: [verification, testing, completion, deploy, evidence, qa]
    related_skills: [systematic-debugging, test-driven-development, requesting-code-review, delegation-brief]
---

# Verify Before Done

A claim of "fixed", "done", "passing" or "deployed" needs evidence from this session: a command you ran, its output, and the resulting state you inspected. Finding the cause of a failure is `systematic-debugging`; writing the failing test first is `test-driven-development`.

## When to Use

- About to tell the user something works, is fixed, passes, or is live
- About to commit, open a PR, close a task, or mark a todo complete
- A subagent, CI job or script reports success and you are about to relay it
- After a deploy, migration, config change or service restart

Don't use for: exploratory answers that make no claim of completion.

## Evidence Table

| Claim | Evidence required | Not enough |
|---|---|---|
| Tests pass | This session's test output: 0 failed, skipped count read | An earlier run, "should pass" |
| Bug fixed | Original symptom reproduced, then shown gone | Code changed, suite green |
| Feature works | User path run end to end, resulting state inspected | Build green, unit tests green |
| Deployed | Live target serves the new version; write path exercised | Deploy command printed success |
| Data written | Row, file or object read back | Write call returned 200 |
| Subagent done | Its verification re-run by you, or end state inspected | Its report |

## Procedure

1. **Name the claim and the path.** Write one line: the claim, and the exact path a user takes to hit it (URL and method, CLI invocation, screen and button, job and input). Done when someone else could repeat the path from that line.
2. **Pick a check that can fail.** It must go red if the claim is false. Prefer the user's path over a proxy:
   - A green build or passing CI proves it compiles and the existing tests pass. Run the feature itself.
   - Test the write path, not only a read. A GET smoke test passes while uploads, saves and form posts are broken.
   - Test the value the consumer reads. Two components can each pass their own tests while the integrated output never changes. Check the rendered value, the table row, the bytes in the file.
   - For a bug fix, reproduce the original symptom first, then show it gone.
3. **Build clean for anything shipped.** An incremental build can ship stale code. Wipe the build output or build in a fresh checkout before producing the artifact you deploy, then confirm the artifact's version string or timestamp matches this build.
4. **Run it fresh.** Run the full check now with `terminal`. Output from before your last edit does not count. Done when you have the command, its exit code and its output from this session.
5. **Inspect the resulting state.** "Deployed", "Saved", "0 errors" and exit 0 are the tool's claims. Query the database row, `read_file` the written file, fetch the deployed page and compare its version, list the uploaded object, or take a screenshot with the browser tools and check it with `vision_analyze`.
6. **Treat a quiet log as no evidence.** Crashes can exit before writing a line, and log rate-limiting drops lines. "No errors in the log" counts for nothing until step 5 passes.
7. **Check delegated claims.** A subagent's or CI's "done" is a claim. Re-run its verification command or inspect the end state it describes.
8. **Report with scope.** For each claim state the exact path exercised, the command and result, and a `Not verified:` line naming what you left unchecked (other platforms, roles, the email step, production data). If a check failed, say so and show the output.

## Pitfalls

- "Should work", "looks good", "probably fixed" mark an unverified claim. Run the check or label the claim unverified.
- Verifying in one environment and reporting on another (local passes, claim is about production).
- Checking that the service is up (health endpoint returns 200) instead of that the feature works.
- Re-running only the test you changed. Run the suite covering the area, then the user path.
- Checking a deploy through a cache. Bypass it (cache-busting query string, `curl -H 'Cache-Control: no-cache'`) and confirm the version.
- Reporting "all tests pass" when tests were skipped or deselected. Read the skipped count.
- A regression test that passes without the fix proves nothing. Revert the fix, watch the test fail, restore it, watch it pass.

## Verification

The report sent to the user contains, for every claim:
- the path exercised (exact command, URL, or UI steps)
- the evidence (output excerpt, queried value, screenshot path)
- a `Not verified:` line
