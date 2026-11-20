"""Conversation state graph. Edges come from the waiting step, not a word list."""

from __future__ import annotations

import re
from typing import Any, Callable, TypedDict

from kai.engine.objections import is_objection, next_objection_strike, objection_port
from kai.engine.prompts import match_choice
from kai.engine.slots import asks_capacity, asks_for_accounts, greeting_reply, looks_like_question
from kai.engine.tools import select_tool


class TurnState(TypedDict, total=False):
    text: str
    phase: str
    prompt: dict
    decision: dict


def run_conversation(payload: dict[str, Any], understand: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Walk one customer message. `understand` is the model-or-fallback turn."""

    try:
        from langgraph.graph import END, StateGraph
    except Exception:
        return _without_graph(payload, understand)

    prompt = payload.get("prompt") or {}
    ports = [str(choice.get("port")) for choice in (prompt.get("choices") or []) if choice.get("port")]

    def route(state: TurnState) -> str:
        waiting = state.get("prompt") or {}
        if waiting:
            text = state.get("text") or ""
            port = match_choice(text, waiting)
            if port and port in ports:
                return port
            if _is_skip(text):
                return "skip" if waiting.get("skip") else "aside"
            if is_objection(text) and objection_port(waiting):
                return "object"
            if greeting_reply(text) or select_tool(text):
                return "aside"
            if waiting.get("fields") and not looks_like_question(text) and not is_objection(text):
                return "fields"
            return "aside"
        if payload.get("pinned"):
            return "understand"
        if state.get("phase") in {"idle", "", None} and greeting_reply(state.get("text") or ""):
            return "greet"
        return "understand"

    def choice(state: TurnState) -> dict[str, Any]:
        port = match_choice(state.get("text") or "", state.get("prompt") or {}) or ""
        return {
            "decision": _with_route(
                {
                    "action": "choice",
                    "port": port,
                    "workflow_key": "",
                    "confidence": 1,
                    "slots": {},
                    "needs": "none",
                    "reply": "",
                },
                "choice",
            )
        }

    def fields_node(state: TurnState) -> dict[str, Any]:
        return {
            "decision": _with_route(
                {
                    "action": "choice",
                    "port": "continue",
                    "text": state.get("text") or "",
                    "workflow_key": "",
                    "confidence": 1,
                    "slots": {},
                    "needs": "none",
                    "reply": "",
                },
                "fields",
            )
        }

    def object_node(state: TurnState) -> dict[str, Any]:
        struck = next_objection_strike(
            state.get("prompt") or {},
            objection_count=int(payload.get("objection_count") or 0),
            objection_step=str(payload.get("objection_step") or ""),
        )
        if not struck:
            return {"decision": _aside_decision(payload, state.get("prompt") or {}, understand)}
        decision = {
            "action": struck["action"],
            "port": struck.get("port") or "",
            "workflow_key": "",
            "confidence": 1,
            "slots": struck.get("slots") or {},
            "needs": "none",
            "reply": struck.get("reply") or "",
            "strike": struck.get("strike") or 0,
        }
        return {"decision": _with_route(decision, "object")}

    def aside(state: TurnState) -> dict[str, Any]:
        return {"decision": _with_route(_aside_decision(payload, state.get("prompt") or {}, understand), "aside")}

    def skip_node(state: TurnState) -> dict[str, Any]:
        return {
            "decision": _with_route(
                {
                    "action": "choice",
                    "port": "continue",
                    "text": "",
                    "workflow_key": "",
                    "confidence": 1,
                    "slots": {},
                    "needs": "none",
                    "reply": "",
                },
                "skip",
            )
        }

    def greet(state: TurnState) -> dict[str, Any]:
        line = greeting_reply(state.get("text") or "") or ""
        return {
            "decision": _with_route(
                {"action": "chat", "port": "", "workflow_key": "", "confidence": 0, "slots": {}, "needs": "none", "reply": line},
                "greet",
            )
        }

    def understand_node(state: TurnState) -> dict[str, Any]:
        decision = understand(payload)
        decision.setdefault("port", "")
        return {"decision": _with_route(decision, "understand")}

    graph = StateGraph(TurnState)
    graph.add_node("start", lambda state: {})
    graph.add_node("choice", choice)
    graph.add_node("fields", fields_node)
    graph.add_node("object", object_node)
    graph.add_node("aside", aside)
    graph.add_node("skip", skip_node)
    graph.add_node("greet", greet)
    graph.add_node("understand", understand_node)
    graph.set_entry_point("start")
    targets = {port: "choice" for port in ports}
    targets["fields"] = "fields"
    targets["object"] = "object"
    targets["aside"] = "aside"
    targets["skip"] = "skip"
    targets["greet"] = "greet"
    targets["understand"] = "understand"
    graph.add_conditional_edges("start", route, targets)
    for node in ("choice", "fields", "object", "aside", "skip", "greet", "understand"):
        graph.add_edge(node, END)
    from kai.config import apply_langsmith_env, langsmith_enabled

    apply_langsmith_env()
    invoke_config: dict[str, Any] = {"run_name": "kai_conversation_turn"}
    if langsmith_enabled():
        invoke_config["tags"] = ["kai", "conversation"]
    result = graph.compile().invoke(
        {
            "text": payload.get("text") or "",
            "phase": payload.get("phase") or "idle",
            "prompt": prompt,
        },
        config=invoke_config,
    )
    return result.get("decision") or understand(payload)


def _is_skip(text: str) -> bool:
    return " ".join((text or "").lower().split()) in {"skip", "skip this", "skip it"}


def _with_route(decision: dict[str, Any], route: str) -> dict[str, Any]:
    from kai.engine.audit import step, turn_trail

    decision = dict(decision)
    decision["route"] = route
    if decision.get("ai_steps"):
        steps = list(decision.get("ai_steps") or [])
        detail = route
        if decision.get("strike"):
            detail = f"{route} · strike {decision['strike']} of 3"
        if decision.get("action") == "answer" and route in {"aside", "object"} and not decision.get("port"):
            detail = f"{detail} · reask" if "reask" not in detail else detail
        steps = [row for row in steps if row.get("id") not in {"route", "decision"}]
        steps.append(step("route", "Route", detail, route=route, strike=decision.get("strike") or 0))
        steps.append(
            step(
                "decision",
                "Decision",
                f"{decision.get('action')} {decision.get('port') or decision.get('workflow_key') or ''}".strip(),
                action=decision.get("action"),
                port=decision.get("port"),
                workflow_key=decision.get("workflow_key"),
            )
        )
        decision["ai_steps"] = steps
        return decision
    decision["ai_steps"] = turn_trail(
        chosen=decision,
        route=route,
        strike=decision.get("strike"),
    )
    return decision


def _without_graph(payload: dict[str, Any], understand: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    prompt = payload.get("prompt") or {}
    port = match_choice(payload.get("text") or "", prompt)
    ports = {str(choice.get("port")) for choice in (prompt.get("choices") or [])}
    text = payload.get("text") or ""
    if port and port in ports:
        return _with_route(
            {"action": "choice", "port": port, "workflow_key": "", "confidence": 1, "slots": {}, "needs": "none", "reply": ""},
            "choice",
        )
    if _is_skip(text):
        if prompt.get("skip"):
            return _with_route(
                {"action": "choice", "port": "continue", "text": "", "workflow_key": "", "confidence": 1, "slots": {}, "needs": "none", "reply": ""},
                "skip",
            )
        return _with_route(_aside_decision(payload, prompt, understand), "aside")
    if is_objection(text) and objection_port(prompt):
        struck = next_objection_strike(
            prompt,
            objection_count=int(payload.get("objection_count") or 0),
            objection_step=str(payload.get("objection_step") or ""),
        )
        if struck:
            return _with_route(
                {
                    "action": struck["action"],
                    "port": struck.get("port") or "",
                    "workflow_key": "",
                    "confidence": 1,
                    "slots": struck.get("slots") or {},
                    "needs": "none",
                    "reply": struck.get("reply") or "",
                    "strike": struck.get("strike") or 0,
                },
                "object",
            )
    if (greeting_reply(text) or select_tool(text)) and prompt:
        return _with_route(_aside_decision(payload, prompt, understand), "aside")
    if prompt.get("fields") and not looks_like_question(text) and not is_objection(text):
        return _with_route(
            {
                "action": "choice",
                "port": "continue",
                "text": payload.get("text") or "",
                "workflow_key": "",
                "confidence": 1,
                "slots": {},
                "needs": "none",
                "reply": "",
            },
            "fields",
        )
    if prompt:
        return _with_route(_aside_decision(payload, prompt, understand), "aside")
    return _with_route(understand(payload), "understand")


def _looks_like_workflow_switch(text: str) -> bool:
    """True when an open-prompt aside might be starting a different workflow."""
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return False
    if select_tool(text):
        return True
    return bool(
        re.search(
            r"\b(onboarding|open an account|new account|create an account|sign\s*up|sign\s*in|"
            r"top\s*up|topup|transfer|sweep|move money|balance alert|fx rate|demo \w+|"
            r"check balance|fetch balance|my balance|low balance)\b",
            lowered,
        )
    )


def _aside_decision(
    payload: dict[str, Any],
    prompt: dict[str, Any],
    understand: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    # Default: re-ask instantly. Calling Gemini on every mistype made choice steps feel stuck.
    text = payload.get("text") or ""
    if _looks_like_workflow_switch(text):
        decision = understand(payload)
        if (
            decision.get("action") == "start"
            and decision.get("workflow_key")
            and decision.get("workflow_key") != (payload.get("workflow_key") or "")
            and float(decision.get("confidence") or 0) >= 0.75
        ):
            decision.setdefault("port", "")
            return decision
    # Mid-flow capacity / account questions get a real answer; mistypes just re-ask the prompt.
    if asks_for_accounts(text) or asks_capacity(text):
        decision = understand(payload)
        if decision.get("action") in {"answer", "chat"}:
            decision = dict(decision)
            decision.setdefault("port", "")
            prompt_line = str(prompt.get("text") or "").strip()
            if prompt_line and prompt_line not in str(decision.get("reply") or ""):
                decision["reask"] = prompt_line
            return decision
    return {
        "action": "answer",
        "port": "",
        "workflow_key": "",
        "confidence": 1,
        "slots": {},
        "needs": "none",
        "reply": prompt.get("text") or "",
    }
