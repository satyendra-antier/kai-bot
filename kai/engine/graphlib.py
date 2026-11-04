"""Small helpers for building a workflow graph and fitting a story path."""

from __future__ import annotations

import copy


def step(
    step_id: str,
    kind: str,
    step_type: str | None,
    purpose: str,
    x: int,
    y: int,
    line: str | None = None,
    role: str | None = None,
    result: str | None = None,
) -> dict:
    config: dict = {}
    if role:
        config["role"] = role
    return {
        "id": step_id,
        "kind": kind,
        "type": step_type or "",
        "title": purpose,
        "purpose": purpose,
        "line": line,
        "position": {"x": x, "y": y},
        "speed_target_ms": 2000,
        "config": config,
        "result": result,
    }


def edge(from_step: str, to_step: str, port: str) -> dict:
    return {"id": f"{from_step}:{port}:{to_step}", "from": from_step, "to": to_step, "port": port}


def layout(rows: list[tuple]) -> list[dict]:
    built = []
    for index, row in enumerate(rows):
        step_id, kind, step_type, purpose = row[:4]
        line = row[4] if len(row) > 4 else None
        role = row[5] if len(row) > 5 else None
        result = row[6] if len(row) > 6 else None
        built.append(step(step_id, kind, step_type, purpose, 48, index * 132, line, role, result))
    return built


def chain(pairs: list[tuple[str, str, str]]) -> list[dict]:
    return [edge(src, dst, port) for src, dst, port in pairs]


LANE_X = {"source": 24, "continue": 300, "approve": 576, "reject": 852}
ONBOARD_X = {"source": 24, "question": 300, "response": 576, "sumsub": 852, "score": 1128, "stop": 1404}
_ONBOARD = {"question", "question_popup", "user_response", "user_response_popup", "sumsub", "score"}
_FAIL = {"false", "no", "reject"}

_SAMPLES = {
    "get_source_balance": ({"account": "EUR", "amount": 100}, {"available": 62.4, "ccy": "EUR", "floor": 0, "spare": 62.4}),
    "source_enough": ({"spare": 62.4, "amount": 100}, {"result": "false", "short_by": 37.6}),
    "check_other_accounts": ({"customer": "@you", "which": "except", "exclude": ["EUR"]}, {"accounts": [{"account": "USDT", "available": 950, "ccy": "USDT", "floor": 0}]}),
    "any_sufficient": ({"available": 950, "amount": 100}, {"result": "true", "short_by": 0}),
    "get_balance": ({"account": "USD"}, {"available": 12, "ccy": "USD", "floor": 0, "spare": 12}),
    "above": ({"value": 210, "level": 50, "direction": "above"}, {"result": "yes | no"}),
    "below": ({"value": 12, "level": 50, "direction": "below"}, {"result": "yes | no"}),
    "needs_topup": ({"value": 12, "level": 100, "direction": "below"}, {"result": "yes | no"}),
    "confirm_request": ({"request": {"target": "USD", "amount": 100, "source": "EUR"}}, {"confirmed": True}),
    "offer_account": ({"amount": 100, "can_cover": ["USDT", "GBP"]}, {"options": ["USDT", "GBP"], "chosen": "USDT"}),
    "approve_topup": ({"amount": 100, "from": "EUR", "to": "USD", "quote_id": "q-1"}, {"decision": "approve | reject", "by": "@you"}),
    "get_move_quote": ({"move_type": "internal", "from": "EUR", "to": "USD", "send": 100}, {"quote_id": "q-1", "rate": 1.08, "fee": 0, "send": 100, "receive": 108, "valid_s": 30}),
}


def _lane_of(step: dict, incoming: dict[str, list[str]], onboarding: bool) -> str:
    kind = step.get("kind")
    if onboarding:
        if kind in {"source", "trigger"}:
            return "source"
        if kind in {"question", "question_popup"}:
            return "question"
        if kind in {"user_response", "user_response_popup"}:
            return "response"
        if kind == "sumsub":
            return "sumsub"
        if kind == "score":
            return "score"
        if kind == "stop":
            return "stop"
        return "question"
    ports = incoming.get(step["id"]) or []
    if kind == "source":
        return "source"
    if kind == "approval":
        return "approve"
    if kind in {"result", "stop"} and ports and all(port in _FAIL for port in ports):
        return "reject"
    return "continue"


def _flow_order(steps: list[dict], edges: list[dict], start: str | None = None) -> list[str]:
    ids = [step["id"] for step in steps]
    known = set(ids)
    outgoing: dict[str, list[dict]] = {}
    for item in edges:
        outgoing.setdefault(item["from"], []).append(item)
    for items in outgoing.values():
        items.sort(key=lambda item: (item.get("port") != "continue", str(item.get("port") or "")))
    ordered: list[str] = []
    seen: set[str] = set()

    def walk(step_id: str) -> None:
        if step_id in seen or step_id not in known:
            return
        seen.add(step_id)
        ordered.append(step_id)
        for item in outgoing.get(step_id, []):
            walk(item["to"])

    if start:
        walk(start)
    for step_id in ids:
        walk(step_id)
    return ordered


