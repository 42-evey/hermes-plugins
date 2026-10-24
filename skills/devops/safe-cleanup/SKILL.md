---
name: safe-cleanup
description: "Before deletes, overwrites, kills or test runs on user data."
version: 1.0.0
author: hermes-plugins contributors
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    category: devops
    tags: [cleanup, delete, backup, archive, processes, data-safety, destructive]
    related_skills: [verify-before-done, work-ledger]
---

# Safe Cleanup

Delete, move, overwrite, truncate, kill, drop or reset without losing anything the user needs, and keep test runs from writing into real user data. Look at the target first, act on an exact list, keep a way back.

## When to Use

- Deleting or moving files, freeing disk space, pruning branches, containers, images or volumes
- Overwriting a file, config or database; `git reset --hard`, `git checkout -- .`, `git clean`, force-push
- Killing processes or restarting services
- A scripted run of a game, app, migration or sync tool that may autosave, migrate or overwrite saves, configs or databases
- Don't use for: scratch files you created this session that nothing else uses

## Procedure

1. **Confirm the exact set.** If the request is ambiguous ("clean up the old stuff", "remove the test accounts", a name that may be a typo), list the exact items you would touch and confirm them with `clarify` first. Never widen the scope of a destructive request from an ambiguous reading; take the narrow reading.
2. **Look at each target.** Inspect it before you touch it: `terminal("ls -la <path>")`, `du -sh <path>`, `read_file` for a file; `git status`, `git stash list` and `git log @{u}..` for unpushed work in a repo; `SELECT count(*)` for a table. Done when you have a written list of targets with size and contents.
3. **Prefer moving to an archive over rm.** Move targets into `archive/<YYYY-MM-DD>/` and write `MANIFEST.txt` there: original path, size, sha256, reason.
4. **Delete an original only after the backup is proven to contain it.** Per file: list it in the archive or backup (`tar -tvf`, `unzip -l`, the backup tool's listing) and compare size or hash (`sha256sum`; `shasum -a 256` on macOS) against the original. Then delete that one file.
5. **Delete per file, never by wildcard.** Build the explicit list, read it, delete from it item by item. No `rm -rf dir/*`. Before `find ... -delete`, run the same `find` with `-print` and read the output. Quote paths and guard variables: `rm -- "${dir:?}/name"`.
6. **Kill by PID, not by pattern.** `pgrep -f`/`pkill -f <pattern>` also match the shell running your own loop or command. Kill by the PID file the process wrote, or find it with `pgrep -x <exact-name>`, confirm the cmdline with `ps -o pid,args -p <pid>`, then `kill <pid>`. Send TERM, wait, check, and send KILL only if it is still running.
7. **Protect user state around test runs.** Before a run that may write saves, configs or databases, copy them aside: `cp -a <dir> <dir>.bak-<timestamp>`; for SQLite `sqlite3 <db> ".backup <db>.bak"`; for Postgres `pg_dump`. After the run, restore and confirm with `diff -r` or hashes. Better: point the program at a scratch profile or data dir (flag or env var) so the real state is never opened.
8. **Report** what was removed, moved or killed, where the archive or backup is, and what you left because you could not confirm it.

## Pitfalls

- `git clean -fdx` also deletes ignored files: `.env`, local databases, caches. Run `git clean -ndx` first and read it.
- `git reset --hard` and `git checkout -- .` discard uncommitted work with no undo. `git stash` first.
- `docker system prune --volumes` and `docker volume rm` delete databases. List volumes and the containers mounting them first.
- An archive that was written but never listed may be empty or truncated (full disk, interrupted write). List it before trusting it.
- Overwriting a config in place without `cp <file> <file>.bak` and a diff afterwards.
- Cleaning up test data by a broad filter (`WHERE email LIKE '%test%'`) catches real users. Delete by the IDs you created.
- A backup taken while the program is running can be inconsistent. Stop it or use the database's own backup command.

## Verification

- Every removed item is on the confirmed target list; every archived item is in the manifest with a matching hash.
- After a kill, `ps -p <pid>` shows the target gone, and nothing outside the list was signalled.
- After a test run, the user's state matches the backup (`diff -r` or hashes), or the run never opened it.
