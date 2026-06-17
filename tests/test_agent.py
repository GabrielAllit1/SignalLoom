from pathlib import Path

from loom.agent import InvoiceOpsAgent
from loom.ai import QwenClient
from loom.analyze import analyze
from loom.parse import extract_text


def test_agent_run_has_plan_cards_and_human_gate() -> None:
    doc = extract_text(Path("samples") / "invoice_ops.csv")
    result = analyze(doc, "invoice", client=QwenClient(host="http://127.0.0.1:9"))
    run = InvoiceOpsAgent().apply(result)
    assert run.status == "complete"
    assert [step.id for step in run.steps] == ["observe", "validate", "exceptions", "produce", "gate"]
    assert run.needs_human_approval is True
    assert any(card["id"] == "agent_report" for card in result.deliverables)
    assert any(item["type"] == "agent_run" for item in result.triggers_run)


def test_agent_never_auto_routes() -> None:
    doc = extract_text(Path("samples") / "invoice_ops.csv")
    result = analyze(doc, "invoice", client=QwenClient(host="http://127.0.0.1:9"))
    run = InvoiceOpsAgent().run(result)
    gate = next(step for step in run.steps if step.id == "gate")
    assert gate.output["human_required"] is True
    assert "send_webhook_without_click" in gate.output["blocked_actions"]
