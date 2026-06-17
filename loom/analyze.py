from __future__ import annotations

from typing import Any
import json
import re

from .ai import QwenClient
from .model import ExtractionResult, SourceDocument, now_iso

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s)\]>]+")
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
_DATE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b", re.I)
_INVOICE_ID = re.compile(r"\b(?:invoice(?:\s+(?:id|no\.?|number))?|inv(?:oice)?[-_\s]?(?:id|no\.?)?)\s*[:=#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", re.I)
_VENDOR = re.compile(r"\bvendor\s*[:=]\s*([^|\n;]+)", re.I)
_TOTAL = re.compile(r"\b(?:total(?:\s+amount)?|amount\s+due|invoice\s+total)\s*[:=]?\s*(\$?\s?\d[\d,]*(?:\.\d{2})?)", re.I)

REQUIRED_INVOICE_FIELDS = ["vendor", "invoice_id", "total_amount", "invoice_date", "due_date", "department", "payment_terms"]


def _uniq(values: list[str], limit: int = 80) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _norm_key(key: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    aliases = {
        "invoice": "invoice_id",
        "invoice_no": "invoice_id",
        "invoice_number": "invoice_id",
        "invoice_id": "invoice_id",
        "inv": "invoice_id",
        "vendor_name": "vendor",
        "supplier": "vendor",
        "payee": "vendor",
        "amount": "total_amount",
        "amount_due": "total_amount",
        "invoice_total": "total_amount",
        "total_usd": "total_amount",
        "total": "total_amount",
        "payment_due": "due_date",
        "due": "due_date",
        "cost_center": "cost_center",
        "gl": "gl_code",
        "gl_code": "gl_code",
        "approver_name": "approver",
        "approval_status": "approval_status",
        "campaign_project": "campaign",
        "campaign_project_code": "campaign",
        "po": "po_number",
        "po_number": "po_number",
    }
    return aliases.get(k, k)


def _parse_pairs(line: str) -> dict[str, str]:
    if ":" not in line or "|" not in line:
        return {}
    _, rest = line.split(":", 1)
    pairs: dict[str, str] = {}
    for chunk in rest.split("|"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = _norm_key(key)
        value = value.strip()
        if key and value:
            pairs[key] = value
    return pairs


def invoice_rows(text: str, limit: int = 500) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        pairs = _parse_pairs(line)
        if not pairs:
            continue
        keys = set(pairs)
        if not (keys & {"vendor", "invoice_id", "total_amount", "due_date", "invoice_date", "department", "payment_terms", "po_number"}):
            continue
        row: dict[str, Any] = dict(pairs)
        if "total_amount" in row:
            row["total_amount_numeric"] = _num(row["total_amount"])
        rows.append(row)
        if len(rows) >= limit:
            break
    if rows:
        return rows

    ids = _uniq(_INVOICE_ID.findall(text), 100)
    vendors = _uniq(_VENDOR.findall(text), 100)
    totals = _uniq(_TOTAL.findall(text), 100)
    dates = _uniq(_DATE.findall(text), 100)
    count = max(len(ids), len(vendors), len(totals), 0)
    for idx in range(count):
        row: dict[str, Any] = {}
        if idx < len(ids):
            row["invoice_id"] = ids[idx]
        if idx < len(vendors):
            row["vendor"] = vendors[idx]
        if idx < len(totals):
            row["total_amount"] = totals[idx]
            row["total_amount_numeric"] = _num(totals[idx])
        if idx < len(dates):
            row["invoice_date"] = dates[idx]
        if row:
            rows.append(row)
    return rows


def deterministic_profile(doc: SourceDocument, schema_name: str = "universal_document") -> dict[str, Any]:
    text = doc.text
    rows = invoice_rows(text)
    totals = [row.get("total_amount_numeric") for row in rows if isinstance(row.get("total_amount_numeric"), (int, float))]
    vendors = _uniq([str(row.get("vendor", "")) for row in rows if row.get("vendor")], 120)
    invoice_ids = _uniq([str(row.get("invoice_id", "")) for row in rows if row.get("invoice_id")], 200)
    first = rows[0] if rows else {}
    workbook = {}
    if doc.metadata.get("workbook_type") == "excel":
        workbook = {
            "type": "excel_workbook",
            "sheet_count": doc.metadata.get("sheet_count", 0),
            "rows_with_data": doc.metadata.get("rows_with_data", 0),
            "cells_with_data": doc.metadata.get("cells_with_data", 0),
            "sheets": doc.metadata.get("sheets", []),
        }

    canonical: dict[str, Any] = {
        "document_name": doc.name,
        "document_type": doc.metadata.get("workbook_type") or doc.suffix.lstrip(".") or "unknown",
        "schema": schema_name,
        "sha256": doc.sha256,
        "vendor": first.get("vendor") or (vendors[0] if vendors else None),
        "invoice_id": first.get("invoice_id") or (invoice_ids[0] if invoice_ids else None),
        "total_amount": first.get("total_amount"),
        "invoice_date": first.get("invoice_date"),
        "due_date": first.get("due_date"),
        "department": first.get("department"),
        "campaign": first.get("campaign"),
        "po_number": first.get("po_number"),
        "cost_center": first.get("cost_center"),
        "gl_code": first.get("gl_code"),
        "payment_terms": first.get("payment_terms"),
        "approver": first.get("approver"),
        "invoice_record_count": len(rows),
        "vendors": vendors,
        "invoice_ids": invoice_ids,
        "total_amount_sum": round(sum(float(x) for x in totals), 2) if totals else None,
    }
    return {
        "schema": schema_name,
        "summary": _summary(doc.name, rows, text),
        "canonical_fields": {k: v for k, v in canonical.items() if v not in (None, "", [], {})},
        "records": {"invoice_rows": rows},
        "entities": {
            "emails": _uniq(_EMAIL.findall(text)),
            "urls": _uniq(_URL.findall(text)),
            "money": _uniq(_MONEY.findall(text), 200),
            "dates": _uniq(_DATE.findall(text), 240),
            "headings": [line.strip() for line in text.splitlines() if line.strip() and (line.isupper() or line.endswith(":"))][:60],
        },
        "workbook": workbook,
        "source_fidelity": {
            "source_name": doc.name,
            "sha256": doc.sha256,
            "size_bytes": doc.size_bytes,
            "line_count": len(text.splitlines()),
            "char_count": len(text),
            "extractor": doc.metadata,
        },
    }


def _summary(name: str, rows: list[dict[str, Any]], text: str) -> str:
    if rows:
        totals = [row.get("total_amount_numeric") for row in rows if isinstance(row.get("total_amount_numeric"), (int, float))]
        vendors = _uniq([str(row.get("vendor", "")) for row in rows if row.get("vendor")], 8)
        amount = f" totaling ${sum(float(x) for x in totals):,.2f}" if totals else ""
        vendor_text = f" Vendors include {', '.join(vendors[:5])}." if vendors else ""
        return f"{name} contains {len(rows)} invoice-like record(s){amount}.{vendor_text}"
    clipped = text[:800].strip().replace("\n", " ")
    return clipped + ("…" if len(text) > 800 else "")


def review_invoice(profile: dict[str, Any]) -> dict[str, Any]:
    fields = dict(profile.get("canonical_fields", {}))
    rows = profile.get("records", {}).get("invoice_rows", []) if isinstance(profile.get("records"), dict) else []
    first = rows[0] if rows else {}
    for key, value in first.items():
        fields.setdefault(key, value)
    missing = [field for field in REQUIRED_INVOICE_FIELDS if not fields.get(field)]
    total = _num(fields.get("total_amount") or fields.get("total_amount_sum"))
    risk_flags: list[str] = []
    if missing:
        risk_flags.append("missing_required_fields")
    if total and total >= 5000:
        risk_flags.append("manager_approval_threshold")
    if total and total >= 25000:
        risk_flags.append("finance_review_threshold")
    invoice_ids = [str(row.get("invoice_id")) for row in rows if row.get("invoice_id")]
    duplicates = sorted({x for x in invoice_ids if invoice_ids.count(x) > 1})
    if duplicates:
        risk_flags.append("duplicate_invoice_number")
    if rows and any(str(row.get("approval_status", "")).lower() not in {"", "approved", "auto-approved"} for row in rows):
        risk_flags.append("approval_exception_present")

    if missing:
        action = "Needs review: resolve missing invoice fields before routing to AP."
        ready = False
    elif "duplicate_invoice_number" in risk_flags:
        action = "Hold for duplicate check before approval routing."
        ready = False
    elif total and total >= 25000:
        action = "Route to marketing manager and finance review before AP handoff."
        ready = False
    elif total and total >= 5000:
        action = "Route to marketing manager approval before AP handoff."
        ready = False
    else:
        action = "Ready for AP packet routing after human review."
        ready = True

    route = "Finance + Marketing Manager" if total and total >= 25000 else "Marketing Manager" if total and total >= 5000 else "AP Intake"
    return {
        "fields": {k: fields.get(k) for k in ["vendor", "invoice_id", "total_amount", "total_amount_sum", "invoice_date", "due_date", "department", "campaign", "po_number", "cost_center", "gl_code", "payment_terms", "approver"] if fields.get(k) not in (None, "")},
        "missing_fields": missing,
        "risk_flags": risk_flags,
        "duplicates": duplicates,
        "record_count": len(rows),
        "payment_ready": ready,
        "recommended_route": route,
        "recommended_action": action,
    }


def make_deliverables(result_like: dict[str, Any], review: dict[str, Any]) -> list[dict[str, Any]]:
    structured = result_like.get("structured_data", result_like)
    fields = review.get("fields", {})
    summary = structured.get("summary") or "Invoice packet prepared from source document."
    vendor = fields.get("vendor") or "Unknown vendor"
    invoice_id = fields.get("invoice_id") or "Unknown invoice"
    amount = fields.get("total_amount") or fields.get("total_amount_sum") or "Unknown amount"
    due = fields.get("due_date") or "Unknown due date"
    route = review.get("recommended_route") or "Review queue"
    action = review.get("recommended_action") or "Review and route."
    missing = review.get("missing_fields") or []
    risks = review.get("risk_flags") or []

    ap_packet = f"""# Invoice Review Packet

Vendor: {vendor}
Invoice: {invoice_id}
Amount: {amount}
Due Date: {due}
Route: {route}

## Summary
{summary}

## Review Notes
- Missing fields: {', '.join(missing) if missing else 'None detected'}
- Risk flags: {', '.join(risks) if risks else 'None detected'}
- Recommended action: {action}

## AP Handoff
Attach the source invoice/workbook, this packet, and any approval notes. Preserve the source hash from SignalLoom for audit traceability.
""".strip()
    slack = f"""Invoice review needed: {vendor} / {invoice_id} / {amount}. Route: {route}. Due: {due}. Action: {action}"""
    payload = {
        "type": "invoice_review",
        "vendor": vendor,
        "invoice_id": invoice_id,
        "amount": amount,
        "due_date": due,
        "route": route,
        "missing_fields": missing,
        "risk_flags": risks,
        "recommended_action": action,
    }
    exceptions = "\n".join(f"- {item}" for item in (missing + risks)) or "No missing fields or risk flags detected."
    return [
        {"id": "ap_packet", "title": "AP Review Packet", "kind": "markdown", "body": ap_packet},
        {"id": "slack_approval", "title": "Slack Approval Message", "kind": "text", "body": slack},
        {"id": "n8n_payload", "title": "n8n / Make Payload", "kind": "json", "body": json.dumps(payload, ensure_ascii=False, indent=2)},
        {"id": "exceptions", "title": "Exception Checklist", "kind": "markdown", "body": f"# Exceptions\n\n{exceptions}"},
    ]


def build_prompt(doc: SourceDocument, schema_name: str) -> str:
    return f"""
You are SignalLoom's local invoice operations extraction engine.
Return one valid JSON object only. Do not include markdown or prose outside JSON.
Do not invent missing values.

Required keys:
summary, canonical_fields, records, dates, money, contacts, missing_fields, risk_flags, recommended_route, recommended_action, confidence_notes

Schema: {schema_name}
Source: {doc.name}
Source text:
{doc.text[:22000]}
""".strip()


def analyze(doc: SourceDocument, schema_name: str, client: QwenClient | None = None) -> ExtractionResult:
    client = client or QwenClient()
    fallback = deterministic_profile(doc, schema_name)
    ai = client.generate_json(build_prompt(doc, schema_name), fallback)
    ai_usable = not any(k in ai for k in ("_model_status", "_model_error"))
    structured = dict(fallback)
    if ai_usable:
        structured["model_extraction"] = {k: v for k, v in ai.items() if not k.startswith("_model_")}
        if isinstance(ai.get("canonical_fields"), dict):
            structured["canonical_fields"] = {**structured.get("canonical_fields", {}), **{k: v for k, v in ai["canonical_fields"].items() if v not in (None, "", [], {})}}
        if isinstance(ai.get("summary"), str) and ai["summary"].strip():
            structured["summary"] = ai["summary"].strip()
    else:
        structured["model_notice"] = ai.get("_model_status") or ai.get("_model_error")
        if ai.get("_raw_model_response"):
            structured["raw_model_response_preview"] = ai["_raw_model_response"]

    review = review_invoice(structured)
    temp = {"structured_data": structured}
    deliverables = make_deliverables(temp, review)
    notes: list[str] = []
    if ai_usable and ai.get("_model_used"):
        notes.append(f"Local model analysis completed with {ai['_model_used']}.")
    elif "_model_status" in ai:
        notes.append(str(ai["_model_status"]))
        notes.append("Deterministic invoice analysis remained active.")
    elif "_model_error" in ai:
        notes.append(f"Local model fallback: {ai['_model_error']}")
        notes.append("Deterministic extraction produced the review packet and cards.")
    else:
        notes.append("Deterministic invoice analysis completed.")
    if structured.get("workbook"):
        wb = structured["workbook"]
        notes.append(f"Excel workbook parsed: {wb.get('sheet_count', 0)} sheet(s), {wb.get('rows_with_data', 0)} populated row(s), {wb.get('cells_with_data', 0)} populated cell(s).")
    notes.append("Original text is stored verbatim with a SHA-256 hash for source fidelity.")
    return ExtractionResult(
        source_id=doc.source_id,
        source_name=doc.name,
        schema_name=schema_name,
        model=client.model,
        created_at=now_iso(),
        preserved_text=doc.text,
        structured_data=structured,
        review=review,
        deliverables=deliverables,
        confidence_notes=notes,
        triggers_run=[],
    )


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
