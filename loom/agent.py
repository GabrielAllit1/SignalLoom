from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json
import re

from .analyze import make_deliverables
from .model import ExtractionResult, now_iso

DEFAULT_AGENT_GOAL = (
    "Reduce manual marketing invoice processing time while preserving source fidelity, "
    "approval controls, and an auditable AP handoff."
)

REQUIRED_INVOICE_FIELDS = [
    "vendor",
    "invoice_id",
    "total_amount",
    "invoice_date",
    "due_date",
    "department",
    "payment_terms",
]

BLOCKING_RISK_FLAGS = {
    "missing_required_fields",
    "duplicate_invoice_number",
    "approval_exception_present",
}

HUMAN_GATED_ACTIONS = [
    "approve_payment",
    "mark_invoice_paid",
    "send_webhook",
    "modify_source_record",
]


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
    """Bounded, human-gated invoice operations agent.

    The agent can inspect invoice evidence, assess AP readiness, plan workflow
    actions, produce deliverables, and verify blockers. It cannot approve
    payment, mark an invoice paid, alter source evidence, or send webhooks
    without a human-triggered UI action.
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
                objective="Read extracted source, schema, SHA-256, record count, and model notes.",
            ),
            AgentStep(
                id="validate",
                name="Validate invoice facts",
                tool="invoice_validator",
                objective=(
                    "Check vendor, invoice ID, amount, due date, department, PO, "
                    "payment terms, and review state."
                ),
            ),
            AgentStep(
                id="exceptions",
                name="Find exceptions and routing blockers",
                tool="exception_detector",
                objective=(
                    "Identify missing fields, duplicates, threshold approvals, unresolved exceptions, "
                    "and AP readiness blockers."
                ),
            ),
            AgentStep(
                id="produce",
                name="Produce work product",
                tool="deliverable_builder",
                objective=(
                    "Create AP packet, approval message, exception checklist, workflow payload, "
                    "and agent run report."
                ),
            ),
            AgentStep(
                id="gate",
                name="Apply human approval gate",
                tool="governance_gate",
                objective=(
                    "Recommend next action while preventing automatic approval, payment, or webhook routing "
                    f"unless a human clicks the route control. Goal: {selected_goal}"
                ),
            ),
        ]

    def run(self, result: ExtractionResult, goal: str | None = None) -> AgentRun:
        started = now_iso()
        safe_started = started.replace(":", "").replace("+", "Z")
        run_id = f"agent-{result.source_id}-{safe_started}"

        steps = self.plan(result, goal)
        structured = result.structured_data if isinstance(result.structured_data, dict) else {}
        review = result.review if isinstance(result.review, dict) else {}

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

        route_allowed = _review_route_allowed(review)
        needs_human_approval = True

        base_action = str(
            review.get("recommended_action")
            or "Review extracted facts, confirm exceptions, then route manually."
        )
        if route_allowed:
            action = f"Human may approve routing after final review. {base_action}"
        else:
            action = f"Hold for review before routing. {base_action}"

        cards = self._agent_cards(result, structured, review, steps, route_allowed, action)

        return AgentRun(
            run_id=run_id,
            goal=goal or self.goal,
            started_at=started,
            completed_at=now_iso(),
            status="complete",
            steps=steps,
            cards=cards,
            recommended_next_action=action,
            needs_human_approval=needs_human_approval,
            route_allowed=route_allowed,
            audit_note=(
                "Agent completed inspect-plan-act-verify cycle. External routing, "
                "payment approval, and webhook sending remain human-click gated."
            ),
        )

    def apply(self, result: ExtractionResult, goal: str | None = None) -> AgentRun:
        run = self.run(result, goal)
        existing = {str(card.get("id")) for card in result.deliverables}

        for card in run.cards:
            card_id = str(card.get("id"))
            if card_id not in existing:
                result.deliverables.append(card)
                existing.add(card_id)

        result.triggers_run.append(
            {
                "type": "agent_run",
                "run_id": run.run_id,
                "created_at": run.completed_at,
                "route_allowed": run.route_allowed,
                "needs_human_approval": run.needs_human_approval,
                "recommended_next_action": run.recommended_next_action,
            }
        )
        return run

    def _observe(self, result: ExtractionResult) -> dict[str, Any]:
        structured = result.structured_data if isinstance(result.structured_data, dict) else {}
        fidelity = structured.get("source_fidelity", {})
        if not isinstance(fidelity, dict):
            fidelity = {}

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
        fields = review.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}

        present = sorted([key for key in REQUIRED_INVOICE_FIELDS if _present(fields.get(key))])
        missing = _dedupe_strings(review.get("missing_fields") or [])

        for key in REQUIRED_INVOICE_FIELDS:
            if key not in present and key not in missing:
                missing.append(key)

        return {
            "present_required_fields": present,
            "missing_fields": missing,
            "record_count": review.get("record_count", 0),
            "payment_ready": bool(review.get("payment_ready")),
        }

    def _exceptions(self, review: dict[str, Any]) -> dict[str, Any]:
        return {
            "risk_flags": _dedupe_strings(review.get("risk_flags") or []),
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
            "route_allowed_after_review": _review_route_allowed(review),
            "blocked_actions": HUMAN_GATED_ACTIONS,
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
        fields = review.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}

        fidelity = structured.get("source_fidelity", {})
        if not isinstance(fidelity, dict):
            fidelity = {}

        source_hash = fidelity.get("sha256")
        step_lines = "\n".join(
            f"- {step.name}: {step.status} via `{step.tool}`" for step in steps
        )

        blockers = _dedupe_strings(
            list(review.get("missing_fields") or []) + list(review.get("risk_flags") or [])
        )
        blocker_text = (
            "\n".join(f"- {item}" for item in blockers)
            if blockers
            else "- No automatic blocker detected. Human review still required before routing."
        )

        report = f"""# SignalLoom Agent Run Report

