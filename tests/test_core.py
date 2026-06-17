from __future__ import annotations

from pathlib import Path
import json

from loom.ai import QwenClient
from loom.analyze import analyze, deterministic_profile, invoice_rows, make_deliverables, review_invoice
from loom.parse import extract_text


def test_csv_invoice_rows_parse_sample() -> None:
    doc = extract_text(Path("samples") / "invoice_ops.csv")
    rows = invoice_rows(doc.text)
    assert len(rows) == 3
    assert rows[0]["vendor"] == "Northstar Media Group"
    assert rows[0]["invoice_id"] == "INV-2026-001"
    assert rows[0]["total_amount_numeric"] == 48295.52


def test_invoice_review_flags_high_value() -> None:
    doc = extract_text(Path("samples") / "invoice_ops.csv")
    profile = deterministic_profile(doc, "invoice")
    review = review_invoice(profile)
    assert review["record_count"] == 3
    assert "manager_approval_threshold" in review["risk_flags"]
    assert "finance_review_threshold" in review["risk_flags"]
    assert review["payment_ready"] is False


def test_deliverables_include_ap_slack_payload() -> None:
    doc = extract_text(Path("samples") / "invoice_ops.csv")
    result = analyze(doc, "invoice", client=QwenClient(host="http://127.0.0.1:9"))
    ids = {item["id"] for item in result.deliverables}
    assert {"ap_packet", "slack_approval", "n8n_payload", "exceptions"}.issubset(ids)
    payload_card = next(item for item in result.deliverables if item["id"] == "n8n_payload")
    assert json.loads(payload_card["body"])["type"] == "invoice_review"


def test_json_salvage() -> None:
    client = QwenClient()
    parsed = client.parse_json('notes before {"summary":"ok","canonical_fields":{"a":1}} notes after')
    assert parsed is not None
    assert parsed["summary"] == "ok"


def test_analyze_offline_keeps_review_and_source() -> None:
    doc = extract_text(Path("samples") / "invoice_ops.csv")
    result = analyze(doc, "invoice", client=QwenClient(host="http://127.0.0.1:9"))
    assert result.review["recommended_route"] == "Finance + Marketing Manager"
    assert result.preserved_text.startswith("# CSV document")
    assert result.structured_data["source_fidelity"]["source_name"] == "invoice_ops.csv"
