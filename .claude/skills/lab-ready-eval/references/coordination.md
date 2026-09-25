# Coordinating subagents on one host

The coordinator keeps the plan, the merges and the status table; subagents
do bounded work from written briefs. These rules came from collisions.

## Briefs

- One brief per agent: goal, hard rules, exact inputs (paths, commits,
  numbers), the steps, what to verify, and the shape of the report expected
  back. State what is out of scope and who owns the files the agent must not
  touch.
- Say which model: judgment-heavy work (metric changes, report writing,
  boundary design) to the strongest model; mechanical work (regrades,
  sweeps, doc sweeps) to a cheaper one.
- Ask for a report that names commit hashes, files changed, test counts, the
  verification numbers side by side with the expected ones, and anything
  left undone and why. "Nothing in the brief was left undone" should be a
  sentence the agent has to earn.

## The checkout

- Run directories, caches, assets and the hidden corpus are git-ignored and
  live only in the main checkout. Runners and measurement agents work
  there; nobody else does.
- Everyone who touches git works in a worktree on their own branch; only the
  coordinator merges, and only after the checkout is on the expected branch
  with a clean tree. A coordinator `git commit -a` in the main checkout while
  an agent held it once swept the agent's edits into the wrong commit.
- Two agents editing the same file in different worktrees is a merge
  conflict waiting; assign files, or hold the file back for a second pass.

## Long jobs

- Detach hour-long jobs with `nohup setsid … > log 2>&1 &`, keep the pid, and
  wait with a tracked background loop in the coordinator's own session.
  Backgrounding with a bare `&` inside a subagent ends the agent's turn and
  loses the wait. A subagent told to watch a job may stop; the coordinator
  must hold the wait itself.
- Check the log with timestamps and load averages at each step; a timeline
  file per job is worth more than the log.
- Never run two things that both want every core; controls sweeps, cache
  rebuilds and finals are exclusive.

## Agents and the sandboxes

- Every container carries an owner label; sweeps are owner-scoped; a
  harness-wide sweep destroyed another attempt's sandbox and cost two
  calibration runs.
- An agent that cannot get permission for something must say so, not route
  it through another agent.
- Server-side errors (overload) can kill a long documentation pass mid-way;
  ask agents to commit checkpoints after each item, and resume the same agent
  with its context rather than restarting.

## Verification by the coordinator

- Trust but check: re-run the test suite on the branch before merging; spot
  check one number in the report against its evidence path; confirm the
  worktree is clean and the main checkout untouched.
- Keep a memory of decisions the repository does not record (who authorised
  paid runs, what was deferred and why) so the next session does not undo
  them.
- The final message to the person you work for must stand alone: what was
  measured, what changed, what is theirs to do. Nothing that depends on the
  reader having watched.