Goal: {self.goal}
Source: {result.source_name}
Schema: {result.schema_name}
Model: {result.model}
Source Hash: {source_hash or "not available"}

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

Vendor: {fields.get("vendor", "Unknown vendor")}
Invoice: {fields.get("invoice_id", "Unknown invoice")}
Amount: {fields.get("total_amount") or fields.get("total_amount_sum") or "Unknown amount"}
Recommended Route: {review.get("recommended_route", "Review queue")}
Payment Ready: {bool(review.get("payment_ready"))}

Recommended Action:
{action}

Governance:
- The agent may prepare the packet.
- The agent may recommend the route.
- The agent may not approve payment or send a webhook without a human click.
""".strip()

        playbook = """# InvoiceOps Agent Playbook

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
            {
                "id": "agent_report",
                "title": "Agent Run Report",
                "kind": "markdown",
                "body": report,
            },
            {
                "id": "agent_route",
                "title": "Approval Route Recommendation",
                "kind": "markdown",
                "body": route_card,
            },
            {
                "id": "agent_playbook",
                "title": "InvoiceOps Agent Playbook",
                "kind": "markdown",
                "body": playbook,
            },
        ]


def run_agent(result: ExtractionResult, goal: str | None = None) -> AgentRun:
    """Run the class-based agent against an ExtractionResult and attach cards."""
    return InvoiceOpsAgent(goal or DEFAULT_AGENT_GOAL).apply(result, goal)


