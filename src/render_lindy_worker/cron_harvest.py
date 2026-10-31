from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .apify_enricher import normalize_apify_lead_records
from .security import compute_hmac_sha256


def run_harvest_cycle(
    *,
    dataset_items: list[dict[str, Any]],
    lindy_ingest_url: str | None = None,
    webhook_secret: str = "dev-secret-change-me",
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Normalize harvested Apify dataset items and optionally push a signed batch to Lindy AI."""
    normalized = normalize_apify_lead_records(dataset_items)
    ts = str(int(time.time()))
    payload_bytes = json.dumps(
        {
            "action_run_id": f"cron_{ts}",
            "agent_id": "render_cron_harvester",
            "raw_leads": dataset_items,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signature = compute_hmac_sha256(webhook_secret, ts, payload_bytes)

    delivered = False
    if lindy_ingest_url:
        own_client = http_client is None
        client = http_client or httpx.Client(timeout=15.0)
        try:
            resp = client.post(
                lindy_ingest_url,
                content=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Lindy-Timestamp": ts,
                    "X-Lindy-Signature": signature,
                },
            )
            delivered = 200 <= resp.status_code < 300
        finally:
            if own_client:
                client.close()

    return {
        "accepted_count": normalized["accepted_count"],
        "skipped_duplicates": normalized["skipped_duplicates"],
        "delivered": delivered,
        "signature_prefix": signature[:15],
    }


def main() -> int:
    actor_id = os.getenv("APIFY_ACTOR_ID", "thanh81646/apify-b2b-lead-and-contact-enricher")
    token = os.getenv("APIFY_API_TOKEN", "")
    ingest_url = os.getenv("LINDY_INGEST_WEBHOOK_URL")
    secret = os.getenv("LINDY_WEBHOOK_SECRET", "dev-secret-change-me")

    items: list[dict[str, Any]] = []
    if token:
        api_slug = actor_id.replace("/", "~")
        url = f"https://api.apify.com/v2/acts/{api_slug}/runs/last/dataset/items"
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, params={"token": token, "clean": "true", "limit": 200})
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                items = data

    summary = run_harvest_cycle(
        dataset_items=items,
        lindy_ingest_url=ingest_url,
        webhook_secret=secret,
    )
    print(json.dumps({"actor": actor_id, **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
