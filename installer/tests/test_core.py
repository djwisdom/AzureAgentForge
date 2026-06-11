"""Offline tests for the Forge Console core — no az, terraform, or network."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer import core  # noqa: E402


# ---------------------------------------------------------------------------
# Config validation + rendering
# ---------------------------------------------------------------------------

GOOD = dict(subscription_id="12345678-1234-1234-1234-123456789abc")


def test_valid_config_passes():
    assert core.DeployConfig(**GOOD).validate() == []


@pytest.mark.parametrize("field,value,fragment", [
    ("subscription_id", "not-a-guid", "GUID"),
    ("location", "East US!", "region"),
    ("environment", "Prod-1", "environment"),
    ("profile", "cheap", "profile"),
    ("ai_foundry_endpoint", "http://insecure.example.com", "https"),
    ("owner_email", 'evil"injection', "email"),
])
def test_invalid_config_rejected(field, value, fragment):
    cfg = core.DeployConfig(**{**GOOD, field: value})
    errs = cfg.validate()
    assert errs and any(fragment in e for e in errs)


def _kv(out: str) -> dict:
    """Parse rendered tfvars into {key: raw_value} ignoring fmt alignment."""
    pairs = {}
    for line in out.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            pairs[k.strip()] = v.strip()
    return pairs


def test_render_tfvars_minimal():
    out = core.render_tfvars(core.DeployConfig(**GOOD))
    kv = _kv(out)
    assert kv["subscription_id"] == '"12345678-1234-1234-1234-123456789abc"'
    assert kv["telegram_enabled"] == "false"
    assert "ai_foundry_endpoint" not in kv  # optional, unset


def test_render_tfvars_optional_fields_and_quoting():
    cfg = core.DeployConfig(**GOOD, ai_foundry_endpoint="https://x.example.com/",
                            owner_email="ops@example.com", telegram_enabled=True)
    kv = _kv(core.render_tfvars(cfg))
    assert kv["ai_foundry_endpoint"] == '"https://x.example.com/"'
    assert kv["telegram_enabled"] == "true"


def test_render_tfvars_is_fmt_aligned():
    out = core.render_tfvars(core.DeployConfig(**GOOD))
    eq_cols = {line.index("=") for line in out.splitlines()
               if "=" in line and not line.startswith("#")}
    assert len(eq_cols) == 1, "all '=' must align for terraform fmt"


def test_write_config_creates_tfvars_and_backend_override(tmp_path):
    written = core.write_config(core.DeployConfig(**GOOD), tf_dir=tmp_path)
    assert (tmp_path / "terraform.tfvars").exists()
    backend = (tmp_path / "backend_override.tf").read_text()
    assert 'backend "local"' in backend
    assert written["profile_file"].endswith("cost-optimized.tfvars")


def test_write_config_refuses_invalid(tmp_path):
    with pytest.raises(ValueError):
        core.write_config(core.DeployConfig(subscription_id="nope"), tf_dir=tmp_path)
    assert not (tmp_path / "terraform.tfvars").exists()


# ---------------------------------------------------------------------------
# Step command allowlist
# ---------------------------------------------------------------------------

def test_known_steps_build_expected_commands():
    plan = core.build_step_command("plan", "cost-optimized")
    assert plan[0] == "terraform" and "-out=tfplan" in plan
    assert any(a.startswith("-var-file=") and a.endswith("cost-optimized.tfvars") for a in plan)
    apply_ = core.build_step_command("apply", "hardened")
    assert apply_[-1] == "tfplan"          # apply only ever runs the saved plan
    assert "-auto-approve" not in apply_


def test_unknown_step_rejected():
    with pytest.raises(ValueError):
        core.build_step_command("rm -rf /", "cost-optimized")


def test_dangerous_steps_flagged():
    assert "apply" in core.DANGEROUS_STEPS
    assert "destroy" in core.DANGEROUS_STEPS
    assert "plan" not in core.DANGEROUS_STEPS


# ---------------------------------------------------------------------------
# Runner — streaming, status, one-at-a-time
# ---------------------------------------------------------------------------

def _wait(runner, timeout=10.0):
    deadline = time.time() + timeout
    while runner.busy() and time.time() < deadline:
        time.sleep(0.02)
    assert not runner.busy(), "runner did not finish in time"


def test_runner_captures_output_and_status(tmp_path):
    r = core.Runner()
    r.start("echo-test", [sys.executable, "-c", "print('hello console')"], cwd=tmp_path)
    _wait(r)
    run = r.history[-1]
    assert run.status == "succeeded" and run.returncode == 0
    assert any("hello console" in ln for ln in run.lines)


def test_runner_reports_failure(tmp_path):
    r = core.Runner()
    r.start("fail-test", [sys.executable, "-c", "import sys; sys.exit(3)"], cwd=tmp_path)
    _wait(r)
    assert r.history[-1].status == "failed"
    assert r.history[-1].returncode == 3


def test_runner_rejects_concurrent_steps(tmp_path):
    r = core.Runner()
    r.start("slow", [sys.executable, "-c", "import time; time.sleep(1.5)"], cwd=tmp_path)
    with pytest.raises(RuntimeError):
        r.start("second", [sys.executable, "-c", "print('no')"], cwd=tmp_path)
    _wait(r)


def test_runner_handles_missing_binary(tmp_path):
    r = core.Runner()
    r.start("ghost", ["definitely-not-a-real-binary-xyz"], cwd=tmp_path)
    _wait(r)
    assert r.history[-1].status == "failed"


# ---------------------------------------------------------------------------
# API layer — guards (uses FastAPI TestClient when available)
# ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from installer import app as forge_app  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(forge_app.app)


def _h():
    return {"x-forge-token": forge_app.SESSION_TOKEN}


def test_api_requires_token(client):
    assert client.get("/api/checks").status_code == 401
    assert client.post("/api/run", json={"step": "plan"}).status_code == 401


def test_api_rejects_cross_origin(client):
    r = client.get("/api/state", headers={**_h(), "origin": "https://evil.example"})
    assert r.status_code == 403


def test_bootstrap_requires_url_token(client):
    assert client.get("/api/bootstrap").status_code == 401
    ok = client.get(f"/api/bootstrap?token={forge_app.SESSION_TOKEN}")
    assert ok.status_code == 200 and ok.json()["profiles"]


def test_run_requires_config_first(client):
    forge_app._last_config.clear()
    r = client.post("/api/run", json={"step": "plan"}, headers=_h())
    assert r.status_code == 409


def test_apply_requires_typed_confirmation(client):
    forge_app._last_config.update({"profile": "cost-optimized", "environment": "dev"})
    r = client.post("/api/run", json={"step": "apply", "confirm": "wrong"}, headers=_h())
    assert r.status_code == 428
    forge_app._last_config.clear()


def test_unknown_step_rejected_by_api(client):
    forge_app._last_config.update({"profile": "cost-optimized", "environment": "dev"})
    r = client.post("/api/run", json={"step": "weird"}, headers=_h())
    assert r.status_code == 422
    forge_app._last_config.clear()


def test_config_endpoint_preview(client):
    r = client.post("/api/config", headers=_h(), json={
        "subscription_id": GOOD["subscription_id"], "preview_only": True})
    assert r.status_code == 200
    assert r.json()["written"] is False
    assert "subscription_id" in r.json()["preview"]
