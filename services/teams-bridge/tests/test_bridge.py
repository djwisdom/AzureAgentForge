"""Offline tests for the Teams bridge — pure helpers + the endpoint contract.

No network: the PaperClip POST is swapped via the injectable `issue_poster`.
Run: pip install -r requirements-dev.txt && pytest
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(main.app)


# ── parse_activity ───────────────────────────────────────────────────────────

def test_parse_message_activity_extracts_fields():
    body = {
        "type": "message",
        "text": "  deploy the staging stack  ",
        "from": {"id": "29:abc", "name": "Ada"},
        "conversation": {"id": "19:meeting"},
        "serviceUrl": "https://smba.example/",
    }
    parsed = main.parse_activity(body)
    assert parsed == {
        "text": "deploy the staging stack",
        "user": "Ada",
        "conversation_id": "19:meeting",
        "service_url": "https://smba.example/",
    }


def test_parse_ignores_non_message_and_empty():
    assert main.parse_activity({"type": "typing"}) is None
    assert main.parse_activity({"type": "conversationUpdate"}) is None
    assert main.parse_activity({"type": "message", "text": "   "}) is None
    assert main.parse_activity("not a dict") is None
    assert main.parse_activity({}) is None


def test_parse_falls_back_to_id_then_default_for_user():
    assert main.parse_activity(
        {"type": "message", "text": "hi", "from": {"id": "29:x"}})["user"] == "29:x"
    assert main.parse_activity({"type": "message", "text": "hi"})["user"] == "teams-user"


# ── build_issue_payload ──────────────────────────────────────────────────────

def test_build_issue_payload_is_camelcase_and_tagged():
    parsed = {"text": "x" * 200, "user": "Ada", "conversation_id": "19:c"}
    p = main.build_issue_payload(parsed, "co-1", agent_id="agent-9")
    assert p["companyId"] == "co-1"           # camelCase, not company_id
    assert p["assigneeId"] == "agent-9"
    assert p["status"] == "todo"
    assert len(p["title"]) == 120             # truncated
    assert p["metadata"] == {"surface": "teams", "conversationId": "19:c"}
    assert "via Microsoft Teams — Ada" in p["description"]


def test_build_issue_payload_omits_assignee_when_unset():
    p = main.build_issue_payload({"text": "t", "user": "u", "conversation_id": ""}, "co")
    assert "assigneeId" not in p


# ── build_card ───────────────────────────────────────────────────────────────

def test_build_card_is_a_valid_adaptive_card_envelope():
    card = main.build_card("done ✅")
    att = card["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["content"]["type"] == "AdaptiveCard"
    assert att["content"]["body"][0]["text"] == "done ✅"


# ── endpoint contract ────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["surface"] == "teams"


def test_message_creates_issue_and_acks(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "issue_poster", lambda payload: seen.update(payload) or 201)
    r = client.post("/api/messages", json={
        "type": "message", "text": "run the audit", "from": {"name": "Ada"},
        "conversation": {"id": "19:c"}})
    assert r.status_code == 200 and r.json()["queued"] is True
    assert seen["title"] == "run the audit"


def test_non_message_is_ignored_with_200(monkeypatch):
    monkeypatch.setattr(main, "issue_poster", lambda p: (_ for _ in ()).throw(AssertionError("should not post")))
    r = client.post("/api/messages", json={"type": "typing"})
    assert r.status_code == 200 and r.json()["ignored"] is True


def test_poster_failure_never_5xxes_bot_framework(monkeypatch):
    def boom(_):
        raise RuntimeError("paperclip down")
    monkeypatch.setattr(main, "issue_poster", boom)
    r = client.post("/api/messages", json={
        "type": "message", "text": "hi", "conversation": {"id": "c"}})
    assert r.status_code == 200 and r.json()["queued"] is False
