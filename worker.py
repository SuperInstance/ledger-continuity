#!/usr/bin/env python3
"""worker.py — a mortal agent.

Claims self-contained tasks from the ledger, heartbeats while working,
and — the point of this tool — takes over dead agents' work using only
the ledger. Every worker runs the same code path; recovery is not a
special mode, it is what happens when heartbeats go stale.

The ledger outlives the agent. This worker is the proof.

Usage:
    python3 worker.py <agent_id> --task T1 --ledger ./ledger.jsonl \\
        --subtasks '[{"subtask": 0, "input": 10}, ...]'
    python3 worker.py <agent_id> --task T1 --demo   # built-in 5-subtask demo

Crash injection (for tests; os._exit = hard crash, no cleanup):
    --crash-after N   die after completing N subtasks
    --crash-mid K     die during subtask K, before claiming (work lost)

Exit codes: 0 = task complete, 1 = crash, 2 = deadline exceeded.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger import append, read_all


def do_work(subtask_input):
    """Demo work function: square the input. Replace with real work.

    Whatever you put here, keep it deterministic enough that evidence
    can verify the result — the ledger's verify() depends on it.
    """
    time.sleep(0.3)  # simulate real work
    return subtask_input * subtask_input


def heartbeat(agent_id, task_id, ledger_path):
    append(agent_id, {"op": "heartbeat", "task_id": task_id},
           {"alive": True}, ledger_path)


def task_state(task_id, ledger_path):
    """Reconstruct everything a stranger needs, purely from the ledger.

    Returns (task, results, beats, claimer):
      task    — the self-contained task claim (subtasks WITH inputs)
      results — {subtask: (result, agent)} for claimed subresults
      beats   — {agent: last_heartbeat_time}
      claimer — agent id that claimed the task (None if unclaimed)
    """
    task, claimer = None, None
    results, beats = {}, {}
    for e in read_all(ledger_path):
        c = e.get("claim", {})
        if c.get("op") == "task" and c.get("task_id") == task_id:
            task, claimer = c, e["agent"]
        elif c.get("op") == "subresult" and c.get("task_id") == task_id:
            results[c["subtask"]] = (c["result"], e["agent"])
        elif c.get("op") == "heartbeat" and c.get("task_id") == task_id:
            beats[e["agent"]] = e["t"]
    return task, results, beats, claimer


def log(agent_id, msg):
    print(f"{agent_id}: {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="mortal ledger worker")
    ap.add_argument("agent", help="this worker's id")
    ap.add_argument("--task", required=True, help="task id to work on")
    ap.add_argument("--ledger", default=None, help="ledger file path")
    ap.add_argument("--subtasks", default=None,
                    help="JSON list of {subtask, input} (used when claiming)")
    ap.add_argument("--demo", action="store_true",
                    help="use the built-in 5-subtask demo (squares of 10..50)")
    ap.add_argument("--heartbeat-timeout", type=float, default=3.0,
                    help="seconds after which a heartbeat is stale (death)")
    ap.add_argument("--deadline", type=float, default=120.0,
                    help="give up waiting after this many seconds")
    ap.add_argument("--crash-after", type=int, default=None,
                    help="hard-crash after completing N subtasks (test only)")
    ap.add_argument("--crash-mid", type=int, default=None,
                    help="hard-crash during subtask K before claiming (test only)")
    args = ap.parse_args()

    ledger_path = args.ledger
    agent, task_id = args.agent, args.task
    hb_timeout = args.heartbeat_timeout

    def subtask_list():
        if args.demo:
            return [{"subtask": i, "input": (i + 1) * 10} for i in range(5)]
        if args.subtasks:
            return json.loads(args.subtasks)
        ap.error("need --subtasks JSON or --demo to claim a task")

    # Claim the task if nobody has. Re-check right before appending so two
    # workers racing to claim don't both write (last write still wins;
    # duplicate identical claims are harmless).
    task, _, _, _ = task_state(task_id, ledger_path)
    if task is None:
        subs = subtask_list()
        task2, _, _, _ = task_state(task_id, ledger_path)
        if task2 is None:
            append(agent, {"op": "task", "task_id": task_id, "subtasks": subs},
                   {"created": True, "n": len(subs)}, ledger_path)
            log(agent, f"task {task_id} claimed ({len(subs)} subtasks)")

    deadline = time.time() + args.deadline
    done_by_me = 0
    while time.time() < deadline:
        task, results, beats, _ = task_state(task_id, ledger_path)
        remaining = [s for s in task["subtasks"] if s["subtask"] not in results]
        if not remaining:
            log(agent, "task complete")
            return 0

        now = time.time()
        live = [a for a, t in beats.items()
                if a != agent and now - t < hb_timeout]
        if live:
            # Someone else is working. Wait — don't steal live work.
            # Death is detected by staleness, not by announcement.
            log(agent, f"{live} alive, waiting...")
            time.sleep(0.5)
            continue

        # No live workers: take the next remaining subtask.
        s = remaining[0]
        i, w = s["subtask"], s["input"]
        heartbeat(agent, task_id, ledger_path)
        if args.crash_mid is not None and i == args.crash_mid:
            log(agent, f"CRASHING mid-subtask {i} (work lost, unclaimed)")
            os._exit(1)
        result = do_work(w)
        # Re-check before claiming: exactly-once *recording* under
        # at-least-once *execution*. If a peer claimed while we worked,
        # skip instead of double-recording.
        _, results2, _, _ = task_state(task_id, ledger_path)
        if i in results2:
            log(agent, f"subtask {i} already claimed, skipping")
            continue
        append(agent, {"op": "subresult", "task_id": task_id,
                       "subtask": i, "result": result},
               {"input": w}, ledger_path)
        log(agent, f"subtask {i} done -> {result}")
        done_by_me += 1
        if args.crash_after is not None and done_by_me >= args.crash_after:
            log(agent, f"CRASHING after {done_by_me} subtasks (hard, no cleanup)")
            os._exit(1)

    log(agent, "deadline exceeded waiting for task completion")
    return 2


if __name__ == "__main__":
    sys.exit(main())
