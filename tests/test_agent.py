from __future__ import annotations

from loom.agent import run_invoice_agent


def test_run_invoice_agent_contract_ready_invoice() -> None:
    result = run_invoice_agent(
        source_text=(
            "Invoice INV-1001 from Northstar Print Co. "
            "Total $12000. Due 2026-02-03. Department Marketing. "
            "Payment terms Net 30. PO PO-7518."
        ),
        structured_data={
            "canonical_fields": {
                "vendor": "Northstar Print Co.",
                "invoice_id": "INV-1001",
                "total_amount": "12000",
                "invoice_date": "2026-01-04",
                "due_date": "2026-02-03",
                "department": "Marketing",
                "payment_terms": "Net 30",
                "po_number": "PO-7518",
            }
        },
        history=[],
        services={"n8n_webhook": "https://example.invalid/webhook"},
    )

    expected_keys = {
        "goal",
        "inspect",
        "assess",
        "plan",
        "actions",
        "verify",
        "human_gate",
        "deliverables",
    }

    assert expected_keys.issubset(result.keys())
    assert result["human_gate"]["required"] is True
    assert result["actions"]["automatic_payment_approved"] is False
    assert result["actions"]["automatic_webhook_sent"] is False
    assert result["actions"]["source_modified"] is False
    assert result["verify"]["human_review_required"] is True
    assert "manager_approval_threshold" in result["assess"]["risk_flags"]

    titles = [card["title"] for card in result["deliverables"]]
    assert "Agent Run Report" in titles
    assert "AP Review Packet" in titles
    assert "Approval Route Recommendation" in titles
    assert "Slack Approval Message" in titles
    assert "Exception Checklist" in titles
    assert "n8n / Make / Zapier Payload" in titles


def test_run_invoice_agent_blocks_missing_required_fields() -> None:
    result = run_invoice_agent(
        source_text="Invoice with missing AP fields.",
        structured_data={
            "canonical_fields": {
                "vendor": "CloudArc Analytics",
                "invoice_id": "INV-2002",
            }
        },
        history=[],
        services={},
    )

    assert result["verify"]["required_fields_present"] is False
    assert result["verify"]["payment_ready"] is False
    assert result["verify"]["safe_for_external_routing"] is False
    assert "missing_required_fields" in result["assess"]["risk_flags"]
    assert result["plan"]["recommended_step"] == "block_routing_until_review"


def test_run_invoice_agent_detects_duplicate_invoice() -> None:
    result = run_invoice_agent(
        source_text="Invoice INV-3003 from Vendor A.",
        structured_data={
            "canonical_fields": {
                "vendor": "Vendor A",
                "invoice_id": "INV-3003",
                "total_amount": "500",
                "invoice_date": "2026-01-05",
                "due_date": "2026-02-05",
                "department": "Marketing",
                "payment_terms": "Net 30",
            }
        },
        history=[
            {
                "fields": {
                    "vendor": "Vendor A",
                    "invoice_id": "INV-3003",
                    "total_amount": "500",
                }
            }
        ],
        services={},
    )

    assert result["assess"]["duplicate_invoice_risk"] is True
    assert "duplicate_invoice_number" in result["assess"]["risk_flags"]
    assert result["verify"]["payment_ready"] is False
    assert result["plan"]["recommended_step"] == "block_routing_until_review"
