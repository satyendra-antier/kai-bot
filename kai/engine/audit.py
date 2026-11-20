"""Canonical audit steps for chat turns and graph runs.

Every decision Kai makes should leave a step shaped like:
  {id, label, detail, data?}

Chat turns store the list on Message.payload.ai_steps.
Graph runs store typed rows in RunEvent (one row per hop).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def step(step_id: str, label: str, detail: str = "", **data: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": step_id, "label": label, "detail": detail or ""}
    clean = {key: value for key, value in data.items() if value not in (None, "", [], {})}
    if clean:
        row["data"] = clean
    return row


def append(steps: list[dict[str, Any]] | None, *rows: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(steps or [])
    for row in rows:
        if row:
            out.append(row)
    return out


def run_event(
    event_type: str,
    step_id: str,
    *,
    port: str = "",
    text: str | None = None,
    payload: dict[str, Any] | None = None,
    retries: int = 0,
) -> dict[str, Any]:
    body = dict(payload or {})
    if port:
        body.setdefault("port", port)
    if text is not None:
        body.setdefault("text", text)
    return {
        "type": event_type,
        "step_id": step_id,
        "port": port,
        "text": text,
        "payload": body,
        "retries": retries,
        "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def turn_trail(
    *,
    pii: list[str] | None = None,
    tool: str = "",
    local: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    model_used: bool = False,
    model_skipped: str = "",
    chosen: dict[str, Any] | None = None,
    route: str = "",
    guard: dict[str, Any] | None = None,
    strike: int | None = None,
) -> list[dict[str, Any]]:
    """Build the full chat-turn audit list in decision order."""
    steps: list[dict[str, Any]] = []
    kinds = list(pii or [])
    steps.append(step("redact", "Redact", ", ".join(kinds) if kinds else "Nothing held back", pii=kinds))
    if tool:
        steps.append(step("tool", "Tool", tool, tool=tool))
    if local:
        steps.append(
            step(
                "local",
                "Local scorer",
                _decision_detail(local),
                action=local.get("action"),
                workflow_key=local.get("workflow_key"),
                confidence=local.get("confidence"),
                slots=_slot_keys(local.get("slots")),
            )
        )
    if model_used and model:
        steps.append(
            step(
                "model",
                "Gemini",
                _decision_detail(model),
                action=model.get("action"),
                workflow_key=model.get("workflow_key"),
                confidence=model.get("confidence"),
                slots=_slot_keys(model.get("slots")),
                source=model.get("source") or "gemini",
            )
        )
    elif model_skipped:
        steps.append(step("model", "Gemini", model_skipped, skipped=True, reason=model_skipped))
    if route:
        detail = route
        if strike:
            detail = f"{route} · strike {strike} of 3"
        steps.append(step("route", "Route", detail, route=route, strike=strike or 0))
    if guard:
        steps.append(
            step(
                "guard",
                "Guard",
                f"{guard.get('action')} {guard.get('workflow_key') or guard.get('reply') or ''}".strip(),
                action=guard.get("action"),
                workflow_key=guard.get("workflow_key"),
            )
        )
    picked = chosen or guard or model or local or {}
    steps.append(
        step(
            "decision",
            "Decision",
            _decision_detail(picked),
            action=picked.get("action"),
            workflow_key=picked.get("workflow_key"),
            confidence=picked.get("confidence"),
            needs=picked.get("needs"),
            slots=_slot_keys(picked.get("slots")),
            port=picked.get("port"),
            source=picked.get("source") or ("gemini" if model_used else "deterministic"),
        )
    )
    return steps


def _slot_keys(slots: Any) -> list[str]:
    if not isinstance(slots, dict):
        return []
    return sorted(str(key) for key in slots.keys())


def _decision_detail(decision: dict[str, Any]) -> str:
    action = decision.get("action") or "chat"
    key = decision.get("workflow_key") or decision.get("port") or ""
    conf = decision.get("confidence")
    parts = [str(action)]
    if key:
        parts.append(str(key))
    if conf is not None and conf != "":
        parts.append(f"@ {conf}")
    slots = _slot_keys(decision.get("slots"))
    if slots:
        parts.append("slots " + ", ".join(slots))
    needs = decision.get("needs")
    if needs and needs != "none":
        parts.append(f"needs {needs}")
    return " · ".join(parts)
