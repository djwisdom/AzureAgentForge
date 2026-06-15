"""Failure-signature detectors (the testable core).

Pure functions: given a window of agent-run results and/or agent_events rows,
return a list of Finding objects. No I/O, no network — `watchdog.py` does the
poll and `filer.py` turns findings into PaperClip issues. Keeping the detection
logic here means the whole signature library is unit-testable offline.

Each detector encodes a failure mode an agent platform actually hits. Add a
detector when a new class of failure is worth surfacing to an operator.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional


@dataclass(frozen=True)
class Finding:
    """One detected problem worth an operator's attention."""
    signature: str          # stable key for dedup (same problem → same signature)
    severity: str           # critical | high | medium
    title: str              # issue title
    summary: str            # human-readable what+why
    evidence: dict          # structured facts (run ids, counts, error text)
    recommended_owner: str  # Infrastructure | Coder | Security | Orchestrator
    # Self-improvement loop: when a finding names a specific agent,
    # `subject_agent` is who the lesson is ABOUT (a display name as it appears in
    # run results, or a peer slug from agent_events) and `lesson` is the
    # durable_fact text persisted for that agent so its planner re-injects it.
    # Both are None on infra-level findings (e.g. stuck wakes).
    subject_agent: str | None = None
    lesson: str | None = None

    def dedup_key(self) -> str:
        return hashlib.sha256(self.signature.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Input shapes (duck-typed dicts — whatever the PaperClip API / events return)
# ---------------------------------------------------------------------------
# run result: {id, agentId, agentName, status, stopReason, result, startedAt,
#              finishedAt, model}
# event row:  {id, ts, actor_peer, event_type, channel, payload}

CRASH_STOP_REASONS = {"adapter_failed", "error", "timeout"}


def _ev(severity, signature, title, summary, evidence, owner,
        subject_agent=None, lesson=None):
    return Finding(signature=signature, severity=severity, title=title,
                   summary=summary, evidence=evidence, recommended_owner=owner,
                   subject_agent=subject_agent, lesson=lesson)


def detect_adapter_failures(runs: Iterable[dict], *, min_count: int = 1) -> list[Finding]:
    """Repeated adapter_failed runs for one agent.

    A single adapter failure can be transient; the same agent failing init
    repeatedly within a window is a deployment problem (missing dep, bad
    config, model/provider mismatch) that won't self-resolve.
    """
    by_agent: dict[str, list[dict]] = {}
    for r in runs:
        if r.get("stopReason") == "adapter_failed" or (
            r.get("status") == "failed" and r.get("stopReason") in CRASH_STOP_REASONS
        ):
            by_agent.setdefault(r.get("agentName") or r.get("agentId") or "?", []).append(r)

    out = []
    for agent, fails in by_agent.items():
        if len(fails) < min_count:
            continue
        sample = (fails[-1].get("result") or "").strip().splitlines()
        err = sample[0][:200] if sample else "no error text"
        # Signature is agent + error-class, not the run id, so retries of the
        # SAME failure collapse to one issue.
        owner = ("Infrastructure" if "package is required" in err or "pip install" in err
                 else "Coder" if "skill" in err.lower() or "adapter" in err.lower()
                 else "Security" if "auth" in err.lower() or "jwt" in err.lower()
                 else "Orchestrator")
        lesson = (
            f"Known failure pattern (auto-observed by the watchdog): you "
            f"('{agent}') hit repeated adapter/init failures ({len(fails)}× in a "
            f"~30-minute window). Representative error: {err}. Treat this as a "
            f"known platform issue — check the linked watchdog issue and your "
            f"environment/config before retrying the same call."
        )
        out.append(_ev(
            "critical", f"adapter-fail:{agent}:{err[:60]}",
            f"Agent '{agent}' failing init ({len(fails)}× in window)",
            f"{agent} had {len(fails)} failed runs with adapter/init errors. "
            f"Representative error: {err}",
            {"agent": agent, "count": len(fails),
             "run_ids": [r.get("id") for r in fails][:10], "error": err},
            owner, subject_agent=agent, lesson=lesson))
    return out


def detect_stuck_wakes(events: Iterable[dict], *, threshold: int = 3) -> list[Finding]:
    """Wake requests that never get claimed — a wake-worker hang.

    The platform's wake path is event-driven rows that a worker claims. When
    the worker hangs, rows pile up unclaimed and agents silently stop
    responding to assignments. Counts queued-but-unclaimed wake events.
    """
    queued = [e for e in events if e.get("event_type") in ("wakeup_queued", "agent_wakeup_requested")]
    claimed_ids = {e.get("payload", {}).get("wakeup_id")
                   for e in events if e.get("event_type") == "wakeup_claimed"}
    unclaimed = [e for e in queued if e.get("payload", {}).get("wakeup_id") not in claimed_ids]
    if len(unclaimed) < threshold:
        return []
    return [_ev(
        "high", "stuck-wakes",
        f"{len(unclaimed)} wake requests unclaimed — wake worker may be hung",
        f"{len(unclaimed)} wakeup events have no matching claim in the window. "
        f"Symptom matches a wake-worker hang (agents stop responding to "
        f"assignments while heartbeat still updates).",
        {"unclaimed_count": len(unclaimed),
         "oldest": min((e["ts"] for e in unclaimed if e.get("ts")), default=None)},
        "Infrastructure")]


def detect_budget_anomaly(runs: Iterable[dict], *, agent_caps: dict,
                          ratio: float = 0.9) -> list[Finding]:
    """An agent's spend approaching/exceeding its cap — CostGuardian's lane, automated.

    agent_caps: {agentName: monthly_cap_usd}. Sums cost_usd per agent over the
    window; flags any agent past `ratio` of its cap. Catches runaway loops
    before they exhaust the budget silently.
    """
    spend: dict[str, float] = {}
    for r in runs:
        c = r.get("cost_usd")
        if isinstance(c, (int, float)):
            spend[r.get("agentName") or "?"] = spend.get(r.get("agentName") or "?", 0.0) + c
    out = []
    for agent, total in spend.items():
        cap = agent_caps.get(agent)
        if cap and total >= cap * ratio:
            lesson = (
                f"Known failure pattern (auto-observed by the watchdog): you "
                f"('{agent}') spent ${total:.2f} of your ${cap:.2f} monthly budget "
                f"within a short window — a sign of a retry/loop. Be economical: "
                f"confirm a call is making progress before repeating it, and stop "
                f"and report a platform issue rather than looping."
            )
            out.append(_ev(
                "high", f"budget:{agent}",
                f"Agent '{agent}' at {total/cap:.0%} of monthly budget",
                f"{agent} has spent ${total:.2f} of its ${cap:.2f} cap in the "
                f"window. Investigate for a loop before the cap hard-stops it.",
                {"agent": agent, "spend_usd": round(total, 2), "cap_usd": cap},
                "CostGuardian", subject_agent=agent, lesson=lesson))
    return out


def detect_fabrication_signals(events: Iterable[dict], *, threshold: int = 1) -> list[Finding]:
    """Phantom-delegation / proof-of-source guard trips — the trust-burning class.

    When the orchestrator's close-parent --require-children guard refuses (exit 6)
    or a proof-of-source self-test fails, the runtime can emit an event. Any such
    event is worth surfacing — it means an agent tried to claim work it
    didn't do, and the guard caught it. Trend matters: rising = prompt drift.
    """
    trips = [e for e in events if e.get("event_type") in
             ("phantom_delegation_blocked", "proof_of_source_failed", "fabrication_guard_trip")]
    if len(trips) < threshold:
        return []
    by_agent: dict[str, int] = {}
    for e in trips:
        by_agent[e.get("actor_peer") or "?"] = by_agent.get(e.get("actor_peer") or "?", 0) + 1
    worst = max(by_agent, key=by_agent.get)
    lesson = (
        f"Known failure pattern (auto-observed by the watchdog): your ('{worst}') "
        f"outputs tripped the anti-fabrication / proof-of-source guard. Never "
        f"claim delegations, sources, or results you cannot prove — cite the "
        f"actual tool output. The guard rejects unproven claims."
    )
    return [_ev(
        "medium", "fabrication-guard",
        f"Anti-fabrication guard tripped {len(trips)}× in window",
        f"The phantom-delegation / proof-of-source guards fired {len(trips)} "
        f"times (worst: {worst}, {by_agent[worst]}×). The guard did its job, "
        f"but a rising rate signals prompt or model drift worth reviewing.",
        {"total": len(trips), "by_agent": by_agent},
        "Security", subject_agent=worst, lesson=lesson)]


def detect_stale_sync(last_sync_ts: Optional[datetime], *, now: datetime,
                      max_age_hours: int = 36) -> list[Finding]:
    """Standby-site sync freshness.

    A secondary/standby site syncs from the primary on a schedule and stamps a
    `site_sync_completed` event. This flags when the most recent one is older
    than max_age_hours (default 36h — a missed nightly plus margin) or has never
    happened. A silently-stopped sync means the standby is quietly rotting: a
    failover would then lose more than the intended window of state. Pure — the
    caller supplies the latest sync timestamp and `now`, and only invokes this
    when standby monitoring is enabled (so plain single-site deployments never
    see a false finding)."""
    if last_sync_ts is None:
        return [_ev(
            "high", "standby-sync:never",
            "Standby site: no completed sync on record",
            "Standby monitoring is on but no site_sync_completed event exists. "
            "The secondary site has never synced (or the sync job never ran) — "
            "a standby that never synced is not a standby.",
            {"max_age_hours": max_age_hours}, "Infrastructure")]
    age_h = (now - last_sync_ts).total_seconds() / 3600.0
    if age_h > max_age_hours:
        return [_ev(
            "high", "standby-sync:stale",
            f"Standby site: last sync {age_h:.0f}h ago (> {max_age_hours}h)",
            f"The standby-site sync last completed {age_h:.0f}h ago. The "
            "secondary site is drifting stale; a failover now would lose more "
            "state than intended. Check the sync job and network reachability "
            "to the primary database host.",
            {"last_sync": last_sync_ts.isoformat(), "age_hours": round(age_h, 1),
             "max_age_hours": max_age_hours}, "Infrastructure")]
    return []


ALL_DETECTORS = (
    detect_adapter_failures,
    detect_stuck_wakes,
    detect_budget_anomaly,
    detect_fabrication_signals,
    detect_stale_sync,
)


def run_detectors(runs: list[dict], events: list[dict],
                  agent_caps: Optional[dict] = None, *,
                  last_sync_ts: Optional[datetime] = None,
                  now: Optional[datetime] = None,
                  monitor_standby_sync: bool = False) -> list[Finding]:
    """Run every detector over the window; return all findings (caller dedups).

    Standby-site sync freshness is opt-in (`monitor_standby_sync=True`, set by
    watchdog.py when STANDBY_SYNC_MONITOR is configured) so plain deployments
    never file false sync-stale issues."""
    caps = agent_caps or {}
    findings: list[Finding] = []
    findings += detect_adapter_failures(runs)
    findings += detect_stuck_wakes(events)
    findings += detect_budget_anomaly(runs, agent_caps=caps)
    findings += detect_fabrication_signals(events)
    if monitor_standby_sync:
        findings += detect_stale_sync(last_sync_ts, now=now or datetime.now(timezone.utc))
    return findings


def dedup(findings: Iterable[Finding], seen_keys: set) -> list[Finding]:
    """Drop findings whose dedup_key is already in seen_keys; mutates seen_keys."""
    fresh = []
    for f in findings:
        k = f.dedup_key()
        if k in seen_keys:
            continue
        seen_keys.add(k)
        fresh.append(f)
    return fresh
