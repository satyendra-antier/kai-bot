"""Deterministic graph stepper shared by stories, Try it, eval replay, and Temporal."""

from __future__ import annotations

import copy
import re
from typing import Any


def num(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return float(value.get("amount") or 0)
    text = str(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def display(value: Any) -> str:
    if isinstance(value, dict) and value.get("display"):
        return str(value["display"])
    if value is None:
        return ""
    return str(value)


def render_line(template: str | None, slots: dict[str, Any]) -> str:
    if not template:
        return ""

    def repl(match: re.Match[str]) -> str:
        return display(slots.get(match.group(1), ""))

    return re.sub(r"\{(\w+)\}", repl, template)


def _steps(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in graph["steps"]}


def _edge(graph: dict[str, Any], step_id: str, port: str) -> str | None:
    for edge in graph["edges"]:
        if edge["from"] == step_id and edge["port"] == port:
            return edge["to"]
    return None


def _blank(step_id: str, slots: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "status": "active",
        "slots": slots,
        "trace": [],
        "messages": [],
        "events": [],
        "result": None,
        "pending": None,
        "entered": None,
        "spoke": False,
        "move_id": None,
    }


def initial_state(graph: dict[str, Any], origin: str, slots: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = copy.deepcopy(slots or {})
    merged["origin"] = origin
    entry = graph["entries"][origin]
    state = _blank(entry, merged)
    return drive(state, graph, None)


def _role(step: dict[str, Any]) -> str:
    return str((step.get("config") or {}).get("role") or step.get("type") or step["id"])


def _saved(step: dict[str, Any], key: str) -> Any:
    raw = step.get("input")
    if not isinstance(raw, dict) or key not in raw:
        return None
    value = raw[key]
    if isinstance(value, str) and (not value.strip() or value.strip().startswith("<")):
        return None
    return value


def _prefer(slots: dict[str, Any], slot_key: str, step: dict[str, Any], input_key: str) -> Any:
    current = slots.get(slot_key)
    if current not in (None, ""):
        return current
    return _saved(step, input_key)


def resolve_fetch(step: dict[str, Any], slots: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    accounts: dict[str, Any] = ledger.get("accounts") or {}
    role = _role(step)
    if role == "get_source_balance":
        code = str(_prefer(slots, "source", step, "account") or slots.get("source") or "")
        account = accounts[code]
        shown = _money(account)
        amount = _saved(step, "amount")
        if amount is not None and slots.get("amount") in (None, ""):
            slots["amount"] = amount
        return {"source_balance": shown, "source_balance_num": account["amount"]}
    if role == "check_other_accounts":
        return _other_accounts(slots, accounts)
    if role in {"get_balance", "get_card_balance"}:
        code = slots.get("target") or slots.get("account") or slots.get("source")
        if role == "get_card_balance" and not slots.get("target"):
            code = slots.get("account") or "CARD"
        if not code:
            code = _saved(step, "account")
        account = accounts.get(str(code or "")) or {"amount": 0, "currency": code or ""}
        shown = _money(account)
        return {
            "target_balance": shown,
            "target_balance_num": account.get("amount") or 0,
            "balance": shown,
            "available": account.get("amount") or 0,
            "_line": _balance_line(str(code or ""), shown, ledger),
        }
    if role in {"get_source_accounts", "get_funding_accounts"}:
        rows = []
        for code, account in accounts.items():
            if account.get("kind") == "card" and role != "get_funding_accounts":
                continue
            if role == "get_funding_accounts" and account.get("kind") == "card":
                continue
            if code == slots.get("target"):
                continue
            spare = float(account["amount"]) - float(account.get("floor") or 0)
            rows.append({"code": code, "spare": spare, "display": _money(account), "floor": account.get("floor") or 0})
        rows.sort(key=lambda row: row["code"])
        return {"candidates": rows}
    if role == "get_move_quote":
        return _move_quote(slots, ledger, accounts)
    if role == "get_rate":
        rate = float(slots.get("rate_now") if slots.get("rate_now") is not None else ledger.get("rate") or 0)
        return {"rate": rate, "rate_now": rate}
    return {}


def resolve_action(step: dict[str, Any], slots: dict[str, Any], ledger: dict[str, Any], move_id: str) -> dict[str, Any]:
    if _role(step) not in {"move_funds", "move_money"} and step.get("type") not in {"move_money", "move_funds"}:
        return {"action": step.get("type"), "ok": True}
    if slots.get("origin") == "rule" and slots.get("top_up_to") is not None:
        target_after = _money({"amount": num(slots["top_up_to"]), "currency": _currency_of(slots.get("target"), ledger.get("accounts") or {})})
    else:
        current = num(slots.get("target_balance"))
        arrival = num(slots.get("est"))
        target_after = _money(
            {"amount": round(current + arrival, 2), "currency": _currency_of(slots.get("target"), ledger.get("accounts") or {})}
        )
    return {"move_id": move_id, "target_after": target_after, "ok": True}


def drive(state: dict[str, Any], graph: dict[str, Any], observation: dict[str, Any] | None) -> dict[str, Any]:
    state = copy.deepcopy(state)
    steps = _steps(graph)
    budget = int(graph.get("budget") or 40)
    hops = 0
    while hops < budget:
        hops += 1
        step = steps[state["step_id"]]
        if state.get("entered") != step["id"]:
            state["trace"].append(step["id"])
            state["entered"] = step["id"]
            state["spoke"] = False
        if observation and observation.get("step_id") == step["id"]:
            _apply_observation(state, step, observation)
            port = observation["port"]
            if step["kind"] in {"question", "question_popup"} and _edge(graph, step["id"], port) is None:
                port = _existing_port(graph, step["id"], "continue")
            required = _requirement_target(graph, step, state["slots"])
            observation = None
            if required:
                state["slots"]["_arrived_by"] = port
                state["step_id"] = required
                continue
            nxt = _edge(graph, step["id"], port)
            if nxt is None:
                if not _outgoing(graph, step["id"]):
                    _end_here(state, step)
                    return state
                state["status"] = "blocked"
                state["pending"] = None
                state["result"] = "missing_edge"
                return state
            state["slots"]["_arrived_by"] = port
            state["step_id"] = nxt
            continue
        kind = step["kind"]
        path = state["slots"].get("_path") or []
        if path and step["id"] in path:
            if step["id"] == path[-1]:
                _speak(state, step)
                state["status"] = "completed"
                state["pending"] = None
                state["result"] = step.get("result") or step["id"]
                return state
            port = _scripted_port(state, graph, step)
            if port:
                if not _jump(state, graph, step, port):
                    return state
                continue
        if state["slots"].get("pause_at") == step["id"]:
            _speak(state, step)
            state["status"] = "completed"
            state["pending"] = None
            state["result"] = step.get("result") or step["id"]
            return state
        if kind in {"source", "trigger"}:
            if kind == "source":
                _speak(state, step)
            if kind == "trigger":
                state["slots"]["request"] = {
                    "target": state["slots"].get("target"),
                    "amount": state["slots"].get("amount"),
                    "source": state["slots"].get("source"),
                    "origin": state["slots"].get("origin"),
                }
            if not _jump(state, graph, step, "continue"):
                return state
            continue
        if kind == "condition":
            port = _condition_port(step, state["slots"])
            if _edge(graph, step["id"], port) is None:
                port = _existing_port(graph, step["id"], "continue")
            state["events"].append(
                {
                    "type": "condition",
                    "step_id": step["id"],
                    "port": port,
                    "payload": {
                        "port": port,
                        "role": _role(step),
                        "type": step.get("type") or "",
                        "kind": kind,
                    },
                }
            )
            if not _jump(state, graph, step, port):
                return state
            continue
        if kind == "keep_matching":
            port = _pick(step, state["slots"])
            state["events"].append(
                {
                    "type": "pick",
                    "step_id": step["id"],
                    "port": port,
                    "payload": {"port": port, "role": _role(step), "kind": kind},
                }
            )
            if not _jump(state, graph, step, port):
                return state
            continue
        if kind == "fetch":
            state["status"] = "active"
            state["pending"] = "fetch"
            return state
        if kind == "action":
            state["status"] = "active"
            state["pending"] = "action"
            return state
        if kind in {"ask", "approval"}:
            _speak(state, step)
            state["status"] = "waiting"
            state["pending"] = "wait"
            return state
        if kind == "result" or kind == "stop":
            arrived = str(state["slots"].get("_arrived_by") or "continue")
            if kind == "stop" and arrived == "continue" and not state["slots"].get("_path") and _edge(graph, step["id"], "continue"):
                if not _jump(state, graph, step, "continue"):
                    return state
                continue
            _speak(state, step)
            state["status"] = "completed"
            state["pending"] = None
            state["result"] = step.get("result") or step["id"]
            return state
        if kind in {"question", "question_popup"}:
            raw = step.get("input") or {}
            if raw.get("skip") or (step.get("config") or {}).get("skip"):
                if not _jump(state, graph, step, _existing_port(graph, step["id"], "continue")):
                    return state
                continue
            _speak(state, step)
            state["status"] = "waiting"
            state["pending"] = "wait"
            return state
        if kind in {"user_response", "user_response_popup"}:
            reply = state["slots"].get("_last_reply")
            if reply not in (None, ""):
                state["slots"].setdefault("answers", {})[step["id"]] = reply
            required = _requirement_target(graph, step, state["slots"])
            if required:
                state["slots"]["_arrived_by"] = state["slots"].get("_chosen_port") or "continue"
                state["step_id"] = required
                continue
            if not _jump(state, graph, step, _response_port(graph, step, state["slots"])):
                return state
            continue
        if kind in {"sumsub", "score", "review", "handoff", "baas"}:
            if not _jump(state, graph, step, "continue"):
                return state
            continue
        state["status"] = "blocked"
        state["pending"] = None
        state["result"] = "unknown_kind"
        return state
    state["status"] = "blocked"
    state["pending"] = None
    state["result"] = "budget"
    return state


def run_until_wait(
    graph: dict[str, Any],
    *,
    origin: str,
    slots: dict[str, Any] | None = None,
    ledger: dict[str, Any] | None = None,
    replies: list[str] | None = None,
    run_id: str = "sim",
    moves: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Walk a graph, resolving fetch and action locally, consuming scripted replies."""

    ledger = ledger or {"accounts": {}}
    pending_replies = list(replies or [])
    moves = moves if moves is not None else {}
    state = initial_state(graph, origin, slots)
    guard = 0
    while state["status"] != "completed" and state["status"] != "blocked" and guard < int(graph.get("budget") or 40) + 5:
        guard += 1
        step = _steps(graph)[state["step_id"]]
        if state["pending"] == "fetch":
            data = resolve_fetch(step, state["slots"], ledger)
            state = drive(
                state,
                graph,
                {"step_id": step["id"], "port": "continue", "data": data, "event": {"type": "fetch", "step_id": step["id"], "payload": data}},
            )
            continue
        if state["pending"] == "action":
            key = f"{run_id}:{step['id']}"
            if key not in moves:
                moves[key] = f"move_{key}"
            data = resolve_action(step, state["slots"], ledger, moves[key])
            state = drive(
                state,
                graph,
                {
                    "step_id": step["id"],
                    "port": "continue",
                    "data": data,
                    "event": {"type": "action", "step_id": step["id"], "payload": data, "retries": 0},
                },
            )
            continue
        if state["pending"] == "wait":
            if not pending_replies:
                return state
            text = pending_replies.pop(0)
            soft = soft_objection_reask(step, text, state["slots"], graph)
            if soft:
                state["messages"].append({"role": "kai", "text": soft["reply"], "step_id": step["id"]})
                state["pending"] = "wait"
                continue
            port = reply_port(step, text, state["slots"], graph)
            data = reply_slots(step, text, state["slots"], graph)
            state = drive(
                state,
                graph,
                {
                    "step_id": step["id"],
                    "port": port,
                    "data": data,
                    "event": {"type": "reply", "step_id": step["id"], "text": text, "port": port},
                },
            )
            continue
        return state
    return state


def soft_objection_reask(
    step: dict[str, Any],
    text: str | None,
    slots: dict[str, Any],
    graph: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """First two objections on a list/question re-ask; the third leaves via reply_port."""
    if not text or step.get("kind") not in {"question", "question_popup"}:
        return None
    from kai.engine.objections import apply_objection_to_slots, is_objection, objection_port, strike_line
    from kai.engine.prompts import build_prompt, match_choice

    prompt = build_prompt(step, slots, graph)
    if match_choice(text, prompt):
        return None
    if not is_objection(text) or not objection_port(prompt):
        return None
    struck = apply_objection_to_slots(slots, str(step.get("id") or ""))
    if struck["leave"]:
        return None
    return {"reply": strike_line(int(struck["strike"])), "strike": struck["strike"]}


def reply_port(step: dict[str, Any], text: str | None, slots: dict[str, Any], graph: dict[str, Any] | None = None) -> str:
    role = _role(step)
    if text is None:
        return "reject" if step["kind"] == "approval" else "no"
    raw = text.strip()
    lowered = raw.lower()
    if step["kind"] in {"question", "question_popup"}:
        from kai.engine.objections import apply_objection_to_slots, is_objection, objection_port
        from kai.engine.prompts import build_prompt, match_choice

        prompt = build_prompt(step, slots, graph)
        matched = match_choice(raw, prompt)
        if matched:
            slots.pop("_objection_count", None)
            slots.pop("_objection_step", None)
            return matched
        ports = {str(port).lower(): str(port) for port in ((prompt or {}).get("ports") or [])}
        if lowered in ports:
            slots.pop("_objection_count", None)
            slots.pop("_objection_step", None)
            return ports[lowered]
        if is_objection(raw):
            leave = objection_port(prompt)
            step_id = str(step.get("id") or "")
            if leave and int(slots.get("_objection_count") or 0) >= 3 and str(slots.get("_objection_step") or "") == step_id:
                return leave
            if leave:
                struck = apply_objection_to_slots(slots, step_id)
                if struck["leave"]:
                    return leave
                return "continue"
        return "continue"
    if step["kind"] == "approval":
        if lowered in {"approve", "yes", "confirm"}:
            return "approve"
        return "reject"
    if step["type"] == "confirm" or role in {"confirm_request", "confirm_alert", "confirm_watch"}:
        return "yes" if lowered in {"yes", "y", "confirm", "approve"} else "no"
    if role == "offer_rule":
        return "no" if lowered in {"no", "n"} else "yes"
    if role == "offer_alternative":
        return "no" if lowered in {"no", "n", "neither", "decline"} else "continue"
    if step["type"] == "offer" or role == "offer_account":
        code = raw.upper()
        options = slots.get("options_list") or []
        known = {item["code"].upper() for item in options}
        if code in known or lowered in {"all", "yes", "y"}:
            return "continue"
        return "no"
    return "continue"


def reply_slots(
    step: dict[str, Any],
    text: str | None,
    slots: dict[str, Any] | None = None,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role = _role(step)
    slots = slots or {}
    if text and role == "offer_alternative" and text.strip().lower() == "all":
        return {"amount": slots.get("source_balance")}
    if text and (step["type"] == "offer" or role == "offer_account") and role != "offer_rule":
        picked = text.strip().upper()
        if picked in {"ALL", "YES", "Y"}:
            return {}
        return {"source": picked, "source_from": "asked"}
    if text and step["kind"] == "approval" and text.strip().lower() in {"approve", "yes", "confirm"}:
        return {"approved": True}
    if text and step["kind"] in {"question", "question_popup"}:
        port = reply_port(step, text, slots, graph)
        data = {"_last_reply": text, "_chosen_port": port}
        fields = [item for item in ((step.get("input") or {}).get("fields") or []) if isinstance(item, dict) and item.get("id")]
        if len(fields) == 1 and port == "continue":
            data[str(fields[0]["id"])] = text.strip()
        label = _option_label(step, port) or text.strip()
        if step.get("id") == "ask_residence" and port not in {"", "continue", "objection"}:
            data["residence"] = label
        if step.get("id") == "ask_funds" and port not in {"", "continue", "objection"}:
            data["source_of_funds"] = label
        return data
    return {}


def _option_label(step: dict[str, Any], port: str) -> str:
    raw = step.get("input") if isinstance(step.get("input"), dict) else {}
    for option in raw.get("options") or []:
        if not isinstance(option, dict):
            continue
        option_port = str(option.get("port") or "")
        label = str(option.get("label") or "")
        if option_port == port or label.lower() == port.lower():
            return label or option_port
    return ""


def _apply_observation(state: dict[str, Any], step: dict[str, Any], observation: dict[str, Any]) -> None:
    data = dict(observation.get("data") or {})
    spoken = data.pop("_line", None)
    state["slots"].update(data)
    if observation.get("event"):
        state["events"].append(observation["event"])
    if data.get("move_id"):
        state["move_id"] = data["move_id"]
    if step["kind"] == "fetch":
        _speak(state, step if step.get("line") or not spoken else {**step, "line": spoken})


def _speak(state: dict[str, Any], step: dict[str, Any]) -> None:
    if state.get("spoke"):
        return
    if step.get("kind") in {"question", "question_popup"}:
        from kai.engine.prompts import question_line

        line = question_line(step) or step.get("line")
    else:
        line = step.get("line")
    if not line:
        return
    text = render_line(line, state["slots"])
    state["messages"].append({"step_id": step["id"], "text": text, "exact": text})
    state["spoke"] = True


def _existing_port(graph: dict[str, Any], step_id: str, preferred: str) -> str:
    ports = [edge["port"] for edge in graph["edges"] if edge["from"] == step_id]
    if preferred in ports:
        return preferred
    if "continue" in ports:
        return "continue"
    if ports:
        return ports[0]
    return preferred


def _response_port(graph: dict[str, Any], step: dict[str, Any], slots: dict[str, Any]) -> str:
    edges = [edge["port"] for edge in graph["edges"] if edge["from"] == step["id"]]
    chosen = str(slots.get("_chosen_port") or "")
    if chosen and chosen in edges:
        return chosen
    steps = {item["id"]: item for item in graph["steps"]}
    question = None
    for edge in graph["edges"]:
        if edge["to"] != step["id"]:
            continue
        source = steps.get(edge["from"]) or {}
        if source.get("kind") in {"question", "question_popup"}:
            question = source
            break
    reply = " ".join(str(slots.get("_last_reply") or "").lower().split())
    for index, option in enumerate(((question or {}).get("input") or {}).get("options") or []):
        if isinstance(option, dict):
            label = str(option.get("label") or "")
            port = str(option.get("port") or "")
        else:
            label = str(option)
            port = ""
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if reply and reply in {label.lower(), port.lower(), slug}:
            if port in edges:
                return port
            if index < len(edges):
                return edges[index]
    return _existing_port(graph, step["id"], "continue")


def _requirement_target(graph: dict[str, Any], step: dict[str, Any], slots: dict[str, Any]) -> str | None:
    """The next question this answer requires. Story paths keep their own route."""
    if slots.get("_path"):
        return None
    chosen = str(slots.get("_chosen_port") or "")
    if chosen and _edge(graph, step["id"], chosen):
        return None
    present = {item["id"] for item in graph.get("steps") or []}
    if step["id"] == "signup_method_given":
        if chosen == "email_and_password" and "ask_email_password" in present:
            return "ask_email_password"
        if chosen == "continue_with_google" and "google_sign_in" in present:
            return "google_sign_in"
        if chosen and chosen not in {"continue", "email_and_password", "continue_with_google"} and "offer_passkey" in present:
            return "offer_passkey"
    if step["id"] == "google_sign_in" and chosen == "continue_with_google" and "offer_passkey" in present:
        return "offer_passkey"
    return None


def _outgoing(graph: dict[str, Any], step_id: str) -> list[dict[str, Any]]:
    return [edge for edge in graph.get("edges") or [] if edge.get("from") == step_id]


def _end_here(state: dict[str, Any], step: dict[str, Any]) -> bool:
    """A step with no arrow is the end of the chat."""
    state["status"] = "completed"
    state["pending"] = None
    state["result"] = step.get("result") or "done"
    state["step_id"] = step["id"]
    return False


def _balance_line(code: str, shown: Any, ledger: dict[str, Any]) -> str:
    text = display(shown)
    if code and text:
        return f"{code} is {text}."
    from kai.engine.slots import describe_accounts

    return describe_accounts(ledger or {"accounts": {}}, "list accounts")


def _jump(state: dict[str, Any], graph: dict[str, Any], step: dict[str, Any], port: str) -> bool:
    nxt = _edge(graph, step["id"], port)
    if nxt is None:
        if not _outgoing(graph, step["id"]):
            return _end_here(state, step)
        state["status"] = "blocked"
        state["pending"] = None
        state["result"] = "missing_edge"
        state["step_id"] = step["id"]
        return False
    state["slots"]["_arrived_by"] = port
    state["step_id"] = nxt
    return True


def _scripted_port(state: dict[str, Any], graph: dict[str, Any], step: dict[str, Any]) -> str | None:
    path = state["slots"].get("_path") or []
    if step["id"] not in path:
        return None
    index = path.index(step["id"])
    if index + 1 >= len(path):
        return None
    nxt = path[index + 1]
    for edge in graph["edges"]:
        if edge["from"] == step["id"] and edge["to"] == nxt:
            return edge["port"]
    return None


def _condition_port(step: dict[str, Any], slots: dict[str, Any]) -> str:
    role = _role(step)
    if role in {"source_enough", "covers"}:
        if role == "covers" and slots.get("balance_dropped"):
            return "false"
        amount = _saved(step, "amount")
        needed = num(slots.get("amount")) if slots.get("amount") not in (None, "") else num(amount)
        return "true" if num(slots.get("source_balance")) >= needed else "false"
    if role == "can_cover":
        picked = slots.get("picked_source") or ""
        cap = num(slots.get("cap", 500))
        if not picked or num(slots.get("amount")) > cap:
            return "false"
        return "true"
    if role == "any_sufficient":
        return "true" if slots.get("options_list") else "false"
    if role in {"needs_topup", "still_low"}:
        if role == "needs_topup" and slots.get("origin") == "chat":
            return "true"
        level = slots.get("threshold")
        if level in (None, ""):
            level = _saved(step, "level")
        threshold = num(50 if level is None else level)
        return "true" if num(slots.get("target_balance")) < threshold else "false"
    if role == "above":
        level = slots.get("threshold")
        if level in (None, ""):
            level = _saved(step, "level")
        threshold = num(50 if level is None else level)
        return "true" if num(slots.get("target_balance")) >= threshold else "false"
    if role == "below":
        level = slots.get("threshold")
        if level in (None, ""):
            level = _saved(step, "level")
        threshold = num(50 if level is None else level)
        return "true" if num(slots.get("target_balance")) < threshold else "false"
    if role == "compare":
        direction = str(_saved(step, "direction") or slots.get("direction") or "below")
        level = slots.get("threshold")
        if level in (None, ""):
            level = _saved(step, "level")
        threshold = num(50 if level is None else level)
        balance = num(slots.get("target_balance"))
        if direction == "above":
            return "true" if balance >= threshold else "false"
        return "true" if balance < threshold else "false"
    if role == "credentials":
        expected_email = str(_saved(step, "email") or "").strip().lower()
        expected_password = str(_saved(step, "password") or "")
        got_email = str(slots.get("email") or "").strip().lower()
        got_password = str(slots.get("password") or "")
        if expected_email and got_email == expected_email and got_password == expected_password:
            return "true"
        return "false"
    if role == "enough":
        amount = _saved(step, "amount")
        needed = num(slots.get("amount")) if slots.get("amount") not in (None, "") else num(amount)
        have = slots.get("source_balance")
        if have in (None, ""):
            have = slots.get("target_balance")
        return "true" if num(have) >= needed else "false"
    if role in {"first", "first_time"}:
        return "false" if slots.get("already_alerted") else "true"
    if role in {"reached", "reached_now"}:
        value = slots.get("rate_later") if role == "reached_now" and slots.get("rate_later") is not None else slots.get("rate_now")
        target = slots.get("target_rate", slots.get("target"))
        if (slots.get("direction") or "above") == "below":
            return "true" if num(value) <= num(target) else "false"
        return "true" if num(value) >= num(target) else "false"
    if role in {"from_chat", "origin"}:
        return "true" if slots.get("origin") == "chat" else "false"
    if role == "within_limits":
        failure = slots.get("limit_failure")
        if failure == "balance_moved" and slots.get("origin") == "chat":
            return "reoffer"
        if failure:
            return "false"
        if int(slots.get("runs_left", 3)) <= 0:
            return "false"
        if num(slots.get("amount")) > num(slots.get("cap", 10000)):
            return "false"
        if slots.get("would_loop"):
            return "false"
        return "true"
    return "false"


def _pick(step: dict[str, Any], slots: dict[str, Any]) -> str:
    candidates = list(slots.get("candidates") or [])
    needed = num(slots.get("amount"))
    if slots.get("origin") == "chat":
        chosen = str(slots.get("source") or "").upper()
        for row in candidates:
            if row["code"].upper() == chosen and float(row["spare"]) >= needed:
                slots["picked_source"] = row["code"]
                return "continue"
        return "false"
    for row in candidates:
        if float(row["spare"]) >= needed:
            slots["source"] = row["code"]
            slots["picked_source"] = row["code"]
            return "continue"
    return "false"


def _currency_of(code: Any, accounts: dict[str, Any]) -> str:
    account = accounts.get(str(code or "")) or {}
    return str(account.get("currency") or code or "")


def _money(account: dict[str, Any]) -> dict[str, Any]:
    amount = float(account["amount"])
    currency = account.get("currency") or ""
    return {"amount": amount, "currency": currency, "display": format_money(amount, currency)}


def format_money(amount: float, currency: str) -> str:
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency)
    number = f"{amount:,.2f}"
    if symbol:
        return f"{symbol}{number}"
    return f"{number} {currency}".strip()


def _amount_currency(amount: Any, fallback: str = "") -> str:
    if isinstance(amount, dict) and amount.get("currency"):
        return str(amount["currency"]).upper()
    return str(fallback or "").upper()


def _move_quote(slots: dict[str, Any], ledger: dict[str, Any], accounts: dict[str, Any]) -> dict[str, Any]:
    """Build send/est for a mock move. Amount currency decides which side is fixed."""
    raw_amount = slots.get("amount")
    amount = num(raw_amount)
    rate = float(ledger.get("rate") or 1.08)
    fee = float(ledger.get("fee") or 0)
    source = str(slots.get("source") or "")
    target = str(slots.get("target") or "")
    source_ccy = _currency_of(source, accounts).upper()
    target_ccy = _currency_of(target, accounts).upper()
    amount_ccy = _amount_currency(raw_amount, source_ccy or target_ccy)

    if not amount:
        send = _money({"amount": 0, "currency": source_ccy or amount_ccy or "USD"})
        est = _money({"amount": 0, "currency": target_ccy or amount_ccy or "USD"})
    elif source_ccy and target_ccy and source_ccy == target_ccy:
        # Internal same-asset move — 1:1.
        send = _money({"amount": amount, "currency": source_ccy})
        est = _money({"amount": amount, "currency": target_ccy})
    elif amount_ccy and target_ccy and amount_ccy == target_ccy and amount_ccy != source_ccy:
        # "Top up EUR with €65 from USDT" — arrival fixed; debit source at rate.
        est = _money({"amount": amount, "currency": target_ccy})
        send = _money({"amount": round(amount * rate + fee, 2), "currency": source_ccy or "USD"})
    else:
        # "Top up USD with €100 from EUR" — send fixed in source; convert to target.
        send = _money({"amount": amount, "currency": source_ccy or amount_ccy or "USD"})
        est = _money({"amount": round(amount * rate - fee, 2), "currency": target_ccy or "USD"})

    return {
        "rate": rate,
        "fee": fee,
        "send": send,
        "est": est,
        "quote_ttl": int(ledger.get("quote_ttl") or 30),
    }


def apply_move(ledger: dict[str, Any], slots: dict[str, Any]) -> dict[str, Any]:
    """Debit the source and credit what the quote said would arrive."""
    accounts = ledger.get("accounts") or {}
    source = str(slots.get("source") or "")
    target = str(slots.get("target") or "")
    taken = num(slots.get("send") if slots.get("send") not in (None, "") else slots.get("amount"))
    arrival = num(slots.get("est"))
    if arrival <= 0 and taken:
        source_ccy = _currency_of(source, accounts).upper()
        target_ccy = _currency_of(target, accounts).upper()
        amount_ccy = _amount_currency(slots.get("amount"), source_ccy)
        rate = float(ledger.get("rate") or 1)
        if source_ccy and target_ccy and source_ccy == target_ccy:
            arrival = taken
        elif amount_ccy and target_ccy and amount_ccy == target_ccy and amount_ccy != source_ccy:
            # Amount was target-fixed; taken is source debit — arrival is the EUR/USD/… amount.
            arrival = num(slots.get("amount"))
            if arrival <= 0 and rate:
                arrival = round(taken / rate, 2)
        else:
            arrival = round(taken * rate, 2)
    if source in accounts and taken:
        row = accounts[source]
        row["amount"] = round(float(row.get("amount") or 0) - taken, 2)
        row.pop("display", None)
    if target in accounts and arrival and target != source:
        row = accounts[target]
        row["amount"] = round(float(row.get("amount") or 0) + arrival, 2)
        row.pop("display", None)
    return ledger


def _other_accounts(slots: dict[str, Any], accounts: dict[str, Any]) -> dict[str, Any]:
    needed = num(slots.get("amount"))
    source = slots.get("source")
    target = slots.get("target")
    viable = []
    largest = None
    for code, account in accounts.items():
        if account.get("kind") == "card":
            continue
        spare = float(account["amount"]) - float(account.get("floor") or 0)
        shown = format_money(float(account["amount"]), account.get("currency") or code)
        if largest is None or float(account["amount"]) > largest[0]:
            largest = (float(account["amount"]), shown)
        if code in {source, target}:
            continue
        if spare >= needed:
            viable.append({"code": code, "display": f"{code} ({shown})", "spare": spare})
    viable.sort(key=lambda item: item.get("spare") or 0, reverse=True)
    options = _join_options(viable) if viable else ""
    return {
        "options_list": viable,
        "options": options,
        "largest": largest[1] if largest else "",
    }


def _join_options(viable: list[dict[str, str]]) -> str:
    labels = [item["display"] for item in viable]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"
