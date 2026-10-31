# render-fastapi-lindy-webhook-worker

Production **Render.com Blueprint (`render.yaml`)** deploying an ultra-lean (`< 50MB RAM`) **Python FastAPI** webhook sidecar and **Render Cron Job** for **Lindy AI** custom webhook actions, **Apify B2B Actors**, and **Close / Twenty CRM** pipelines.

## Architecture Included in `render.yaml`

1. **Web Service (`render-lindy-webhook-sidecar`):**
   - Zero-downtime `/healthz` readiness probe configured for Render Web Services.
   - `POST /v1/lindy/webhook`: Verifies `X-Lindy-Signature` (`HMAC-SHA256`) and `X-Lindy-Timestamp` (`300s` replay window), enforces `idempotency_key` deduplication, normalizes B2B company/contact records, and prepares signed async callbacks back to a Lindy Agent webhook trigger.
2. **Cron Job (`apify-b2b-lead-harvest-cron`, `0 */6 * * *`):**
   - Scheduled Python worker pulling new B2B lead datasets from commercial Apify Actors (`thanh81646/apify-b2b-lead-and-contact-enricher`), deduplicating domains, and dispatching clean batches to Lindy AI and Close CRM.
3. **Managed State (`render-lindy-idempotency-kv` + `render-lindy-audit-pg`):**
   - Render Key-Value (`allkeys-lru`) for fast webhook idempotency locks and Render PostgreSQL (`basic-256mb`) for delivery audit logs.

## Quick Verification

```bash
pip install -e ".[dev]"
pytest -v
```

## Deploy in 1 Click via Render Blueprints

Connect this repository inside the Render Dashboard (`New -> Blueprint`) and set `LINDY_WEBHOOK_SECRET`, `APIFY_API_TOKEN`, and `CLOSE_CRM_API_KEY`.

## Author

- **Trung Thanh Tran** (`ttrungthanh90@gmail.com`)
- Portfolio: https://trungthanhtran.vercel.app
- Commercial Apify Actors: https://apify.com/thanh81646
- GitHub: https://github.com/Tthanh2k6
