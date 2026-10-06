# ledger-continuity

**An agent is mortal; its claimed work is not.**

The minimal ledger pattern for agent recovery. Kill an agent mid-task;
a stranger picks it up from the ledger alone — no direct messaging, no
shared memory, only the ledger file.

Proven on Oracle (2026-10-05): three crash variants, all passing.

## The minimal shape

Three claim types, nothing more:

1. **Task claim** — `{'op': 'task', 'task_id', 'subtasks': [{subtask, input}, ...]}`
   Must be **self-contained**: the full work definition lives in the
   ledger, not in the dead agent's memory. Without inputs in the ledger,
   recovery is impossible.
2. **Per-unit result claims** — `{'op': 'subresult', 'task_id', 'subtask', 'result'}`
   with mandatory evidence. Granular enough that partial progress survives.
   A successor diffs these against the task claim to find what's left.
3. **Heartbeats** — `{'op': 'heartbeat', 'task_id'}` with timestamps.
   The non-obvious one: **recovery requires liveness in the ledger.**
   Without heartbeats, a successor cannot distinguish "dead" from "slow."
   Work claims alone are insufficient — you need a death detector, and the
   ledger is the only channel.

Plus one behavior: **re-check before claiming.** After doing the work,
re-read the ledger; if a peer claimed the subtask meanwhile, skip.
This gives exactly-once *recording* under at-least-once *execution*.

## Run it

```bash
python3 test_recovery.py
```

You will watch three agents die and three strangers finish their work:

- **clean crash** — A does 2 of 5, hard-crashes (`os._exit`, no cleanup).
  B started while A was still alive, correctly *waits* on the heartbeat
  instead of stealing work, detects death at staleness, finishes the 3.
- **mid-subtask crash** — A dies *during* subtask 2, before claiming it.
  B trusts only claimed results, redoes subtask 2 idempotently.
- **chained A→B→C** — A does 2 and dies, B takes over, does 1, dies,
  C finishes. Same code path every time; recovery is recursive by
  construction.

Expected: `ALL RECOVERY TESTS PASS` in ~15 seconds.

## Use it

```python
from ledger import append, read_all

append("agent-1", {"op": "task", "task_id": "T1",
                   "subtasks": [{"subtask": 0, "input": 10}]},
       {"created": True})
```

Or run a worker directly:

```bash
python3 worker.py <agent_id> --task T1 --demo --ledger ./ledger.jsonl
```

Replace `do_work()` in `worker.py` with your real work. Keep it
deterministic enough that evidence can verify the result — the ledger's
`verify()` depends on it. Set `LEDGER_CONTINUITY_LEDGER` to move the
default ledger path.

## Honest limitations

- **Death detection is timeout-based, not lease-based.** A merely *slow*
  worker triggers a false takeover → duplicate work (but not duplicate
  *recording*, thanks to re-check-before-claim). For cooperative agents
  with sane timeouts this is correct; adversarial or high-stakes settings
  need real fencing/leases.
- **Simultaneous starts race.** Two workers booting at the same instant
  may both claim and both work; recording stays correct, execution is
  at-least-once. Stagger starts or add an external lock if this matters.
- **No clock-skew or contention testing.** Heartbeat staleness assumes
  roughly synchronized clocks. Two writers appending simultaneously rely
  on `O_APPEND` + `fcntl` locking (POSIX); not proven under real contention.
- **Evidence semantics are yours.** The ledger enforces that evidence
  *exists*, not that it's *true*. `verify()` re-checks claims against
  evidence with a caller-supplied verifier — garbage evidence in means
  garbage verification out.
