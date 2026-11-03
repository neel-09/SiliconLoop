"""Spending guard for the judging window: a hard cap, a paced allowance and per-visitor limits.

All three are checked before any route that calls Muse Spark Open. Settings (environment, with defaults):
  BUDGET_USD=70                 never spend more than this in total (including BUDGET_SPENT_BEFORE)
  BUDGET_SPENT_BEFORE=0         spend that is not in this server's cost log (e.g. local development)
  BUDGET_START / BUDGET_END     pacing window, ISO 8601 with offset (default Nov 4 01:30 to Nov 9 23:00 IST)
  BUDGET_BURST=8                dollars available immediately at the start of the window
  BUDGET_START_SPENT=2.72       total spend when the window opened
Pacing: the allowance grows in a straight line from (start spend + burst) at BUDGET_START to BUDGET_USD at
BUDGET_END, so unused budget carries forward and the cap can never be reached early.
"""
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

CAP = float(os.getenv("BUDGET_USD", "70"))
SPENT_BEFORE = float(os.getenv("BUDGET_SPENT_BEFORE", "0"))
START = datetime.fromisoformat(os.getenv("BUDGET_START", "2026-11-04T01:30:00+05:30")).timestamp()
END = datetime.fromisoformat(os.getenv("BUDGET_END", "2026-11-09T23:00:00+05:30")).timestamp()
BURST = float(os.getenv("BUDGET_BURST", "8"))
START_SPENT = float(os.getenv("BUDGET_START_SPENT", "2.72"))  # total spend when the window opened
IST = timezone(timedelta(hours=5, minutes=30))
HARD_MARGIN = 0.50  # model calls stop this far below the cap, so calls already in flight cannot cross it

# Worst-case-ish cost reserved per request kind (USD), and per-visitor limits: (max requests, window seconds).
COST = {"build": 0.15, "recognise": 0.02, "ideas": 0.03, "chat": 0.01}
LIMITS = {
    "build": [(6, 3600), (20, 86400)],
    "recognise": [(20, 3600), (60, 86400)],
    "ideas": [(10, 3600), (30, 86400)],
    "chat": [(30, 3600), (120, 86400)],
}
MAX_CONCURRENT_BUILDS = 3


class BudgetExceeded(ValueError):
    """Raised instead of making a model call once the hard cap is reached."""


def hard_stop(spent):
    """Called by llm.chat() before every model call."""
    if spent + SPENT_BEFORE >= CAP - HARD_MARGIN:
        raise BudgetExceeded("SiliconLoop has used its AI budget for the judging period, so new AI steps are paused. "
                             "Shared builds and the simulator still work.")


def allowance(now=None):
    """Total dollars (including earlier spend) that may have been spent by now."""
    now = time.time() if now is None else now
    frac = min(max((now - START) / (END - START), 0.0), 1.0)
    base = min(START_SPENT + BURST, CAP)
    return min(CAP, base + (CAP - base) * frac)


def rate_per_s():
    base = min(START_SPENT + BURST, CAP)
    return (CAP - base) / max(END - START, 1)


def status(spent_logged):
    spent = spent_logged + SPENT_BEFORE
    return {"cap_usd": CAP, "spent_usd": round(spent, 4), "allowed_now_usd": round(allowance(), 4),
            "available_now_usd": round(max(0.0, allowance() - spent), 4),
            "pace_usd_per_hour": round(rate_per_s() * 3600, 4),
            "window": [datetime.fromtimestamp(START, IST).isoformat(), datetime.fromtimestamp(END, IST).isoformat()],
            "builds_running": MAX_CONCURRENT_BUILDS - _build_slots._value}


_hits = defaultdict(deque)  # (kind, client) -> request timestamps
_lock = threading.Lock()
_build_slots = threading.BoundedSemaphore(MAX_CONCURRENT_BUILDS)


def client_id(request: Request):
    """Visitor IP. Cloud Run appends the real client address as the last X-Forwarded-For entry; earlier entries
    are supplied by the client and could be forged, so they are ignored."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff.strip():
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _wait_text(seconds):
    m = max(1, round(seconds / 60))
    return f"about {m} minute{'s' if m != 1 else ''}" if m < 90 else f"about {round(m / 60)} hours"


def check(request: Request, kind: str, spent_logged: float):
    """Raise 429/503 if this request would break the cap, the pace or the visitor's limit; otherwise record it."""
    spent = spent_logged + SPENT_BEFORE
    if spent >= CAP - HARD_MARGIN:
        raise HTTPException(503, "SiliconLoop has used its AI budget for the judging period, so new AI steps are "
                                 "paused. Shared builds and the simulator still work.")
    need = spent + COST[kind] - allowance()
    if need > 0:
        wait = need / max(rate_per_s(), 1e-9)
        raise HTTPException(429, f"SiliconLoop is pacing its AI budget so it lasts through judging. "
                                 f"Please try again in {_wait_text(wait)}.", headers={"Retry-After": str(int(wait) + 1)})
    who, now = client_id(request), time.time()
    with _lock:
        q = _hits[(kind, who)]
        longest = max(w for _, w in LIMITS[kind])
        while q and now - q[0] > longest:
            q.popleft()
        for n, window in LIMITS[kind]:
            recent = [t for t in q if now - t < window]
            if len(recent) >= n:
                wait = window - (now - recent[0])
                what = {"build": "builds", "recognise": "photo uploads", "ideas": "idea requests",
                        "chat": "assistant messages"}[kind]
                raise HTTPException(429, f"You've reached the limit of {n} {what} per "
                                         f"{'hour' if window == 3600 else 'day'}. Please try again in {_wait_text(wait)}.",
                                    headers={"Retry-After": str(int(wait) + 1)})
        q.append(now)


def take_build_slot():
    if not _build_slots.acquire(blocking=False):
        raise HTTPException(429, "Several gadgets are being built right now. Please try again in a minute.",
                            headers={"Retry-After": "60"})


def release_build_slot():
    _build_slots.release()
