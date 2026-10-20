# Bounded apply-agent contract

## Apply-agent prompt requirements (non-negotiable, all verified necessary the hard way)

The agent executing `run` needs an extremely tightly-scoped prompt:

- **Write the manifest, verify it round-trips as JSON, run the command EXACTLY ONCE with output
  redirected to a file, Read that file, return its exact contents.** Do not have it capture
  command output inline via Bash — inline capture silently truncates around 30k characters,
  producing invalid JSON the orchestrator can't parse.
- **Give every attempt (first try, narrowed retry, retry-after-external-revert) its own distinct
  manifest/output filenames.** Reusing one path across attempts risked a shorter rewritten file
  retaining stale trailing bytes from an earlier, longer write — "Extra data" JSON corruption at
  the boundary. Distinct filenames make the whole bug class impossible regardless of the exact
  write-timing mechanism behind it.
- **After the Write, verify the file parses as JSON before running anything; if it doesn't,
  rewrite the whole blob (not a patch) and verify again.** A single very-large Write call can
  occasionally leak a stray tool-syntax fragment into the file, corrupting it independent of the
  truncation issue above.
- **Explicitly forbid running the populate command more than once, running `--help`, running any
  other diagnostic command, and — above all — running ANY git command whatsoever** (status, diff,
  reset, clean, checkout, add, commit, log, reflog). State plainly that a non-success result
  (`committed:false`, an `aborted` status, a nonzero exit code) IS the correct final answer for
  the task, not a problem to solve. Without this, a confused agent that sees an unexpected result
  will start "helpfully" investigating — and a subagent WILL, unprompted, run `git reset --hard`
  + `git clean -fd` against the real store if left to freelance a fix. Uncommitted user content may have no recovery path.
- With a host that caches subagent results, changing only a display label may
  replay the old result. Check that host's cache contract and verify a retry
  actually executed; do not assume every host offers Workflow-style caching.
