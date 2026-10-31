from __future__ import annotations

import hashlib
import time
from typing import Any
from urllib.parse import urlparse

NOISE_DOMAINS = {
    "sentry.io",
    "wixpress.com",
    "example.com",
    "domain.com",
    "email.com",
    "cloudflare.com",
}


def _clean_domain(raw_value: str) -> str:
    candidate = (raw_value or "").strip().lower()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = "https://" + candidate
    host = (urlparse(candidate).hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or host in NOISE_DOMAINS:
        return ""
    return host


def _clean_emails(values: list[str], company_domain: str) -> list[str]:
    seen: set[str] = set()
    domain_matches: list[str] = []
    external: list[str] = []
    for raw in values:
        email = (raw or "").strip().lower()
        if "@" not in email or email in seen:
            continue
        if any( email.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif") ):
            continue
        host = email.split("@", 1)[1]
        if host in NOISE_DOMAINS:
            continue
        seen.add(email)
        if host == company_domain or host.endswith("." + company_domain):
            domain_matches.append(email)
        else:
            external.append(email)
    return domain_matches + external


def normalize_apify_lead_records(raw_items: list[dict[str, Any]], max_notes_chars: int = 500) -> dict[str, Any]:
    """Deduplicate and sanitize raw dataset items from `thanh81646` Apify B2B Actors."""
    seen_domains: set[str] = set()
    leads: list[dict[str, Any]] = []
    duplicates = 0
    invalid = 0

    for item in raw_items:
        domain = _clean_domain(str(item.get("domain") or item.get("website") or item.get("url") or ""))
        if not domain:
            invalid += 1
            continue
        if domain in seen_domains:
            duplicates += 1
            continue
        seen_domains.add(domain)

        raw_emails: list[str] = []
        if isinstance(item.get("emails"), list):
            raw_emails.extend(str(x) for x in item["emails"])
        if item.get("email"):
            raw_emails.append(str(item["email"]))

        emails = _clean_emails(raw_emails, domain)
        company_name = " ".join(str(item.get("companyName") or item.get("title") or domain).split())
        notes = " ".join(str(item.get("description") or item.get("summary") or "").split())[:max_notes_chars]
        idem = hashlib.sha256(f"lindy:{domain}:{emails[0] if emails else 'none'}".encode("utf-8")).hexdigest()[:24]

        leads.append(
            {
                "idempotency_key": idem,
                "domain": domain,
                "company_name": company_name,
                "primary_email": emails[0] if emails else None,
                "secondary_emails": emails[1:4],
                "phone": str(item.get("phone") or "").strip() or None,
                "country_code": str(item.get("countryCode") or "").strip().upper()[:2] or None,
                "notes": notes,
            }
        )

    return {
        "accepted_count": len(leads),
        "skipped_duplicates": duplicates,
        "skipped_invalid": invalid,
        "leads": leads,
    }


def build_close_crm_lead_payload(lead: dict[str, Any], source_tag: str = "apify-lindy-render") -> dict[str, Any]:
    """Format a normalized lead record for the Close CRM `/api/v1/lead/` endpoint."""
    contacts = []
    if lead.get("primary_email") or lead.get("phone"):
        contact_entry: dict[str, Any] = {"name": lead.get("contact_name") or "Operations Lead"}
        if lead.get("primary_email"):
            contact_entry["emails"] = [{"type": "office", "email": lead["primary_email"]}]
        if lead.get("phone"):
            contact_entry["phones"] = [{"type": "office", "phone": lead["phone"]}]
        contacts.append(contact_entry)

    return {
        "name": lead["company_name"],
        "url": f"https://{lead['domain']}",
        "description": lead.get("notes") or "",
        "contacts": contacts,
        "custom": {
            "source_pipeline": source_tag,
            "idempotency_key": lead["idempotency_key"],
        },
    }


def build_lindy_callback_envelope(
    *,
    action_run_id: str,
    agent_id: str,
    normalized_batch: dict[str, Any],
    signing_secret: str,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Build a signed async callback envelope ready to POST back to a Lindy AI Webhook Trigger."""
    ts = int(time.time()) if now_epoch is None else int(now_epoch)
    canonical = f"{ts}:{action_run_id}:{normalized_batch['accepted_count']}".encode("utf-8")
    sig = hashlib.sha256(signing_secret.encode("utf-8") + b":" + canonical).hexdigest()
    return {
        "event": "lindy.custom_action.completed",
        "action_run_id": action_run_id,
        "agent_id": agent_id,
        "timestamp": ts,
        "callback_signature": f"sha256={sig}",
        "summary": {
            "accepted_count": normalized_batch["accepted_count"],
            "skipped_duplicates": normalized_batch["skipped_duplicates"],
            "skipped_invalid": normalized_batch["skipped_invalid"],
        },
        "leads": normalized_batch["leads"],
        "close_crm_payloads": [build_close_crm_lead_payload(item) for item in normalized_batch["leads"]],
    }
