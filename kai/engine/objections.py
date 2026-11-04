"""Open-prompt objections. The waiting step owns the port; strikes re-ask until the third."""

from __future__ import annotations

import re
from typing import Any

MAX_OBJECTIONS = 3

_OBJECTION = re.compile(
    r"^\s*("
    r"i\s+don'?t\s+know|don'?t\s+know|dont\s+know|"
    r"not\s+sure|no\s+idea|idk|"
    r"none\s+of\s+(these|those|them)|neither|"
    r"never\s+mind|nevermind|nvm|"
    r"cancel|stop|forget\s+it|"
    r"not\s+now|maybe\s+later|later|"
    r"something\s+else|"
    r"no\s+thanks|no\s+thank\s+you"
    r")\s*[.!]?\s*$",
    re.IGNORECASE,
)

_LEAVE_PORTS = ("objection", "not_now", "no", "cancel")


def is_objection(text: str) -> bool:
    return bool(_OBJECTION.match(" ".join((text or "").split())))


def available_ports(prompt: dict[str, Any] | None) -> set[str]:
    if not prompt:
        return set()
    ports = {str(port) for port in (prompt.get("ports") or []) if port}
    for choice in prompt.get("choices") or []:
        port = choice.get("port") if isinstance(choice, dict) else None
        if port:
            ports.add(str(port))
    return ports


def objection_port(prompt: dict[str, Any] | None) -> str | None:
    ports = available_ports(prompt)
    for name in _LEAVE_PORTS:
        if name in ports:
            return name
    return None


def strike_line(count: int) -> str:
    if count <= 1:
        return "No problem — pick one of the options, or say cancel if you want to stop."
    if count == 2:
        return "Still with you — pick one of the options. One more skip and we'll stop here."
    return "Okay, we can stop here."


def next_objection_strike(
    prompt: dict[str, Any] | None,
    *,
    objection_count: int = 0,
    objection_step: str = "",
) -> dict[str, Any] | None:
    """Return leave or soft re-ask for an objection on this waiting prompt."""
    leave = objection_port(prompt)
    if not leave:
        return None
    step_id = str((prompt or {}).get("step_id") or "")
    count = int(objection_count or 0)
    if objection_step != step_id:
        count = 0
    count += 1
    if count >= MAX_OBJECTIONS:
        return {
            "action": "choice",
            "port": leave,
            "strike": count,
            "reply": "",
            "slots": {"_objection_count": count, "_objection_step": step_id},
        }
    return {
        "action": "answer",
        "port": "",
        "strike": count,
        "reply": strike_line(count),
        "slots": {"_objection_count": count, "_objection_step": step_id},
    }


def apply_objection_to_slots(slots: dict[str, Any], step_id: str) -> dict[str, Any]:
    """Bump the run's objection counter for this step. Returns the strike decision fields."""
    current_step = str(slots.get("_objection_step") or "")
    count = int(slots.get("_objection_count") or 0)
    if current_step != step_id:
        count = 0
    count += 1
    slots["_objection_count"] = count
    slots["_objection_step"] = step_id
    return {"strike": count, "leave": count >= MAX_OBJECTIONS}
