"""Teams bridge (AzureAgentForge) — Microsoft Teams chat surface.

A minimal Bot Framework messaging endpoint that bridges Microsoft Teams to the
agent platform, at parity with the Discord plugin and the Telegram gateway: an
inbound Teams message becomes a PaperClip issue routed to the Orchestrator, and
the agent's reply returns to the channel as an Adaptive Card. Disabled by
default; enable with the `teams_enabled` Terraform variable.

The parse / payload / card helpers are pure and unit-tested offline. The
PaperClip POST is injectable (`issue_poster`) so the endpoint is testable
without a live API, and the endpoint NEVER returns 5xx to Bot Framework (that
triggers an aggressive retry storm) — failures are acked with a body flag.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PAPERCLIP_API_URL = os.getenv("PAPERCLIP_API_URL", "http://paperclip:3000")
PAPERCLIP_COMPANY_ID = os.getenv("PAPERCLIP_COMPANY_ID", "")
PAPERCLIP_API_KEY = os.getenv("PAPERCLIP_API_KEY", "")
ORCHESTRATOR_AGENT_ID = os.getenv("ORCHESTRATOR_AGENT_ID", "")

app = FastAPI(title="teams-bridge", version="1.0.0")


def parse_activity(body: Any) -> Optional[dict]:
    """Pull the routable bits from a Bot Framework activity. Returns None for
    anything that isn't a non-empty `message` (typing, conversationUpdate, …),
    so those are silently acked and ignored."""
    if not isinstance(body, dict) or body.get("type") != "message":
        return None
    text = (body.get("text") or "").strip()
    if not text:
        return None
    frm = body.get("from") or {}
    conv = body.get("conversation") or {}
    return {
        "text": text,
        "user": frm.get("name") or frm.get("id") or "teams-user",
        "conversation_id": conv.get("id") or "",
        "service_url": body.get("serviceUrl") or "",
    }


def build_issue_payload(parsed: dict, company_id: str, agent_id: str = "") -> dict:
    """The camelCase PaperClip issue an inbound Teams message creates (camelCase
    matters — the API's validation drops snake_case fields)."""
    payload: dict = {
        "title": parsed["text"][:120],
        "description": (
            f"{parsed['text']}\n\n"
            f"_via Microsoft Teams — {parsed['user']} "
            f"(conversation `{parsed['conversation_id']}`)_"
        ),
        "status": "todo",
        "companyId": company_id,
        "metadata": {"surface": "teams", "conversationId": parsed["conversation_id"]},
    }
    if agent_id:
        payload["assigneeId"] = agent_id
    return payload


def build_card(text: str) -> dict:
    """A minimal Adaptive Card the bridge posts back to the Teams channel."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                },
            }
        ],
    }


def _post_issue(payload: dict) -> int:
    """Default issue poster — POST to the PaperClip API. Returns the status code."""
    headers = {"Content-Type": "application/json"}
    if PAPERCLIP_API_KEY:
        headers["Authorization"] = f"Bearer {PAPERCLIP_API_KEY}"
    resp = httpx.post(
        f"{PAPERCLIP_API_URL}/api/issues", json=payload, headers=headers, timeout=10.0
    )
    return resp.status_code


# Injectable for tests; production uses _post_issue.
issue_poster: Callable[[dict], int] = _post_issue


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "surface": "teams"}


@app.post("/api/messages")
async def messages(request: Request) -> JSONResponse:
    body = await request.json()
    parsed = parse_activity(body)
    if parsed is None:
        return JSONResponse({"ignored": True}, status_code=200)
    payload = build_issue_payload(parsed, PAPERCLIP_COMPANY_ID, ORCHESTRATOR_AGENT_ID)
    try:
        code = issue_poster(payload)
    except Exception:  # noqa: BLE001 — never 5xx to Bot Framework; it retry-storms
        return JSONResponse({"queued": False, "error": "bridge_post_failed"}, status_code=200)
    return JSONResponse({"queued": 200 <= code < 300, "issueStatus": code}, status_code=200)