def run_invoice_agent(
    source_or_result: ExtractionResult | str,
    structured_data: dict[str, Any] | str | None = None,
    history: list[Any] | None = None,
    services: dict[str, Any] | None = None,
    goal: str | None = None,
) -> AgentRun | dict[str, Any]:
    """Run the public bounded InvoiceOps agent entry point.

    This function intentionally supports both calling styles used in this repo:

    1. Runtime/UI style:
       run_invoice_agent(result: ExtractionResult, goal: str | None = None) -> AgentRun

    2. Public contract/test style:
       run_invoice_agent(source_text: str, structured_data: dict, history=None, services=None) -> dict

    In both modes it never approves payment, marks an invoice paid, sends a webhook
    automatically, or modifies source evidence.
    """
    if isinstance(source_or_result, ExtractionResult):
        runtime_goal = goal
        if isinstance(structured_data, str) and runtime_goal is None:
            runtime_goal = structured_data
        return InvoiceOpsAgent(runtime_goal or DEFAULT_AGENT_GOAL).apply(
            source_or_result,
            runtime_goal,
        )

    source_text = source_or_result or ""
    if structured_data is not None and not isinstance(structured_data, dict):
        raise TypeError("structured_data must be a dictionary when source_text is provided")

    selected_goal = goal or DEFAULT_AGENT_GOAL
    history = history or []
    services = services or {}
    structured = structured_data or {}

    review = _extract_review(structured)
    fields = _extract_fields(structured, review)

    invoice_id = _first_present(
        fields.get("invoice_id"),
        fields.get("invoice_number"),
        fields.get("invoice"),
        structured.get("invoice_id"),
        structured.get("invoice_number"),
    )
    vendor = _first_present(
        fields.get("vendor"),
        fields.get("supplier"),
        structured.get("vendor"),
        structured.get("supplier"),
    )
    total = _first_present(
        fields.get("total_amount"),
        fields.get("amount"),
        fields.get("total"),
        fields.get("total_amount_sum"),
        structured.get("total_amount"),
        structured.get("amount"),
    )
    due_date = _first_present(fields.get("due_date"), structured.get("due_date"))
    invoice_date = _first_present(
        fields.get("invoice_date"),
        fields.get("date"),
        structured.get("invoice_date"),
        structured.get("date"),
    )
    department = _first_present(
        fields.get("department"),
        fields.get("cost_center"),
        structured.get("department"),
        structured.get("cost_center"),
    )
    payment_terms = _first_present(
        fields.get("payment_terms"),
        fields.get("terms"),
        structured.get("payment_terms"),
        structured.get("terms"),
    )
    po_number = _first_present(
        fields.get("po_number"),
        fields.get("purchase_order"),
        structured.get("po_number"),
        structured.get("purchase_order"),
    )

    normalized_fields = {
        "vendor": vendor,
        "invoice_id": invoice_id,
        "total_amount": total,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "department": department,
        "payment_terms": payment_terms,
        "po_number": po_number,
    }

    missing_fields = [
        key for key in REQUIRED_INVOICE_FIELDS if not _present(normalized_fields.get(key))
    ]

    risk_flags = _dedupe_strings(review.get("risk_flags") or [])
    if missing_fields:
        risk_flags = _append_once(risk_flags, "missing_required_fields")

    amount_value = _money_to_float(total)
    if amount_value >= 10_000:
        risk_flags = _append_once(risk_flags, "manager_approval_threshold")
    if amount_value >= 50_000:
        risk_flags = _append_once(risk_flags, "finance_review_threshold")

    duplicate = _has_duplicate_invoice(invoice_id, history)
    if duplicate:
        risk_flags = _append_once(risk_flags, "duplicate_invoice_number")

    approval_exception = _contains_approval_exception(review, structured, source_text)
    if approval_exception:
        risk_flags = _append_once(risk_flags, "approval_exception_present")

    payment_ready = (
        not missing_fields
        and not duplicate
        and not any(flag in BLOCKING_RISK_FLAGS for flag in risk_flags)
    )

    if missing_fields or duplicate:
        recommended_step = "block_routing_until_review"
        recommended_action = "Resolve missing fields or duplicate risk before AP routing."
    elif "approval_exception_present" in risk_flags:
        recommended_step = "request_approval_exception_review"
        recommended_action = "Route to the assigned approval owner before AP handoff."
    elif risk_flags:
        recommended_step = "request_human_review"
        recommended_action = "Route to a human reviewer before AP handoff."
    else:
        recommended_step = "mark_ready_for_human_routing"
        recommended_action = "Human reviewer may route the AP packet after final review."

    available_services = sorted(
        key
        for key, value in services.items()
        if _present(value) and not str(key).lower().endswith("key")
    )
    sensitive_services_configured = sorted(
        key
        for key, value in services.items()
        if _present(value) and str(key).lower().endswith("key")
    )

    inspect = {
        "source_present": bool(source_text),
        "source_chars": len(source_text or ""),
        "source_preview": _clean_preview(source_text, limit=500),
        "structured_keys": sorted(structured.keys()),
        "services_available": available_services,
        "sensitive_services_configured": sensitive_services_configured,
        "invoice_id": invoice_id,
        "vendor": vendor,
        "amount": total,
        "po_number": po_number,
    }

    assess = {
        "fields": normalized_fields,
        "missing_fields": missing_fields,
        "risk_flags": risk_flags,
        "duplicate_invoice_risk": duplicate,
        "approval_exception_risk": approval_exception,
        "amount_value": amount_value,
        "payment_ready": payment_ready,
        "routing_gaps": _dedupe_strings(missing_fields + risk_flags),
    }

    plan = {
        "recommended_step": recommended_step,
        "recommended_action": recommended_action,
        "allowed_next_actions": [
            "generate_ap_packet",
            "create_slack_approval_message",
            "prepare_webhook_payload",
            "request_human_review",
            "save_audit_record",
        ],
        "blocked_next_actions": [
            "approve_payment",
            "mark_invoice_paid",
            "send_webhook_automatically",
            "modify_source_record",
        ],
    }

    deliverables = _build_agent_deliverables(
        goal=selected_goal,
        fields=normalized_fields,
        risk_flags=risk_flags,
        missing_fields=missing_fields,
        duplicate=duplicate,
        payment_ready=payment_ready,
        recommended_step=recommended_step,
        recommended_action=recommended_action,
        source_text=source_text,
        structured_data=structured,
    )

    actions = {
        "generated": [item["title"] for item in deliverables],
        "automatic_payment_approved": False,
        "automatic_webhook_sent": False,
        "source_modified": False,
        "ready_for_manual_route_click": payment_ready,
    }

    verify = {
        "required_fields_present": not missing_fields,
        "duplicate_invoice_risk": duplicate,
        "blocking_risk_flags": [flag for flag in risk_flags if flag in BLOCKING_RISK_FLAGS],
        "payment_ready": payment_ready,
        "safe_for_external_routing": payment_ready and not duplicate,
        "human_review_required": True,
        "source_fidelity_preserved": True,
    }

    human_gate = {
        "required": True,
        "external_routing_required": True,
        "payment_approval_required": True,
        "blocked_without_user_click": HUMAN_GATED_ACTIONS,
        "note": (
            "The agent can prepare and recommend. A human must approve external routing "
            "and payment actions."
        ),
    }

    return {
        "goal": selected_goal,
        "inspect": inspect,
        "assess": assess,
        "plan": plan,
        "actions": actions,
        "verify": verify,
        "human_gate": human_gate,
        "deliverables": deliverables,
    }


