"""The watchdog orchestrator loop (run as a cron job or a small worker).

Pulls a window of run results from the PaperClip API and recent rows from
agent_events, runs the detector library, dedups against a persisted key store,
and files an issue per fresh finding. One pass per invocation — wire it to a
10-minute cron (the platform's existing scheduler) rather than a long-lived
daemon, so a crash is self-healing on the next tick.

GATING: does nothing unless AGENT_EVENTS_ENABLED is true. The events spine
ships seeded OFF; flipping the flag is the deliberate go-live.

Env:
  WATCHDOG_BASE_URL       PaperClip base (default https://app.example.com)
  WATCHDOG_COMPANY_ID     company uuid
  WATCHDOG_JWT            admin/automation JWT (or mint one from the secret below)
  AGENT_EVENTS_DSN        Postgres DSN for agent_events (psycopg)
  WATCHDOG_STATE          dedup key-store path (default /tmp/watchdog-seen.json)
  WATCHDOG_WINDOW_MIN     lookback minutes (default 30)

Self-improvement loop — optional; all three required to persist failure
lessons. Absent → issues are filed but no lessons are written:
  GOVERNOR_BASE_URL       memory-governor base (e.g. http://memory-governor:8090)
  GOVERNOR_API_KEY        shared X-Governor-Key (KV: memory-governor-api-key)
  GOVERNOR_WORKSPACE      governed-memory workspace name (e.g. default)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from . import attribution, detectors, filer, memory, scorecards

DEFAULT_BASE = "https://app.example.com"
# Per-agent monthly caps in dollars (illustrative defaults).
DEFAULT_CAPS = {"Orchestrator": 15.00, "Researcher": 7.50}

# ── Automation JWT (self-minted at runtime) ──────────────────────────────────
# As a scheduled job the watchdog has no operator to paste a token, so it mints
# a short-lived HS256 automation JWT from the shared signing secret — the same
# scheme governor.scope_watcher uses. The sub attributes filed issues + lesson
# writes to the watchdog; scope covers issue create/read.
JWT_ISSUER = os.getenv("PAPERCLIP_AUTOMATION_JWT_ISSUER", "automation-agent")
JWT_AUDIENCE = os.getenv("PAPERCLIP_AUTOMATION_JWT_AUDIENCE", "paperclip-api")
WATCHDOG_JWT_SUB = os.getenv("WATCHDOG_JWT_SUB", "watchdog")
WATCHDOG_JWT_SCOPE = ["issues:read", "issues:write", "agents:read"]


def _automation_jwt_secret() -> str | None:
    val = os.getenv("PAPERCLIP_AUTOMATION_JWT_SECRET")
    if val:
        return val
    p = Path("/secrets/platform-paperclip-automation-jwt-secret")
    return p.read_text().strip() if p.exists() else None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_jwt(secret: str, *, ttl_s: int = 900) -> str:
    """Minimal HS256 automation JWT matching the auth-proxy's verifyJwt
    (mirrors governor.scope_watcher.mint_jwt)."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps(
            {
                "sub": WATCHDOG_JWT_SUB,
                "role": "automation",
                "scope": WATCHDOG_JWT_SCOPE,
                "iss": JWT_ISSUER,
                "aud": JWT_AUDIENCE,
                "iat": now,
                "exp": now + ttl_s,
            }
        ).encode()
    )
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def _normalize_dsn(dsn: str) -> str:
    """KV stores a SQLAlchemy-style URL; psycopg2 wants a plain libpq URI."""
    return (
        dsn.replace("postgresql+psycopg:", "postgresql:")
        .replace("postgresql+asyncpg:", "postgresql:")
    )


def _flag_enabled(dsn: str) -> bool:
    try:
        import psycopg2
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            cur.execute("select enabled from feature_flags where name = 'AGENT_EVENTS_ENABLED'")
            row = cur.fetchone()
            return bool(row and row[0])
    except Exception as e:
        print(f"[watchdog] flag check failed ({e}); refusing to run", file=sys.stderr)
        return False


def _fetch_runs(base, company_id, jwt, window_min) -> list[dict]:
    url = f"{base}/api/companies/{company_id}/runs?sinceMinutes={window_min}&limit=200"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {jwt}")
    req.add_header("Origin", base)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read() or "[]")
            return data if isinstance(data, list) else data.get("runs", [])
    except Exception as e:
        print(f"[watchdog] run fetch failed: {e}", file=sys.stderr)
        return []


def _fetch_events(dsn, window_min) -> list[dict]:
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with psycopg2.connect(dsn) as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "select id, ts, actor_peer, event_type, channel, payload "
                "from agent_events where ts > now() - (%s || ' minutes')::interval",
                (window_min,))
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[watchdog] event fetch failed: {e}", file=sys.stderr)
        return []


def _fetch_last_sync(dsn):
    """Latest site_sync_completed ts (None if never) — feeds detect_stale_sync.
    Queried separately from the windowed events because a daily sync would fall
    outside the normal short event window."""
    try:
        import psycopg2
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            cur.execute("select max(ts) from agent_events where event_type = 'site_sync_completed'")
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"[watchdog] last-sync fetch failed: {e}", file=sys.stderr)
        return None


def _load_seen(path) -> set:
    p = Path(path)
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception:
            return set()
    return set()


def _save_seen(path, seen) -> None:
    Path(path).write_text(json.dumps(sorted(seen)))


