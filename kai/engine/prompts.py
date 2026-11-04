"""Waiting prompts. The step config owns the buttons and fields the chat shows."""

from __future__ import annotations

import re
from typing import Any

from kai.engine.advance import render_line


def _slug(label: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return token or "answer"


def default_choices(step: dict[str, Any]) -> list[dict[str, str]]:
    config = step.get("config") or {}
    saved = config.get("choices") or []
    if saved:
        return [{"port": str(item.get("port") or ""), "label": str(item.get("label") or item.get("port") or "")} for item in saved if item.get("port")]
    kind = step.get("kind")
    step_type = step.get("type")
    role = str(config.get("role") or "")
    if kind == "approval" or step_type in {"approve_money", "approve_next"}:
        return [{"port": "approve", "label": "Approve"}, {"port": "reject", "label": "Reject"}]
    if step_type == "confirm" or role.startswith("confirm"):
        return [{"port": "yes", "label": "Yes"}, {"port": "no", "label": "No"}]
    if role == "offer_alternative":
        return [{"port": "all", "label": "All of it"}, {"port": "no", "label": "No"}]
    return []


def offer_choices(step: dict[str, Any], slots: dict[str, Any]) -> list[dict[str, str]]:
    choices = []
    for item in slots.get("options_list") or []:
        if isinstance(item, dict):
            code = str(item.get("code") or "")
            label = str(item.get("label") or item.get("display") or code)
        else:
            code = str(item)
            label = code
        if code:
            choices.append({"port": code, "label": label})
    role = str((step.get("config") or {}).get("role") or "")
    if role == "offer_alternative":
        choices.append({"port": "all", "label": "All of it"})
    return choices


def question_choices(step: dict[str, Any]) -> list[dict[str, str]]:
    raw = step.get("input") or {}
    if raw.get("type") != "simple_select":
        return []
    choices = []
    for option in raw.get("options") or []:
        if isinstance(option, dict):
            label = str(option.get("label") or "")
            port = str(option.get("port") or _slug(label))
        else:
            label = str(option)
            port = _slug(label)
        if label:
            choices.append({"port": port, "label": label})
    return choices


_STEP_LABEL = re.compile(r"^(?:popup\s*[·•]\s*|\d+[a-z]?\s*[·•]\s*)", re.IGNORECASE)


def customer_ask(text: str) -> str:
    """Turn an internal step label into the sentence Kai says."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    cleaned = raw
    while True:
        nxt = _STEP_LABEL.sub("", cleaned).strip()
        if not nxt or nxt == cleaned:
            break
        cleaned = nxt
    if "?" in cleaned or cleaned.lower().startswith(("let's ", "lets ", "what ", "which ", "how ", "please ")):
        return cleaned
    return f"What is {cleaned[0].lower() + cleaned[1:]}?"


def question_line(step: dict[str, Any] | None) -> str:
    """The line a question step speaks: its ask, or each free-text field."""
    if not step:
        return ""
    raw = step.get("input") if isinstance(step.get("input"), dict) else {}
    ask = raw.get("ask")
    if isinstance(ask, list) and any(ask):
        text = " ".join(str(item) for item in ask if item)
    elif isinstance(ask, str) and ask.strip():
        text = ask
    else:
        fields = [item.get("ask") for item in (raw.get("fields") or []) if isinstance(item, dict) and item.get("ask")]
        text = " ".join(str(item) for item in fields)
    return customer_ask(text)


def build_prompt(
    step: dict[str, Any] | None,
    slots: dict[str, Any] | None = None,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not step:
        return None
    slots = slots or {}
    kind = step.get("kind")
    step_type = step.get("type")
    role = str((step.get("config") or {}).get("role") or "")
    raw = step.get("input") or {}
    if kind in {"question", "question_popup"}:
        choices = question_choices(step)
    elif step_type == "offer" or role in {"offer_account", "offer_alternative"}:
        choices = offer_choices(step, slots) or default_choices(step)
    elif kind in {"ask", "approval"} or step_type in {"confirm", "approve_money", "approve_next"}:
        choices = default_choices(step)
    else:
        return None
    text = question_line(step) or render_line(step.get("line") or "", slots)
    config_asks = (step.get("config") or {}).get("asks")
    if not text and isinstance(config_asks, list) and config_asks:
        text = " ".join(str(line) for line in config_asks)
    elif not text and isinstance(config_asks, str):
        text = config_asks
    fields = [] if choices else (raw.get("fields") or [])
    step_id = step.get("id")
    ports = {
        str(edge.get("port"))
        for edge in ((graph or {}).get("edges") or [])
        if edge.get("from") == step_id and edge.get("port")
    }
    for choice in choices:
        if choice.get("port"):
            ports.add(str(choice["port"]))
    return {
        "step_id": step_id,
        "text": text,
        "choices": choices,
        "fields": fields,
        "answer_type": raw.get("type") or ("free_text" if fields else ""),
        "skip": bool((step.get("config") or {}).get("skip") or raw.get("skip")),
        "ports": sorted(ports),
    }


def match_choice(text: str, prompt: dict[str, Any] | None) -> str | None:
    if not prompt:
        return None
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return None
    for choice in prompt.get("choices") or []:
        label = " ".join(str(choice.get("label") or "").lower().split())
        port = str(choice.get("port") or "")
        if lowered == label or lowered == port.lower():
            return port
    return None
