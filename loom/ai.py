from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

StreamUpdate = Callable[[str], None]

DEFAULT_MODEL = "qwen3:8b"
Progress = Callable[[int | None, str], None]


def _no_console_flags() -> int:
    """Suppress transient console/helper windows on Windows."""
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if not sys.platform.startswith("win"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = 0
    return startupinfo


def _apply_hidden_window(kwargs: dict[str, Any]) -> None:
    flags = _no_console_flags()
    if flags:
        kwargs["creationflags"] = flags
    info = _hidden_startupinfo()
    if info is not None:
        kwargs["startupinfo"] = info


def _run_quiet(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    _apply_hidden_window(kwargs)
    return subprocess.run(args, **kwargs)


def _popen_quiet(args: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    _apply_hidden_window(kwargs)
    return subprocess.Popen(args, **kwargs)


def _strip_thinking(text: str) -> str:
    cleaned = text or ""
    lower = cleaned.lower()
    while "<think>" in lower:
        start = lower.find("<think>")
        end = lower.find("</think>", start)
        if end < 0:
            cleaned = cleaned[:start]
            break
        cleaned = (cleaned[:start] + cleaned[end + len("</think>"):]).strip()
        lower = cleaned.lower()
    return cleaned.strip()


@dataclass(slots=True)
class AIStatus:
    installed: bool
    reachable: bool
    model_ready: bool
    version: str | None
    cli_path: str | None
    models: tuple[str, ...]
    message: str


class QwenClient:
    def __init__(self, host: str = "http://127.0.0.1:11434", model: str = DEFAULT_MODEL) -> None:
        self.host = host.rstrip("/")
        self.model = model

    def binary(self) -> str | None:
        found = shutil.which("ollama")
        if found:
            return found
        if sys.platform.startswith("win"):
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Ollama" / "ollama.exe",
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)
        return None

    def version(self, binary: str | None = None) -> str | None:
        binary = binary or self.binary()
        if not binary:
            return None
        try:
            proc = _run_quiet([binary, "--version"], timeout=5)
            return (proc.stdout or proc.stderr).strip()[:160] or None
        except Exception:
            return None

    def tags(self) -> list[str]:
        with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names: list[str] = []
        for item in models:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if isinstance(name, str):
                    names.append(name)
        return names

    def model_ready(self, names: list[str]) -> bool:
        wanted = self.model.lower()
        family = wanted.split(":", 1)[0]
        return any(name.lower() == wanted or name.lower().startswith(f"{family}:") for name in names)

    def status(self, detailed: bool = False) -> AIStatus:
        # Keep periodic status polling HTTP-first. Calling ollama.exe every few
        # seconds can flash a helper console on some Windows builds. Detailed
        # model/version checks are reserved for the Models page and setup flow.
        binary = self.binary()
        version = self.version(binary) if detailed else None
        try:
            names = self.tags()
            ready = self.model_ready(names)
            return AIStatus(
                installed=True,
                reachable=True,
                model_ready=ready,
                version=version,
                cli_path=binary,
                models=tuple(names),
                message=(
                    f"Local AI is ready. {self.model} is available."
                    if ready
                    else f"Ollama is running. Default model {self.model} is not installed yet."
                ),
            )
        except Exception as exc:
            if binary:
                return AIStatus(True, False, False, version, binary, (), f"Ollama CLI found, but service is offline: {exc}")
            return AIStatus(False, False, False, None, None, (), "Ollama was not detected. Use Setup to install or repair local AI.")

    def ensure_ready(self, progress: Progress | None = None) -> str:
        def emit(percent: int, text: str) -> None:
            if progress:
                progress(max(0, min(percent, 100)), text)

        emit(5, "Checking local AI")
        status = self.status()
        if not status.installed:
            emit(12, "Starting Ollama installer")
            self._install_or_open()
            time.sleep(1)
            status = self.status()
            if not status.installed:
                emit(100, "Installer started")
                return "Ollama installer started. Finish the installer, then click setup again."

        if not status.reachable:
            emit(25, "Starting Ollama service")
            self._start_service()
            status = self._wait_for_service(15, progress)
            if not status.reachable:
                emit(100, "Service not reachable")
                return "Ollama is installed, but the service did not start. Open Ollama once, then click setup again."

        try:
            names = self.tags()
            if self.model_ready(names):
                emit(100, "Local AI ready")
                return f"Local AI is ready. {self.model} is available."
        except Exception:
            pass

        emit(40, f"Downloading {self.model}")
        msg = self._pull_model(progress)
        emit(100, "Local AI setup complete")
        return msg

    def _install_or_open(self) -> None:
        if sys.platform.startswith("win"):
            winget = shutil.which("winget")
            if winget:
                try:
                    _popen_quiet(
                        [winget, "install", "-e", "--id", "Ollama.Ollama", "--accept-source-agreements", "--accept-package-agreements"]
                    )
                    return
                except Exception:
                    pass
            webbrowser.open("https://ollama.com/download/windows")
            return
        webbrowser.open("https://ollama.com/download")

    def _start_service(self) -> None:
        binary = self.binary()
        if binary:
            _popen_quiet([binary, "serve"])

    def _wait_for_service(self, seconds: int, progress: Progress | None = None) -> AIStatus:
        deadline = time.time() + seconds
        last = self.status()
        while time.time() < deadline:
            last = self.status()
            if last.reachable:
                if progress:
                    progress(35, "Ollama service is reachable")
                return last
            if progress:
                elapsed = seconds - max(0.0, deadline - time.time())
                progress(25 + int((elapsed / max(seconds, 1)) * 10), "Waiting for Ollama service")
            time.sleep(0.7)
        return last

    def _pull_model(self, progress: Progress | None = None) -> str:
        payload = json.dumps({"name": self.model, "stream": True}).encode("utf-8")
        req = urllib.request.Request(f"{self.host}/api/pull", data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=1800) as response:
                last_percent = 45
                for raw in response:
                    try:
                        item = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    completed = item.get("completed")
                    total = item.get("total")
                    status = str(item.get("status") or "Downloading model")
                    if isinstance(completed, int) and isinstance(total, int) and total > 0:
                        pct = 45 + int((completed / total) * 50)
                        last_percent = max(last_percent, min(pct, 95))
                    else:
                        last_percent = min(last_percent + 1, 92)
                    if progress:
                        progress(last_percent, status)
            return f"Local AI is ready. {self.model} was installed successfully."
        except Exception as api_exc:
            binary = self.binary()
            if binary:
                try:
                    proc = _run_quiet([binary, "pull", self.model], timeout=1800)
                    if proc.returncode == 0:
                        return f"Local AI is ready. {self.model} was installed successfully."
                    detail = (proc.stderr or proc.stdout).strip()[:400]
                    return f"Could not pull {self.model}: {detail or proc.returncode}"
                except Exception as cli_exc:
                    return f"Could not pull {self.model}: API error {api_exc}; CLI error {cli_exc}"
            return f"Could not pull {self.model}: {api_exc}"

    def parse_json(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("` \n")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        for candidate in (cleaned, self._balanced_json(cleaned)):
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
            return {"model_output": parsed}
        return None

    def _balanced_json(self, text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return None

    def generate_json(self, prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        status = self.status()
        if not status.reachable:
            return fallback | {"_model_status": status.message}
        if not status.model_ready:
            return fallback | {"_model_status": f"Ollama is running, but {self.model} is not installed."}
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
        try:
            req = urllib.request.Request(f"{self.host}/api/generate", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=150) as response:
                obj = json.loads(response.read().decode("utf-8"))
            text = str(obj.get("response", "{}"))
            parsed = self.parse_json(text)
            if parsed is not None:
                parsed.setdefault("_model_used", self.model)
                return parsed
            return fallback | {"_model_error": "Local model responded, but did not return parseable JSON.", "_raw_model_response": text[:1400]}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return fallback | {"_model_error": str(exc)}

    def _chat_prompt(self, message: str, context: dict[str, Any] | None = None) -> str:
        context_text = json.dumps(context or {}, ensure_ascii=False)[:16000]
        return (
            "You are SignalLoom's invoice operations copilot. Answer using only the provided extraction context. "
            "Prioritize concrete improvements that reduce manual invoice review, approval routing, AP handoff, and audit-prep time. "
            "Do not include hidden reasoning, <think> blocks, or analysis logs. Use concise markdown sections with short bullets. Do not use wide markdown tables unless the user explicitly asks for a table. "
            "When producing reusable work product, start with a clear title and make it ready to save as a deliverable card. "
            "When facts are missing, say what is missing and suggest the next verification step.\n\n"
            f"Context JSON:\n{context_text}\n\nUser request:\n{message}"
        )

    def stream_chat(self, message: str, context: dict[str, Any] | None = None, on_update: StreamUpdate | None = None) -> str:
        status = self.status()
        if not status.reachable or not status.model_ready:
            answer = _offline_answer(message, context or {})
            if on_update:
                on_update(answer)
            return answer

        payload = {
            "model": self.model,
            "prompt": self._chat_prompt(message, context),
            "stream": True,
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        raw_text = ""
        visible = ""
        try:
            with urllib.request.urlopen(req, timeout=220) as response:
                for raw in response:
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    raw_text += str(item.get("response") or "")
                    next_visible = _strip_thinking(raw_text)
                    if next_visible != visible:
                        visible = next_visible
                        if on_update and visible.strip():
                            on_update(visible)
                    if item.get("done"):
                        break
            answer = visible.strip() or _offline_answer(message, context or {})
            if on_update:
                on_update(answer)
            return answer
        except Exception:
            answer = _offline_answer(message, context or {})
            if on_update:
                on_update(answer)
            return answer

    def chat(self, message: str, context: dict[str, Any] | None = None) -> str:
        return self.stream_chat(message, context, None)


def _offline_answer(message: str, context: dict[str, Any]) -> str:
    review = context.get("review", {}) if isinstance(context, dict) else {}
    fields = context.get("structured_data", {}).get("canonical_fields", {}) if isinstance(context.get("structured_data", {}), dict) else {}
    missing = review.get("missing_fields") or []
    risks = review.get("risk_flags") or []
    vendor = fields.get("vendor") or fields.get("vendors") or "unknown vendor"
    total = fields.get("total_amount") or fields.get("total_amount_sum") or "unknown amount"
    action = review.get("recommended_action") or "Review extracted fields, resolve missing data, then route for approval."
    return (
        "**Invoice status**\n"
        f"- Vendor: {vendor}\n"
        f"- Amount: {total}\n"
        f"- Missing fields: {', '.join(map(str, missing)) if missing else 'none detected'}\n"
        f"- Risk flags: {', '.join(map(str, risks)) if risks else 'none detected'}\n\n"
        "**Recommended next action**\n"
        f"{action}\n\n"
        "**Manual-hours reduction opportunity**\n"
        "- Standardize required fields before approval routing.\n"
        "- Generate the AP packet and Slack approval message from the same source record.\n"
        "- Route only after the reviewer confirms missing fields and exceptions.\n\n"
        f"Requested task: {message}\n"
    )