def _rows_along_the_flow(steps: list[dict], edges: list[dict], lane_of: dict[str, str], start: str | None = None) -> dict[str, int]:
    """A step sits on the same row as the step before it, unless that row is taken."""
    order = _flow_order(steps, edges, start)
    incoming: dict[str, list[dict]] = {}
    for item in edges:
        incoming.setdefault(item["to"], []).append(item)
    rows: dict[str, int] = {}
    used: dict[str, set[int]] = {}

    def claim(step_id: str, preferred: int) -> None:
        lane = lane_of[step_id]
        taken = used.setdefault(lane, set())
        row = max(0, preferred)
        while row in taken:
            row += 1
        taken.add(row)
        rows[step_id] = row

    for step_id in order:
        previous = [item for item in incoming.get(step_id, []) if item["from"] in rows]
        continue_from = next((item for item in previous if item.get("port") == "continue"), None)
        pred = continue_from or (previous[0] if previous else None)
        if pred is None:
            if step_id == start:
                claim(step_id, 0)
            else:
                lowest = max((row for taken in used.values() for row in taken), default=-1)
                claim(step_id, lowest + 1)
            continue
        pred_row = rows[pred["from"]]
        claim(step_id, pred_row + 1 if lane_of[pred["from"]] == lane_of[step_id] else pred_row)
    return rows


def assign_lanes(steps: list[dict], edges: list[dict], entries: dict | None = None) -> None:
    incoming: dict[str, list[str]] = {}
    for item in edges:
        incoming.setdefault(item["to"], []).append(item["port"])
    onboarding = any(step.get("kind") in _ONBOARD for step in steps)
    columns = ONBOARD_X if onboarding else LANE_X
    lane_of = {step["id"]: _lane_of(step, incoming, onboarding) for step in steps}
    if onboarding:
        start = (entries or {}).get("chat") or next(iter((entries or {}).values()), None)
        rows = _rows_along_the_flow(steps, edges, lane_of, start)
        for step in steps:
            step["position"] = {"x": columns[lane_of[step["id"]]], "y": 56 + rows[step["id"]] * 168}
        return
    buckets = {name: [] for name in columns}
    for step in steps:
        buckets[lane_of[step["id"]]].append(step)
    for lane, items in buckets.items():
        for index, step in enumerate(items):
            step["position"] = {"x": columns[lane], "y": 48 + index * 140}


def stamp_io(steps: list[dict]) -> None:
    from kai.engine.registry import STEP_TYPES

    types = {item["type"]: item for item in STEP_TYPES}
    for step in steps:
        if step.get("input") not in (None, {}, ""):
            continue
        spec = types.get(step.get("type") or "")
        role = str((step.get("config") or {}).get("role") or step.get("id") or "")
        sample = _SAMPLES.get(role) or _SAMPLES.get(step.get("id") or "")
        if sample:
            step["input"] = copy.deepcopy(sample[0])
            step["output"] = copy.deepcopy(sample[1])
        elif spec:
            step["input"] = copy.deepcopy(spec["input"])
            step["output"] = copy.deepcopy(spec["output"])
        else:
            step["input"] = None
            step["output"] = None


def finish_graph(steps: list[dict], edges: list[dict], entries: dict | None = None) -> None:
    assign_lanes(steps, edges, entries)
    stamp_io(steps)


def fit(path: list[str], length: int, pool: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in path:
        if item in pool and item not in seen:
            seen.add(item)
            ordered.append(item)
    if not ordered:
        ordered = [pool[0]]
    end = ordered[-1]
    body = ordered[:-1]
    if len(ordered) > length:
        body = body[: length - 1]
        return body + [end]
    extras = [item for item in pool if item not in seen]
    while len(body) + 1 < length and extras:
        body.append(extras.pop(0))
    if len(body) + 1 != length:
        raise ValueError(f"cannot fit a path of {length} from {len(pool)} steps")
    return body + [end]


def compile_paths(steps: list[dict], stories: list[dict], entries: dict[str, str], budget: int = 220) -> dict:
    pool = [item["id"] for item in steps]
    outgoing: dict[str, list[str]] = {}
    for story in stories:
        path = fit(list(story["path"]), int(story["expected_steps"]), pool)
        story["path"] = path
        story["slots"] = {**(story.get("slots") or {}), "_path": path}
        story["expected_result"] = path[-1]
        story.setdefault("replies", [])
        story.setdefault("ledger", {"accounts": {}})
        story["origin"] = "handoff" if path[0] == "handoff_from_individual" else story.get("origin") or "chat"
        for src, dst in zip(path, path[1:]):
            targets = outgoing.setdefault(src, [])
            if dst not in targets:
                targets.append(dst)
    edges = []
    for src, targets in outgoing.items():
        for index, dst in enumerate(targets):
            port = "continue" if len(targets) == 1 else f"p{index}"
            edges.append(edge(src, dst, port))
    finish_graph(steps, edges, entries)
    return {"budget": budget, "entries": entries, "steps": steps, "edges": edges}
