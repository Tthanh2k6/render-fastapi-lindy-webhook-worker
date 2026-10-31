from __future__ import annotations

import json
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from render_lindy_worker.app import create_app
from render_lindy_worker.cron_harvest import run_harvest_cycle
from render_lindy_worker.security import compute_hmac_sha256

TEST_SECRET = "whsec_render_lindy_unit_test_2026"


def test_render_yaml_blueprint_schema_is_valid() -> None:
    blueprint_path = Path(__file__).resolve().parents[1] / "render.yaml"
    spec = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))

    services = {svc["name"]: svc for svc in spec["services"]}
    assert "render-lindy-webhook-sidecar" in services
    assert "apify-b2b-lead-harvest-cron" in services
    assert "render-lindy-idempotency-kv" in services

    web = services["render-lindy-webhook-sidecar"]
    assert web["type"] == "web"
    assert web["runtime"] == "python"
    assert web["healthCheckPath"] == "/healthz"

    cron = services["apify-b2b-lead-harvest-cron"]
    assert cron["type"] == "cron"
    assert cron["schedule"] == "0 */6 * * *"

    kv = services["render-lindy-idempotency-kv"]
    assert kv["type"] == "keyvalue"
    assert kv["maxmemoryPolicy"] == "allkeys-lru"

    dbs = {db["name"]: db for db in spec["databases"]}
    assert "render-lindy-audit-pg" in dbs


def test_healthz_endpoint() -> None:
    client = TestClient(create_app(webhook_secret=TEST_SECRET))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "render-lindy-webhook-sidecar"


def test_lindy_webhook_rejects_bad_or_expired_signatures() -> None:
    client = TestClient(create_app(webhook_secret=TEST_SECRET))
    raw = json.dumps({"action_run_id": "run_001", "raw_leads": []}).encode("utf-8")

    # Invalid HMAC signature
    now_ts = str(int(time.time()))
    resp_bad = client.post(
        "/v1/lindy/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Lindy-Timestamp": now_ts,
            "X-Lindy-Signature": "v1=deadbeef",
        },
    )
    assert resp_bad.status_code == 401
    assert resp_bad.json()["detail"]["error"] == "invalid_hmac_signature"

    # Expired replay timestamp (older than 300s)
    stale_ts = str(int(time.time()) - 900)
    stale_sig = compute_hmac_sha256(TEST_SECRET, stale_ts, raw)
    resp_stale = client.post(
        "/v1/lindy/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Lindy-Timestamp": stale_ts,
            "X-Lindy-Signature": stale_sig,
        },
    )
    assert resp_stale.status_code == 401
    assert resp_stale.json()["detail"]["error"] == "expired_timestamp_window"


def test_lindy_webhook_processes_leads_and_enforces_idempotency() -> None:
    client = TestClient(create_app(webhook_secret=TEST_SECRET))
    payload = {
        "action_run_id": "run_lindy_777",
        "agent_id": "lindy_outbound_sdr",
        "idempotency_key": "idem_batch_777",
        "raw_leads": [
            {
                "website": "https://www.Acme.example.com/services",
                "companyName": "Acme AG",
                "emails": ["noreply@sentry.io", "jane.doe@acme.example.com"],
                "phone": "+41 44 500 00 00",
                "countryCode": "ch",
                "description": "Swiss digital consultancy building automated workflows.",
            },
            {
                "domain": "acme.example.com",
                "companyName": "Duplicate Swiss Co",
                "email": "info@acme.example.com",
            },
            {
                "domain": "sentry.io",
                "companyName": "Noise Infrastructure Domain",
            },
        ],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    now_ts = str(int(time.time()))
    sig = compute_hmac_sha256(TEST_SECRET, now_ts, raw)
    headers = {
        "Content-Type": "application/json",
        "X-Lindy-Timestamp": now_ts,
        "X-Lindy-Signature": sig,
    }

    first = client.post("/v1/lindy/webhook", content=raw, headers=headers)
    assert first.status_code == 200
    body1 = first.json()
    assert body1["deduplicated_replay"] is False
    env = body1["envelope"]
    assert env["summary"] == {
        "accepted_count": 1,
        "skipped_duplicates": 1,
        "skipped_invalid": 1,
    }
    assert env["leads"][0]["domain"] == "acme.example.com"
    assert env["leads"][0]["primary_email"] == "jane.doe@acme.example.com"
    assert env["close_crm_payloads"][0]["url"] == "https://acme.example.com"

    # Replay with exact same idempotency_key returns cached result without re-running
    second = client.post("/v1/lindy/webhook", content=raw, headers=headers)
    assert second.status_code == 200
    assert second.json()["deduplicated_replay"] is True


def test_cron_harvest_dry_run_cycle() -> None:
    sample = [
        {"domain": "wellbayt.com", "companyName": "Wellbayt", "email": "ops@wellbayt.com"},
        {"domain": "wellbayt.com", "companyName": "Wellbayt Dup"},
    ]
    out = run_harvest_cycle(dataset_items=sample, lindy_ingest_url=None, webhook_secret=TEST_SECRET)
    assert out["accepted_count"] == 1
    assert out["skipped_duplicates"] == 1
    assert out["delivered"] is False
    assert out["signature_prefix"].startswith("v1=")
