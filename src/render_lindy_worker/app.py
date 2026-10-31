from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .apify_enricher import build_lindy_callback_envelope, normalize_apify_lead_records
from .security import BoundedIdempotencyStore, verify_lindy_webhook_signature


class LindyWebhookPayload(BaseModel):
    action_run_id: str = Field(..., min_length=3, max_length=128)
    agent_id: str = Field(default="lindy_b2b_agent")
    idempotency_key: str | None = Field(default=None, max_length=128)
    callback_url: str | None = Field(default=None)
    raw_leads: list[dict[str, Any]] = Field(default_factory=list)


def create_app(
    *,
    webhook_secret: str | None = None,
    idempotency_store: BoundedIdempotencyStore | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Render FastAPI + Lindy AI Webhook Worker",
        version="0.1.0",
        docs_url="/docs",
    )
    store = idempotency_store or BoundedIdempotencyStore(
        max_entries=2048,
        ttl_seconds=86400,
        kv_url=os.getenv("IDEMPOTENCY_KV_URL"),
    )

    def _get_secret() -> str:
        return webhook_secret if webhook_secret is not None else os.getenv("LINDY_WEBHOOK_SECRET", "dev-secret-change-me")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "render-lindy-webhook-sidecar",
            "idempotency_backend": store.kv_url.split("://", 1)[0],
            "cached_keys": store.size(),
        }

    @app.post("/v1/lindy/webhook")
    async def handle_lindy_webhook(
        request: Request,
        x_lindy_signature: str = Header(default=""),
        x_lindy_timestamp: str = Header(default=""),
    ) -> dict[str, Any]:
        raw_body = await request.body()
        secret = _get_secret()
        ok, reason = verify_lindy_webhook_signature(
            secret=secret,
            signature_header=x_lindy_signature,
            timestamp_header=x_lindy_timestamp,
            raw_body=raw_body,
        )
        if not ok:
            raise HTTPException(status_code=401, detail={"error": reason})

        try:
            parsed = LindyWebhookPayload.model_validate(json.loads(raw_body.decode("utf-8")))
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_payload", "message": str(exc)}) from exc

        idem_key = parsed.idempotency_key or parsed.action_run_id
        cached = store.get(idem_key)
        if cached is not None:
            return {**cached, "deduplicated_replay": True}

        normalized = normalize_apify_lead_records(parsed.raw_leads)
        envelope = build_lindy_callback_envelope(
            action_run_id=parsed.action_run_id,
            agent_id=parsed.agent_id,
            normalized_batch=normalized,
            signing_secret=secret,
        )

        callback_dispatched = False
        if parsed.callback_url and parsed.callback_url.startswith("https://"):
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(parsed.callback_url, json=envelope)
                callback_dispatched = 200 <= resp.status_code < 300

        result = {
            "status": "processed",
            "deduplicated_replay": False,
            "idempotency_key": idem_key,
            "callback_dispatched": callback_dispatched,
            "envelope": envelope,
        }
        store.put(idem_key, result)
        return result

    return app


app = create_app()
