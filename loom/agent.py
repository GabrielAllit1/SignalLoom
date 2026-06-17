from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json

from .analyze import make_deliverables
from .model import ExtractionResult, now_iso

DEFAULT_AGENT_GOAL = (
    "Reduce manual marketing invoice processing time while preserving source fidelity, "
    "approval controls, and an auditable AP handoff."
)


@dataclass(slots=True)
class AgentStep:
    id: str
    name: str
    tool: str
    objective: str
    status: str = "pending"
    output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentRun:
    run_id: str
    goal: str
    started_at: str
    completed_at: str | None
    status: str
    steps: list[AgentStep]
    cards: list[dict[str, Any]]
    recommended_next_action: str
    needs_human_approval: bool
    route_allowed: bool
    audit_note: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class InvoiceOpsAgent:
    """Goal-directed invoice operations agent.

    The agent is intentionally bounded. It can inspect, plan, validate, create
    deliverables, and recommend routing. It cannot send a webhook or approve a
    payment by itself; those actions remain human-click gated in the UI.
    """

    def __init__(self, goal: str = DEFAULT_AGENT_GOAL) -> None:
        self.goal = goal

    def plan(self, result: ExtractionResult, goal: str | None = None) -> list[AgentStep]:
        selected_goal = goal or self.goal
        return [
            AgentStep(
                id="observe",
                name="Observe source and extraction",
                tool="source_fidelity_inspector",
                objective="Read the extracted source, schema, SHA-256, record count, and model notes.",
            ),
            AgentStep(
                id="validate",
                name="Validate invoice facts",
                tool="invoice_validator",
                objective="Check required invoice fields, amount, due date, vendor, PO, payment terms, and review state.",
            ),
            AgentStep(
                id="exceptions",
                name="Find exceptions and routing blockers",
                tool="exception_detector",
                objective="Identify missing fields, duplicate invoice numbers, threshold approvals, and AP readiness blockers.",
            ),
            AgentStep(
                id="produce",
                name="Produce work product",
                tool="deliverable_builder",
                objective="Create AP packet, approval message, exception checklist, workflow payload, and an agent run report.",
            ),
            AgentStep(
                id="gate",
                name="Apply human approval gate",
                tool="governance_gate",
                objective=(
                    "Recommend the next action while preventing automatic approval, payment, or webhook routing "
                    f"unless a human clicks the route control. Goal: {selected_goal}"
                ),
            ),
        ]

    def run(self, result: ExtractionResult, goal: str | None = None) -> AgentRun:
        started = now_iso()
        run_id = f"agent-{result.source_id}-{started.replace(':', '').replace('+', 'Z')}"
        steps = self.plan(result, goal)
        structured = result.structured_data or {}
        review = result.review or {}

        for step in steps:
            step.status = "running"
            if step.id == "observe":
                step.output = self._observe(result)
            elif step.id == "validate":
                step.output = self._validate(review)
            elif step.id == "exceptions":
                step.output = self._exceptions(review)
            elif step.id == "produce":
                step.output = self._produce(result, review)
            elif step.id == "gate":
                step.output = self._gate(review)
            step.status = "complete"

        route_allowed = bool(review.get("payment_ready"))
        needs_human_approval = True
        action = str(review.get("recommended_action") or "Review extracted facts, confirm exceptions, then route manually.")
        if route_allowed:
            action = f"Human may approve routing after final review. {action}"
        else:
            action = f"Hold for review before routing. {action}"

        cards = self._agent_cards(result, structured, review, steps, route_allowed, action)
        completed = now_iso()
        return AgentRun(
            run_id=run_id,
            goal=goal or self.goal,
            started_at=started,
            completed_at=completed,
            status="complete",
            steps=steps,
            cards=cards,
            recommended_next_action=action,
            needs_human_approval=needs_human_approval,
            route_allowed=route_allowed,
            audit_note=(
                "Agent completed inspect-plan-act-verify cycle. External routing, payment approval, and webhook sending remain human-click gated."
            ),
        )

    def apply(self, result: ExtractionResult, goal: str | None = None) -> AgentRun:
        run = self.run(result, goal)
        existing = {str(card.get("id")) for card in result.deliverables}
        for card in run.cards:
            if str(card.get("id")) not in existing:
                result.deliverables.append(card)
                existing.add(str(card.get("id")))
        result.triggers_run.append({
            "type": "agent_run",
            "run_id": run.run_id,
            "created_at": run.completed_at,
            "route_allowed": run.route_allowed,
            "needs_human_approval": run.needs_human_approval,
            "recommended_next_action": run.recommended_next_action,
        })
        return run

    def _observe(self, result: ExtractionResult) -> dict[str, Any]:
        fidelity = result.structured_data.get("source_fidelity", {}) if isinstance(result.structured_data, dict) else {}
        return {
            "source_name": result.source_name,
            "schema": result.schema_name,
            "model": result.model,
            "source_id": result.source_id,
            "sha256": fidelity.get("sha256"),
            "line_count": fidelity.get("line_count"),
            "char_count": fidelity.get("char_count"),
            "confidence_notes": result.confidence_notes,
        }

    def _validate(self, review: dict[str, Any]) -> dict[str, Any]:
        fields = review.get("fields", {}) if isinstance(review.get("fields"), dict) else {}
        required = ["vendor", "invoice_id", "total_amount", "invoice_date", "due_date", "department", "payment_terms"]
        present = sorted([key for key in required if fields.get(key) or key in fields])
        return {
            "present_required_fields": present,
            "missing_fields": review.get("missing_fields", []),
            "record_count": review.get("record_count", 0),
            "payment_ready": bool(review.get("payment_ready")),
        }

    def _exceptions(self, review: dict[str, Any]) -> dict[str, Any]:
        return {
            "risk_flags": review.get("risk_flags", []),
            "duplicates": review.get("duplicates", []),
            "recommended_route": review.get("recommended_route"),
            "recommended_action": review.get("recommended_action"),
        }

    def _produce(self, result: ExtractionResult, review: dict[str, Any]) -> dict[str, Any]:
        refreshed = make_deliverables(result.to_dict(), review)
        return {
            "cards_ready": [card.get("title") for card in refreshed],
            "card_count": len(refreshed),
        }

    def _gate(self, review: dict[str, Any]) -> dict[str, Any]:
        return {
            "human_required": True,
            "route_allowed_after_review": bool(review.get("payment_ready")),
            "blocked_actions": ["approve_payment", "send_webhook_without_click", "modify_source_record"],
        }

    def _agent_cards(
        self,
        result: ExtractionResult,
        structured: dict[str, Any],
        review: dict[str, Any],
        steps: list[AgentStep],
        route_allowed: bool,
        action: str,
    ) -> list[dict[str, Any]]:
        fields = review.get("fields", {}) if isinstance(review.get("fields"), dict) else {}
        source_hash = structured.get("source_fidelity", {}).get("sha256") if isinstance(structured.get("source_fidelity"), dict) else None
        step_lines = "\n".join(f"- {step.name}: {step.status} via `{step.tool}`" for step in steps)
        blockers = list(review.get("missing_fields") or []) + list(review.get("risk_flags") or [])
        blocker_text = "\n".join(f"- {item}" for item in blockers) if blockers else "- No automatic blocker detected. Human review still required before routing."
        report = f"""# SignalLoom Agent Run Report

Goal: {self.goal}
Source: {result.source_name}
Schema: {result.schema_name}
Model: {result.model}
Source Hash: {source_hash or 'not available'}

## Agent Loop
1. Observe source and extracted evidence.
2. Plan review and routing checks.
3. Act by creating reusable AP deliverables.
4. Verify blockers and approval readiness.
5. Wait for human approval before external routing.

## Completed Steps
{step_lines}

## Decision
- Route allowed after review: {route_allowed}
- Human approval required: True
- Recommended next action: {action}

## Blockers / Review Items
{blocker_text}
""".strip()

        route_card = f"""# Approval Route Recommendation

Vendor: {fields.get('vendor', 'Unknown vendor')}
Invoice: {fields.get('invoice_id', 'Unknown invoice')}
Amount: {fields.get('total_amount') or fields.get('total_amount_sum') or 'Unknown amount'}
Recommended Route: {review.get('recommended_route', 'Review queue')}
Payment Ready: {bool(review.get('payment_ready'))}

Recommended Action:
{action}

Governance:
- The agent may prepare the packet.
- The agent may recommend the route.
- The agent may not approve payment or send a webhook without a human click.
""".strip()

        playbook = f"""# InvoiceOps Agent Playbook

Use this workflow when marketing invoices arrive from email, Excel, PDF, or vendor portals.

1. Capture: ingest the invoice or workbook and preserve the source hash.
2. Match: compare vendor, invoice ID, amount, PO, receipt, department, campaign, payment terms, and due date.
3. Approve: route exceptions to the correct reviewer based on thresholds and missing data.
4. Pay: send a reviewed packet to AP or workflow automation only after human confirmation.
5. Audit: keep source hash, agent run report, approval message, and payload history.

Manual Work Reduced:
- Re-keying invoice data.
- Chasing missing POs and approvers.
- Writing approval messages manually.
- Building AP packet summaries by hand.
- Acting as middleware between marketing, procurement, AP, and accounting tools.
""".strip()
        return [
            {"id": "agent_report", "title": "Agent Run Report", "kind": "markdown", "body": report},
            {"id": "agent_route", "title": "Approval Route Recommendation", "kind": "markdown", "body": route_card},
            {"id": "agent_playbook", "title": "InvoiceOps Agent Playbook", "kind": "markdown", "body": playbook},
        ]


def run_agent(result: ExtractionResult, goal: str | None = None) -> AgentRun:
    return InvoiceOpsAgent(goal or DEFAULT_AGENT_GOAL).apply(result, goal)
