"""ledger.py — minimal append-only ledger with mandatory evidence.

The unit cell of ledger-continuity. Three operations:
  append(agent_id, claim, evidence) -> {"ok": True} | {"ok": False, "reason"}
  read_all()                       -> [entries]
  verify(verifier=None)            -> {"total": n, "bad": m}

Evidence is mandatory: a claim without evidence is rejected at append
time, not discovered later. File locking (fcntl where available) keeps
concurrent writers from interleaving lines.
"""
import json
import os
import time

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:  # non-POSIX platforms
    _HAS_FCNTL = False

LEDGER = os.environ.get(
    "LEDGER_CONTINUITY_LEDGER",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.jsonl"),
)


def _lock(f, exclusive):
    if _HAS_FCNTL:
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


def _unlock(f):
    if _HAS_FCNTL:
        fcntl.flock(f, fcntl.LOCK_UN)


def append(agent_id, claim, evidence, ledger_path=None):
    """Append a claim. Evidence is mandatory — no evidence, no append."""
    if not evidence:
        return {"ok": False, "reason": "no_evidence"}
    entry = {
        "t": time.time(),
        "agent": agent_id,
        "claim": claim,
        "evidence": evidence,
    }
    path = ledger_path or LEDGER
    with open(path, "a") as f:
        _lock(f, True)
        try:
            f.write(json.dumps(entry) + "\n")
            f.flush()
        finally:
            _unlock(f)
    return {"ok": True}


def read_all(ledger_path=None):
    """Read every entry. Returns [] for a missing ledger (not an error)."""
    path = ledger_path or LEDGER
    if not os.path.exists(path):
        return []
    with open(path) as f:
        _lock(f, False)
        try:
            rows = [json.loads(line) for line in f if line.strip()]
        finally:
            _unlock(f)
    return rows


def verify(verifier=None, ledger_path=None):
    """Re-verify every subresult claim. The ledger is the truth.

    verifier(result, evidence) -> True if the claim holds.
    Default verifier checks the demo work function (square of input);
    pass your own for real work.
    """
    if verifier is None:
        def verifier(result, evidence):
            inp = evidence.get("input")
            return inp is None or result == inp * inp
    bad = []
    entries = read_all(ledger_path)
    for e in entries:
        c = e.get("claim", {})
        if c.get("op") == "subresult" and "result" in c:
            try:
                holds = verifier(c["result"], e.get("evidence", {}))
            except Exception:
                holds = False
            if not holds:
                bad.append(e)
    return {"total": len(entries), "bad": len(bad)}