def main() -> int:
    base = os.getenv("WATCHDOG_BASE_URL", DEFAULT_BASE)
    company = os.getenv("WATCHDOG_COMPANY_ID", "")
    jwt = os.getenv("WATCHDOG_JWT", "")
    if not jwt:
        secret = _automation_jwt_secret()
        if secret:
            jwt = mint_jwt(secret)
    dsn = _normalize_dsn(os.getenv("AGENT_EVENTS_DSN", ""))
    state = os.getenv("WATCHDOG_STATE", "/tmp/watchdog-seen.json")
    window = int(os.getenv("WATCHDOG_WINDOW_MIN", "30"))
    gov_base = os.getenv("GOVERNOR_BASE_URL", "")
    gov_key = os.getenv("GOVERNOR_API_KEY", "")
    gov_ws = os.getenv("GOVERNOR_WORKSPACE", "")
    lessons_on = bool(gov_base and gov_key and gov_ws)

    if not (company and jwt and dsn):
        print("[watchdog] need WATCHDOG_COMPANY_ID + AGENT_EVENTS_DSN and either "
              "WATCHDOG_JWT or PAPERCLIP_AUTOMATION_JWT_SECRET", file=sys.stderr)
        return 2
    if not _flag_enabled(dsn):
        print("[watchdog] AGENT_EVENTS_ENABLED is false — nothing to do.")
        return 0

    runs = _fetch_runs(base, company, jwt, window)
    events = _fetch_events(dsn, window)
    # Standby secondary-site sync freshness is opt-in (only when a standby
    # exists), so plain single-site deployments never file false sync-stale
    # issues.
    standby_monitor = os.getenv("STANDBY_SYNC_MONITOR", "").lower() in ("1", "true", "yes")
    last_sync = _fetch_last_sync(dsn) if standby_monitor else None
    print(f"[watchdog] window={window}m runs={len(runs)} events={len(events)} "
          f"standby_monitor={standby_monitor}")

    findings = detectors.run_detectors(runs, events, agent_caps=DEFAULT_CAPS,
                                       last_sync_ts=last_sync, monitor_standby_sync=standby_monitor)
    seen = _load_seen(state)
    fresh = detectors.dedup(findings, seen)
    print(f"[watchdog] findings={len(findings)} fresh={len(fresh)}")

    filed = 0
    lessons = 0
    for f in fresh:
        try:
            filer.file_finding(f, base_url=base, company_id=company, jwt=jwt)
            print(f"[watchdog] filed [{f.severity}] {f.title}")
            filed += 1
        except Exception as e:
            print(f"[watchdog] file failed for {f.title}: {e}", file=sys.stderr)
            seen.discard(f.dedup_key())  # allow retry next tick
            continue

        # Self-improvement loop: persist the failure lesson for the named agent.
        # Fail-soft — a lesson-write failure never un-files the issue or disturbs
        # the dedup store. Returns None (skipped) for infra-level findings with no
        # agent subject.
        if lessons_on:
            try:
                verdict = memory.write_lesson(
                    f, base_url=gov_base, key=gov_key, workspace=gov_ws
                )
                if verdict is not None:
                    status = verdict.get("status", "?")
                    print(f"[watchdog] lesson[{status}] {f.subject_agent}: {f.signature}")
                    if status == "admitted":
                        lessons += 1
            except Exception as e:
                print(f"[watchdog] lesson write failed for {f.title}: {e}", file=sys.stderr)

    # Use-based earned trust: credit memories injected into runs that reached
    # terminal success. Matched by agent + time window (runs carry no issue id).
    # Deduped via the same seen-store; fail-soft.
    reconfirmed = 0
    if gov_base and gov_key:
        try:
            for c in attribution.attribute_successes(runs, events):
                ckey = f"reconfirm:{c['run_id']}:{c['doc_id']}"
                if ckey in seen:
                    continue
                seen.add(ckey)
                try:
                    memory.reconfirm_memory(
                        c["doc_id"], c["run_id"], base_url=gov_base, key=gov_key
                    )
                    reconfirmed += 1
                except Exception as e:
                    print(f"[watchdog] reconfirm failed for {c['doc_id']}: {e}", file=sys.stderr)
                    seen.discard(ckey)  # allow retry next tick
        except Exception as e:
            print(f"[watchdog] success-attribution failed: {e}", file=sys.stderr)

    # Track-record routing — recompute per-agent delegation scorecards from a
    # LONGER run window (the 30m findings window is too short to score) and
    # upsert them as durable_facts on the orchestrator's scope so its planner
    # injects them at delegation time. Opt-in; fail-soft. Cadence-bounded by a
    # marker in the seen-store so we don't rewrite scorecards every tick.
    scored = 0
    track_routing = os.getenv("TRACK_RECORD_ROUTING", "").lower() in ("1", "true", "yes")
    score_key = f"scorecards:{int(time.time()) // (int(os.getenv('TRACK_RECORD_EVERY_HOURS', '24')) * 3600)}"
    if track_routing and lessons_on and score_key not in seen:
        try:
            days = int(os.getenv("TRACK_RECORD_WINDOW_DAYS", "14"))
            hist = _fetch_runs(base, company, jwt, days * 24 * 60)
            cards = scorecards.compute_scorecards(hist)
            scored = memory.write_scorecards(cards, base_url=gov_base, key=gov_key, workspace=gov_ws)
            seen.add(score_key)
            print(f"[watchdog] track-record: {len(cards)} scorecard(s) from {len(hist)} runs/{days}d → {scored} written")
        except Exception as e:
            print(f"[watchdog] track-record routing failed: {e}", file=sys.stderr)

    _save_seen(state, seen)
    suffix = f", {lessons} lesson(s) persisted" if lessons_on else ""
    if reconfirmed:
        suffix += f", {reconfirmed} memory reconfirm(s)"
    if scored:
        suffix += f", {scored} scorecard(s)"
    print(f"[watchdog] done — {filed} issue(s) filed{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
