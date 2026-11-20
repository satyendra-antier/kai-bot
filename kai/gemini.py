"""Gemini calls used only by worker activities."""

from __future__ import annotations

import json
from typing import Any

from kai.config import GEMINI_API_KEY, GEMINI_MODEL, apply_langsmith_env, langsmith_enabled


def enabled() -> bool:
    return bool(GEMINI_API_KEY)


def _client():
    from google import genai

    return genai.Client(api_key=GEMINI_API_KEY)


def _json(prompt: str) -> dict[str, Any] | None:
    apply_langsmith_env()
    if langsmith_enabled():
        try:
            from langsmith import traceable

            return traceable(name="kai.gemini.json", run_type="llm")(_json_body)(prompt)
        except Exception:
            pass
    return _json_body(prompt)


def _json_body(prompt: str) -> dict[str, Any] | None:
    if not enabled():
        return None
    try:
        response = _client().models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={"temperature": 0, "response_mime_type": "application/json"},
        )
        text = getattr(response, "text", None) or ""
        return json.loads(text)
    except Exception:
        return None


def classify(text: str, contracts: list[dict[str, Any]]) -> dict[str, Any] | None:
    catalog = [
        {
            "workflow_key": item["workflow_key"],
            "description": item["contract"].get("description"),
            "examples": item["contract"].get("examples"),
            "exclusions": item["contract"].get("exclusions"),
        }
        for item in contracts
    ]
    parsed = _json(
        "Score which workflow the customer wants. Use the description, not the exact words. "
        "Exclusions mean this workflow is the wrong home. Return JSON "
        '{"workflow_key": string, "confidence": number between 0 and 1, "reason": string}.\n'
        f"Workflows: {json.dumps(catalog)}\nUtterance: {text}"
    )
    return parsed


def extract(text: str, slots: list[dict[str, Any]]) -> dict[str, Any] | None:
    parsed = _json(
        "Fill only the slots the message itself states. Return JSON "
        '{"slots": {key: {"value": string or number, "currency": optional string}}}.\n'
        f"Slots: {json.dumps(slots)}\nUtterance: {text}"
    )
    return parsed


def interpret(text: str, catalog: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any] | None:
    parsed = _json(
        "You route one customer message for Kai. The loaded chat workflows decide what is possible. "
        "Choose from those workflows and from what the customer just said. "
        "Return JSON with action (start, fill, answer, or chat), workflow_key (a loaded key, or empty), "
        "confidence (0 to 1), slots (only values the message itself states; amount is a number or string), "
        "needs (accounts, cards, or none), and reply (one or two sentences). "
        "start begins a workflow. fill answers the slot that workflow is waiting for. "
        "When state.waiting is set, the customer is on an open question. "
        "If they clearly want a different loaded workflow, return start for that workflow. "
        "If they are answering the open question, do not invent a port; the buttons handle that. "
        "answer is for a question, including asking what is available or to list a card or account; "
        "set needs to accounts or cards when they want balances, and do not put those words into slots. "
        "chat is a greeting or anything that is not one of these workflows. "
        "Do not invent balances, rates, fees, or account codes. The reply must not contain a number "
        "unless that number is in the message or in the account facts. "
        "Exclusions mean that workflow is the wrong one.\n"
        f"State: {json.dumps(state)}\nWorkflows: {json.dumps(catalog)}\nCustomer: {text}"
    )
    return parsed


def normal_chat(text: str, never: list[dict[str, Any]]) -> str | None:
    parsed = _json(
        "Answer as Kai in one or two sentences. Obey every never-rule. "
        'Return JSON {"text": string}.\n'
        f"Rules: {json.dumps(never)}\nCustomer: {text}"
    )
    if not parsed:
        return None
    return str(parsed.get("text") or parsed.get("reply") or parsed.get("message") or "") or None


def judge_line(expected: str, actual: str) -> bool | None:
    parsed = _json(
        "Does the actual sentence keep the meaning of the expected sentence, and does every "
        "number and currency from the expected sentence still appear? "
        'Return JSON {"ok": true or false}.\n'
        f"Expected: {expected}\nActual: {actual}"
    )
    if not parsed:
        return None
    return bool(parsed.get("ok"))
