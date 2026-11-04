"""One line per step of a run: the registry type, the tool, and the choice."""

from __future__ import annotations

from typing import Any

from kai.engine.advance import display
from kai.engine.registry import STEP_TYPES

_TYPE_TITLES = {item["type"]: item["title"] for item in STEP_TYPES}
_KIND_LABELS = {
    "source": "Source",
    "trigger": "Trigger",
    "fetch": "Fetch",
    "condition": "Condition",
    "keep_matching": "Pick",
    "ask": "Ask",
    "approval": "Approval",
    "action": "Action",
    "result": "Result",
    "question": "Question",
    "question_popup": "Question",
    "stop": "Stop",
}


def explain_run(
    graph: dict[str, Any] | None,
    trace: list[str] | None,
    slots: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    replies: list[str] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    graph = graph or {}
    slots = slots or {}
    steps = {step.get("id"): step for step in graph.get("steps") or []}
    pending = [str(reply) for reply in (replies or []) if str(reply).strip()]
    spoken = {}
    for message in messages or []:
        if isinstance(message, dict) and message.get("step_id") and message.get("text"):
            spoken.setdefault(message["step_id"], str(message["text"]))
    rows = []
    path = list(trace or [])
    for index, step_id in enumerate(path):
        step = steps.get(step_id) or {"id": step_id, "kind": "", "type": "", "title": step_id, "config": {}}
        kind = str(step.get("kind") or "")
        step_type = str(step.get("type") or "")
        role = str((step.get("config") or {}).get("role") or step_type or "")
        nxt = path[index + 1] if index + 1 < len(path) else ""
        port = _port(graph, step_id, nxt)
        choice = ""
        if kind in {"question", "question_popup", "ask", "approval"} and pending:
            choice = pending.pop(0)
        payload = _payload(events or [], step_id)
        rows.append(
            {
                "id": step_id,
                "kind": kind,
                "kind_label": _KIND_LABELS.get(kind, kind or "Step"),
                "type": step_type,
                "type_label": _type_label(step, role, step_type),
                "port": port,
                "detail": _detail(step, kind, role, port, choice, slots, payload, spoken.get(step_id, "")),
            }
        )
    return rows


def _type_label(step: dict[str, Any], role: str, step_type: str) -> str:
    if role == "credentials":
        return "Sign-in check"
    if step.get("kind") in {"question", "question_popup"}:
        raw = step.get("input") if isinstance(step.get("input"), dict) else {}
        if raw.get("options") or raw.get("type") == "simple_select":
            return "Options"
        return "Free text"
    return _TYPE_TITLES.get(step_type) or str(step.get("title") or step.get("id") or "")


def _port(graph: dict[str, Any], src: str, dst: str) -> str:
    if not dst:
        return ""
    for edge in graph.get("edges") or []:
        if edge.get("from") == src and edge.get("to") == dst:
            return str(edge.get("port") or "")
    return ""


def _payload(events: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for event in events:
        if event.get("step_id") != step_id:
            continue
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            found.update(payload)
    return found


def _detail(step: dict[str, Any], kind: str, role: str, port: str, choice: str, slots: dict[str, Any], payload: dict[str, Any], said: str) -> str:
    if kind == "source":
        return "The customer asked in chat." if step.get("type") != "rule" else "A saved rule fired."
    if kind == "trigger":
        return f"Started from {slots.get('origin') or 'chat'}."
    if kind == "fetch":
        return _fetch(step, role, slots, payload)
    if kind == "condition":
        return _condition(step, role, port, slots)
    if kind in {"question", "question_popup"}:
        return _asked(step, choice, port)
    if kind in {"ask", "approval"}:
        return _asked(step, choice, port)
    if kind == "action":
        amount = display(slots.get("amount"))
        source = slots.get("source") or ""
        target = slots.get("target") or ""
        moved = f" {amount} from {source} to {target}." if source or target else ""
        return f"Tool { _TYPE_TITLES.get(step.get('type') or '') or 'action' }.{moved}".replace("  ", " ")
    if kind in {"result", "stop"}:
        ended = step.get("result") or step.get("id") or ""
        line = f" Said: {said}" if said else ""
        return f"Ended on {ended}.{line}"
    if said:
        return said
    return step.get("purpose") or step.get("title") or ""


def _fetch(step: dict[str, Any], role: str, slots: dict[str, Any], payload: dict[str, Any]) -> str:
    tool = _TYPE_TITLES.get(str(step.get("type") or "")) or "Get balance"
    data = {**slots, **payload}
    if role == "get_source_balance":
        held = display(data.get("source_balance"))
        code = slots.get("source") or ""
        return f"Tool {tool}, on the source. {code} holds {held}.".strip()
    held = display(data.get("target_balance") or data.get("balance"))
    code = slots.get("target") or slots.get("account") or _saved(step, "account") or ""
    if code and held:
        return f"Tool {tool}. Read {code}. It holds {held}."
    if held:
        return f"Tool {tool}. It holds {held}."
    return f"Tool {tool}."


def _condition(step: dict[str, Any], role: str, port: str, slots: dict[str, Any]) -> str:
    taken = f"Took the {port} path." if port else "This was the branch."
    if role == "compare":
        direction = str(_saved(step, "direction") or "below")
        level = _saved(step, "level")
        balance = display(slots.get("target_balance"))
        level_text = "" if level in (None, "") else f" {level}"
        held = f" Balance {balance}." if balance else ""
        return f"Compare, {direction}{level_text}.{held} {taken}".strip()
    if role == "enough":
        have = display(slots.get("source_balance") or slots.get("target_balance"))
        amount = display(slots.get("amount"))
        return f"Is there enough. Spare {have} against {amount}. {taken}".strip()
    if role == "credentials":
        return f"Checked the email and password against the demo account. {taken}"
    if role in {"origin", "from_chat"}:
        return f"Came from {slots.get('origin') or 'chat'}. {taken}"
    title = _TYPE_TITLES.get(str(step.get("type") or "")) or "Condition"
    return f"{title}. {taken}"


def _asked(step: dict[str, Any], choice: str, port: str) -> str:
    raw = step.get("input") if isinstance(step.get("input"), dict) else {}
    ask = raw.get("ask") or step.get("line") or step.get("title") or "Asked"
    if isinstance(ask, list):
        ask = " ".join(str(item) for item in ask if item)
    label, option_port = _option(step, choice)
    if label:
        via = f" The port is {option_port}." if option_port else ""
        return f"Asked “{ask}”. Chose {label}.{via}"
    if choice:
        via = f" Port {port}." if port else ""
        return f"Asked “{ask}”. Answer: {choice}.{via}"
    return f"Asked “{ask}”."


def _option(step: dict[str, Any], choice: str) -> tuple[str, str]:
    if not choice:
        return "", ""
    lowered = " ".join(choice.lower().split())
    raw = step.get("input") if isinstance(step.get("input"), dict) else {}
    for option in raw.get("options") or []:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "")
        port = str(option.get("port") or "")
        if lowered in {label.lower(), port.lower()}:
            return label or port, port
    return "", ""


def _saved(step: dict[str, Any], key: str) -> Any:
    raw = step.get("input")
    if not isinstance(raw, dict) or key not in raw:
        return None
    value = raw.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, str) and (not value.strip() or value.strip().startswith("<")):
        return None
    return value
