from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import urllib.request

APP_DIR = Path.home() / ".signalloom"
CONFIG_FILE = APP_DIR / "services.json"

SUPPORTED_SERVICES = {
    "Slack webhook": "POST approval message/payload to Slack Incoming Webhook.",
    "Zapier webhook": "POST structured JSON to a Zapier Catch Hook.",
    "Make webhook": "POST structured JSON to a Make.com custom webhook.",
    "n8n webhook": "POST structured JSON to an n8n webhook.",
    "Generic webhook": "POST structured JSON to any HTTPS endpoint.",
    "OpenAI key": "Stored for optional user-run integrations; local Qwen remains default.",
    "Anthropic key": "Stored for optional user-run integrations; local Qwen remains default.",
}

ROUTE_KEYS = ["n8n webhook", "Make webhook", "Zapier webhook", "Slack webhook", "Generic webhook"]


def load_services() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_services(config: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass


def post_webhook(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError("Webhook URL must start with https://")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")[:800]
        return {"status": response.status, "body": body}


def route_payload(result: dict[str, Any], services: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for key in ROUTE_KEYS:
        url = services.get(key)
        if url:
            return key, post_webhook(url, result)
    raise ValueError("No webhook URL is configured in Services.")


def workflow_template() -> dict[str, Any]:
    return {
        "name": "Marketing invoice review",
        "steps": [
            "Open invoice or workbook in SignalLoom",
            "Review required fields and exceptions",
            "Ask Qwen to summarize AP handoff if needed",
            "Send approval packet to n8n/Make/Zapier/Slack webhook",
            "Human approver confirms or rejects",
            "AP receives validated packet and source hash",
        ],
        "automation_target": "Reduce manual review, copy/paste, and approval routing time.",
    }
