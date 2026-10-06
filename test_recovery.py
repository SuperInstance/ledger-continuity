#!/usr/bin/env python3
"""test_recovery.py — prove it: kill agents mid-task, watch strangers finish.

Three variants, separate OS processes, zero direct messaging. The only
shared state is the ledger file.

    python3 test_recovery.py

Exit 0: all variants pass. Non-zero: something broke — read the output.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "worker.py")
sys.path.insert(0, HERE)
from ledger import read_all, verify

HB_TIMEOUT = "2.0"  # death detection threshold (seconds)
SUBTASKS = [{"subtask": i, "input": (i + 1) * 10} for i in range(5)]


def run_worker(agent, task_id, ledger, crash_after=None, crash_mid=None):
    cmd = [sys.executable, WORKER, agent, "--task", task_id,
           "--ledger", ledger,
           "--subtasks", json.dumps(SUBTASKS),
           "--heartbeat-timeout", HB_TIMEOUT,
           "--deadline", "60"]
    if crash_after is not None:
        cmd += ["--crash-after", str(crash_after)]
    if crash_mid is not None:
        cmd += ["--crash-mid", str(crash_mid)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def results_of(ledger, task_id):
    out = {}
    for e in read_all(ledger):
        c = e.get("claim", {})
        if c.get("op") == "subresult" and c.get("task_id") == task_id:
            out[c["subtask"]] = (c["result"], e["agent"])
    return out


def wait_for(ledger, task_id, n=5, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(results_of(ledger, task_id)) >= n:
            return True
        time.sleep(0.3)
    return False


def check(label, ledger, task_id, expect_agents=None):
    res = results_of(ledger, task_id)
    assert len(res) == 5, f"{label}: only {len(res)}/5 subresults"
    for s in SUBTASKS:
        i, w = s["subtask"], s["input"]
        got, who = res[i]
        assert got == w * w, f"{label}: subtask {i} = {got}, want {w*w}"
    # every subresult carries evidence with the input
    for e in read_all(ledger):
        c = e.get("claim", {})
        if c.get("op") == "subresult" and c.get("task_id") == task_id:
            assert e.get("evidence", {}).get("input") is not None, \
                f"{label}: subresult without evidence"
    # ledger re-verifies clean
    v = verify(ledger_path=ledger)
    assert v["bad"] == 0, f"{label}: ledger verify found {v['bad']} bad claims"
    if expect_agents:
        who = sorted({a for _, a in res.values()})
        assert who == sorted(expect_agents), \
            f"{label}: workers {who}, want {sorted(expect_agents)}"
    print(f"PASS {label}: 5/5 correct, all evidenced, ledger verifies clean",
          flush=True)


def test_clean_crash():
    """A claims 5, does 2, hard-crashes. B (started while A lived) waits
    out the heartbeat, then finishes the 3 remaining from ledger alone."""
    with tempfile.TemporaryDirectory() as d:
        ledger = os.path.join(d, "ledger.jsonl")
        a = run_worker("A", "T1", ledger, crash_after=2)
        time.sleep(0.4)  # B starts while A is still alive: must wait, not steal
        b = run_worker("B", "T1", ledger)
        assert a.wait(timeout=20) == 1, "A should have crashed (exit 1)"
        assert b.wait(timeout=40) == 0, "B should finish cleanly (exit 0)"
        assert wait_for(ledger, "T1"), "clean crash: task never completed"
        check("clean crash", ledger, "T1", expect_agents=["A", "B"])


def test_mid_subtask_crash():
    """A crashes DURING subtask 2, before claiming it. B finds only 2
    claimed results, redoes subtask 2 idempotently, finishes the rest."""
    with tempfile.TemporaryDirectory() as d:
        ledger = os.path.join(d, "ledger.jsonl")
        a = run_worker("A", "T2", ledger, crash_mid=2)
        time.sleep(0.4)
        b = run_worker("B", "T2", ledger)
        assert a.wait(timeout=20) == 1, "A should have crashed (exit 1)"
        assert b.wait(timeout=40) == 0, "B should finish cleanly (exit 0)"
        assert wait_for(ledger, "T2"), "mid-subtask crash: task never completed"
        res = results_of(ledger, "T2")
        assert res[2][1] == "B", "subtask 2 should have been redone by B"
        check("mid-subtask crash", ledger, "T2", expect_agents=["A", "B"])


def test_chained_recovery():
    """A does 2 and dies. B takes over, does 1, dies. C finishes.
    Same code path every time — recovery is recursive by construction."""
    with tempfile.TemporaryDirectory() as d:
        ledger = os.path.join(d, "ledger.jsonl")
        a = run_worker("A", "T3", ledger, crash_after=2)
        time.sleep(0.4)
        b = run_worker("B", "T3", ledger, crash_after=1)
        assert a.wait(timeout=20) == 1, "A should have crashed (exit 1)"
        assert b.wait(timeout=40) == 1, "B should have crashed (exit 1)"
        # C starts after B's death; must wait out B's heartbeat, then finish
        time.sleep(1.0)
        c = run_worker("C", "T3", ledger)
        assert c.wait(timeout=40) == 0, "C should finish cleanly (exit 0)"
        assert wait_for(ledger, "T3"), "chained: task never completed"
        check("chained A->B->C", ledger, "T3", expect_agents=["A", "B", "C"])


def main():
    print("ledger-continuity recovery tests", flush=True)
    print("=" * 50, flush=True)
    test_clean_crash()
    test_mid_subtask_crash()
    test_chained_recovery()
    print("=" * 50, flush=True)
    print("ALL RECOVERY TESTS PASS", flush=True)


if __name__ == "__main__":
    main()