def _extract_review(structured_data: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        structured_data.get("review"),
        structured_data.get("invoice_review"),
        structured_data.get("ap_review"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def _extract_fields(
    structured_data: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    candidates = [
        review.get("fields"),
        structured_data.get("canonical_fields"),
        structured_data.get("fields"),
        structured_data.get("invoice"),
        structured_data.get("header"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def _build_agent_deliverables(
    *,
    goal: str,
    fields: dict[str, Any],
    risk_flags: list[str],
    missing_fields: list[str],
    duplicate: bool,
    payment_ready: bool,
    recommended_step: str,
    recommended_action: str,
    source_text: str,
    structured_data: dict[str, Any],
) -> list[dict[str, Any]]:
    vendor = fields.get("vendor") or "Missing"
    invoice_id = fields.get("invoice_id") or "Missing"
    total = fields.get("total_amount") or "Missing"
    invoice_date = fields.get("invoice_date") or "Missing"
    due_date = fields.get("due_date") or "Missing"
    department = fields.get("department") or "Missing"
    payment_terms = fields.get("payment_terms") or "Missing"
    po_number = fields.get("po_number") or "Missing"

    risk_text = ", ".join(risk_flags) if risk_flags else "None detected"
    missing_text = ", ".join(missing_fields) if missing_fields else "None detected"
    structured_key_text = ", ".join(sorted(structured_data.keys())) if structured_data else "None"

    agent_report = f"""# Agent Run Report

Goal: {goal}

## Loop
1. Inspect source and structured extraction.
2. Assess missing fields, duplicate risk, approval status, payment readiness, and routing gaps.
3. Plan the next AP workflow step.
4. Act by generating reusable AP deliverables.
5. Verify readiness and blocked actions.
6. Stop at the human gate before payment approval or external routing.

## Inspect
- Source present: {bool(source_text)}
- Source characters: {len(source_text or "")}
- Structured keys: {structured_key_text}

## Assess
- Vendor: {vendor}
- Invoice: {invoice_id}
- Amount: {total}
- Missing fields: {missing_text}
- Risk flags: {risk_text}
- Duplicate invoice risk: {duplicate}
- Payment ready: {payment_ready}

## Plan
- Recommended step: {recommended_step}
- Recommended action: {recommended_action}

## Human Gate
The agent did not approve payment, mark the invoice paid, send a webhook, or modify source evidence.
""".strip()

    ap_packet = f"""# AP Review Packet

Vendor: {vendor}
Invoice: {invoice_id}
Amount: {total}
Invoice Date: {invoice_date}
Due Date: {due_date}
Department: {department}
PO Number: {po_number}
Payment Terms: {payment_terms}

Missing Fields: {missing_text}
Risk Flags: {risk_text}

Recommended Action:
{recommended_action}

Review Control:
Human approval is required before external routing or payment action.
""".strip()

    approval_route = f"""# Approval Route Recommendation

Recommended Step: {recommended_step}
Recommended Action: {recommended_action}

Invoice Context:
- Vendor: {vendor}
- Invoice: {invoice_id}
- Amount: {total}
- Department: {department}
- Due Date: {due_date}

Governance:
- Agent may prepare the packet.
- Agent may recommend the route.
- Agent may not approve payment.
- Agent may not send a webhook without a human click.
""".strip()

    slack_message = (
        f"Invoice review needed: {vendor} / {invoice_id} / {total}. "
        f"Department: {department}. Due: {due_date}. Action: {recommended_action}"
    )

    checklist_items = _dedupe_strings(missing_fields + risk_flags)
    if checklist_items:
        exception_checklist = "# Exception Checklist\n\n" + "\n".join(
            f"- [ ] {item}" for item in checklist_items
        )
    else:
        exception_checklist = "# Exception Checklist\n\n- [ ] Final human review before routing."

    workflow_payload = {
        "type": "invoice_review",
        "vendor": fields.get("vendor"),
        "invoice_id": fields.get("invoice_id"),
        "amount": fields.get("total_amount"),
        "invoice_date": fields.get("invoice_date"),
        "due_date": fields.get("due_date"),
        "department": fields.get("department"),
        "po_number": fields.get("po_number"),
        "payment_terms": fields.get("payment_terms"),
        "missing_fields": missing_fields,
        "risk_flags": risk_flags,
        "duplicate_invoice_risk": duplicate,
        "payment_ready": payment_ready,
        "recommended_step": recommended_step,
        "recommended_action": recommended_action,
        "human_gate_required": True,
    }

    return [
        {
            "id": "agent_report",
            "title": "Agent Run Report",
            "kind": "markdown",
            "body": agent_report,
        },
        {
            "id": "ap_packet",
            "title": "AP Review Packet",
            "kind": "markdown",
            "body": ap_packet,
        },
        {
            "id": "approval_route",
            "title": "Approval Route Recommendation",
            "kind": "markdown",
            "body": approval_route,
        },
        {
            "id": "slack_approval",
            "title": "Slack Approval Message",
            "kind": "text",
            "body": slack_message,
        },
        {
            "id": "exception_checklist",
            "title": "Exception Checklist",
            "kind": "markdown",
            "body": exception_checklist,
        },
        {
            "id": "workflow_payload",
            "title": "n8n / Make / Zapier Payload",
            "kind": "json",
            "body": workflow_payload,
        },
    ]


def _review_route_allowed(review: dict[str, Any]) -> bool:
    risk_flags = _dedupe_strings(review.get("risk_flags") or [])
    blocking = any(flag in BLOCKING_RISK_FLAGS for flag in risk_flags)
    missing = bool(review.get("missing_fields") or [])
    duplicate = bool(review.get("duplicates") or [])
    return bool(review.get("payment_ready")) and not blocking and not missing and not duplicate


def _has_duplicate_invoice(invoice_id: Any, history: list[Any]) -> bool:
    if not _present(invoice_id):
        return False

    invoice_value = str(invoice_id).strip().lower()

    for prior in history:
        if not isinstance(prior, dict):
            continue

        prior_review = prior.get("review") if isinstance(prior.get("review"), dict) else {}
        prior_fields = (
            prior.get("fields")
            if isinstance(prior.get("fields"), dict)
            else prior_review.get("fields")
            if isinstance(prior_review.get("fields"), dict)
            else prior
        )

        if not isinstance(prior_fields, dict):
            continue

        prior_invoice = _first_present(
            prior_fields.get("invoice_id"),
            prior_fields.get("invoice_number"),
            prior_fields.get("invoice"),
        )

        if _present(prior_invoice) and str(prior_invoice).strip().lower() == invoice_value:
            return True

    return False


def _contains_approval_exception(
    review: dict[str, Any],
    structured_data: dict[str, Any],
    source_text: str,
) -> bool:
    approval_status = str(
        _first_present(
            review.get("approval_status"),
            structured_data.get("approval_status"),
            "",
        )
    ).lower()

    haystack = f"{approval_status}\n{source_text or ''}".lower()
    patterns = [
        "approval required",
        "approval exception",
        "needs review",
        "pending approval",
        "manager approval",
    ]
    return any(pattern in haystack for pattern in patterns)


def _money_to_float(value: Any) -> float:
    if value is None:
        return 0.0

    if isinstance(value, int | float):
        return float(value)

    text = str(value)
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0

    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return 0.0


def _first_present(*values: Any) -> Any:
    for value in values:
        if _present(value):
            return value
    return None


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | tuple | set | dict):
        return bool(value)
    return True


def _append_once(items: list[str], item: str) -> list[str]:
    if item not in items:
        items.append(item)
    return items


def _dedupe_strings(items: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for item in items:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key not in seen:
            result.append(text)
            seen.add(key)

    return result


def _clean_preview(text: str, limit: int = 500) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


__all__ = [
    "AgentRun",
    "AgentStep",
    "InvoiceOpsAgent",
    "run_agent",
    "run_invoice_agent",
    "DEFAULT_AGENT_GOAL",
    "REQUIRED_INVOICE_FIELDS",
    "BLOCKING_RISK_FLAGS",
]
