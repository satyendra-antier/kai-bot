"""Temporal activities. All Gemini and database access stays here."""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import copy

import httpx
from temporalio import activity

from kai.db import session_scope
from kai.engine.advance import apply_move, render_line, resolve_action, resolve_fetch
from kai.engine.audit import run_event, turn_trail
from kai.engine.scoring import score_run
from kai.engine.slots import HELP, accept_model, extract_slots, fallback_turn, greeting_reply, idle_line, pinned_turn, score_intent, sync_try_prompts
from kai.engine.tools import _OPT_IN, _OPT_OUT, apply_guard, redact_pii, select_tool
from kai.intelligence import ended_label, next_run_code
from kai.gemini import classify as gemini_classify
from kai.gemini import enabled as gemini_enabled
from kai.gemini import extract as gemini_extract
from kai.gemini import interpret as gemini_interpret
from kai.gemini import judge_line, normal_chat
from kai.models import Connector, Message, Run, RunEvent, RunScore, Workflow, WorkflowVersion


def _publish(conversation_id: str | None, payload: dict) -> None:
    if not conversation_id:
        return
    try:
        import redis

        from kai.config import REDIS_URL

        client = redis.Redis.from_url(REDIS_URL)
        client.publish(f"conv:{conversation_id}", __import__("json").dumps(payload))
    except Exception:
        return


def _contract_row(row, graph: dict, contract: dict, eval_config: dict, version_id: str) -> dict:
    contract = sync_try_prompts(dict(contract or {}))
    return {
        "workflow_id": row.id,
        "workflow_key": row.key,
        "version_id": version_id,
        "graph": graph or {},
        "contract": contract,
        "eval_config": eval_config or {},
        "ledger": contract.get("preview_ledger") or {"accounts": {}},
    }


def _chat_ready(graph: dict, channels: list | None, pinned_here: bool) -> bool:
    if pinned_here:
        return True
    if "chat" not in (channels or []):
        return False
    return bool(((graph or {}).get("entries") or {}).get("chat"))


@activity.defn(name="load_contracts")
def load_contracts(payload: dict) -> dict:
    tenant_id = payload["tenant_id"]
    pinned = payload.get("workflow_id") or ""
    with session_scope() as session:
        rows = session.query(Workflow).filter(Workflow.tenant_id == tenant_id).all()
        contracts = []
        for row in rows:
            draft = row.draft or {}
            pinned_here = bool(pinned and pinned in {row.id, row.key})
            if pinned_here or draft.get("graph"):
                if not pinned_here and not row.published_version_id:
                    continue
                graph = draft.get("graph") or {}
                if not _chat_ready(graph, row.channels, pinned_here):
                    continue
                contracts.append(_contract_row(row, graph, draft.get("chat_contract") or {}, draft.get("eval_config") or {}, "draft"))
                continue
            if not row.published_version_id:
                continue
            version = session.get(WorkflowVersion, row.published_version_id)
            if version is None:
                continue
            if not _chat_ready(version.graph or {}, row.channels, pinned_here):
                continue
            contracts.append(_contract_row(row, version.graph, version.chat_contract, version.eval_config, version.id))
    return {"contracts": contracts}


@activity.defn(name="classify_intent")
def classify_intent(payload: dict) -> dict:
    text = payload["text"]
    contracts = payload["contracts"]
    best = None
    best_score = -1.0
    for item in contracts:
        score = score_intent(text, item["contract"], workflow_key=item.get("workflow_key") or "")
        product = 0 if str(item.get("workflow_key") or "").startswith("demo-") else 1
        if score > best_score or (score == best_score and product and best and str(best.get("workflow_key") or "").startswith("demo-")):
            best_score = score
            best = item
    if gemini_enabled():
        parsed = gemini_classify(text, contracts)
        if parsed and parsed.get("workflow_key"):
            match = next((item for item in contracts if item["workflow_key"] == parsed["workflow_key"]), None)
            confidence = float((parsed or {}).get("confidence") or 0)
            if match and confidence >= max(best_score, 0.5):
                return {
                    "workflow_key": match["workflow_key"],
                    "workflow_id": match["workflow_id"],
                    "version_id": match["version_id"],
                    "confidence": confidence,
                    "reason": parsed.get("reason") or "",
                    "source": "gemini",
                }
    if best is None or best_score <= 0:
        return {
            "workflow_key": "",
            "workflow_id": "",
            "version_id": "",
            "confidence": 0,
            "reason": "no chat workflow matched",
            "source": "deterministic",
        }
    return {
        "workflow_key": best["workflow_key"],
        "workflow_id": best["workflow_id"],
        "version_id": best["version_id"],
        "confidence": best_score,
        "reason": "matched the chat description",
        "source": "deterministic",
    }


@activity.defn(name="extract_slots")
def extract_slots_activity(payload: dict) -> dict:
    text = payload["text"]
    found = extract_slots(text)
    if found:
        return {"slots": found, "source": "deterministic"}
    if gemini_enabled():
        parsed = gemini_extract(text, payload.get("slot_specs") or [])
        slots = {}
        for key, value in (parsed or {}).get("slots", {}).items():
            slots[key] = {"value": value.get("value") if isinstance(value, dict) else value, "from": "the message"}
        if slots:
            return {"slots": slots, "source": "gemini"}
    return {"slots": {}, "source": "deterministic"}


def _turn_catalog(contracts: list[dict]) -> list[dict]:
    catalog = []
    for item in contracts:
        contract = item.get("contract") or {}
        accounts = []
        for code, row in ((item.get("ledger") or {}).get("accounts") or {}).items():
            accounts.append(
                {
                    "code": code,
                    "kind": (row or {}).get("kind") or "account",
                    "display": (row or {}).get("display")
                    or f"{(row or {}).get('amount')} {(row or {}).get('currency') or code}",
                }
            )
        catalog.append(
            {
                "workflow_key": item.get("workflow_key"),
                "description": contract.get("description") or "",
                "examples": (contract.get("examples") or [])[:5],
                "exclusions": contract.get("exclusions") or [],
                "slots": [
                    {"key": slot.get("key"), "fill_from": slot.get("fill_from")}
                    for slot in (contract.get("slots") or [])
                    if isinstance(slot, dict)
                ],
                "accounts": accounts,
                "never": [rule.get("text") for rule in (contract.get("never") or []) if isinstance(rule, dict)],
            }
        )
    return catalog


def _turn_state(payload: dict, contracts: list[dict]) -> dict:
    filled = payload.get("filled") or {}
    shown = {}
    for key, value in filled.items():
        if isinstance(value, dict):
            shown[key] = value.get("display") or value.get("amount")
        else:
            shown[key] = value
    pinned_id = payload.get("pinned_workflow_id") or ""
    opened = next(
        (
            item.get("workflow_key")
            for item in contracts
            if pinned_id and pinned_id in {item.get("workflow_id"), item.get("workflow_key")}
        ),
        "",
    )
    state = {
        "phase": payload.get("phase") or "idle",
        "pending_slot": payload.get("pending_slot") or "",
        "workflow_key": payload.get("workflow_key") or opened or "",
        "opened_workflow_key": opened or "",
        "filled": shown,
    }
    prompt = payload.get("prompt") or {}
    if prompt:
        state["waiting"] = {
            "step_id": prompt.get("step_id") or "",
            "text": prompt.get("text") or "",
            "choices": [choice.get("label") for choice in (prompt.get("choices") or []) if choice.get("label")],
            "ports": list(prompt.get("ports") or []),
        }
    return state


def resolve_turn(payload: dict) -> dict:
    text = payload.get("text") or ""
    contracts = payload.get("contracts") or []
    state = _turn_state(payload, contracts)
    pinned = pinned_turn(text, contracts, state)
    if pinned:
        pinned = dict(pinned)
        pinned["source"] = "pinned"
        pinned["ai_steps"] = turn_trail(
            pii=[],
            tool=select_tool(text) or "",
            chosen=pinned,
            route="pinned",
        )
        return pinned
    local = fallback_turn(text, contracts, state)
    shown, kinds = redact_pii(text)
    local["model_input"] = shown
    local["pii"] = kinds
    local["tool"] = select_tool(text) or ""
    local["source"] = "deterministic"
    if gemini_enabled():
        raw = gemini_interpret(shown, _turn_catalog(contracts), state)
        accepted = accept_model(raw, text, contracts, state)
        if accepted:
            accepted = dict(accepted)
            accepted["source"] = "gemini"
            if accepted["action"] in {"chat", "answer"} and accepted["needs"] == "none" and not accepted.get("reply"):
                accepted["reply"] = local.get("reply") or greeting_reply(text) or idle_line(contracts)
            strong_local = local["action"] in {"start", "fill"} and local["confidence"] >= 0.75
            pending = state.get("pending_slot") or ""
            if strong_local and pending == "amount" and "amount" in (local.get("slots") or {}):
                local["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=local, route="local_amount")
                return local
            if strong_local and pending and pending in (local.get("slots") or {}) and pending not in (accepted.get("slots") or {}):
                local["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=local, route="local_slot")
                return local
            from kai.engine.slots import asks_capacity

            # Mid-slot "how much can I move?" — prefer a capacity answer over a full account dump.
            if local.get("action") == "answer" and local.get("needs") == "capacity" and asks_capacity(text):
                local["model_input"] = shown
                local["pii"] = kinds
                local["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=local, route="local_capacity")
                return local
            if accepted["needs"] != "none":
                if asks_capacity(text) and accepted.get("needs") == "accounts":
                    accepted["needs"] = "capacity"
                accepted["model_input"] = shown
                accepted["pii"] = kinds
                accepted["tool"] = local["tool"]
                accepted["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=accepted, route="model_needs")
                return accepted
            local_key = str(local.get("workflow_key") or "")
            demo_local = local_key.startswith("demo-") and "demo" not in (text or "").lower()
            if (
                strong_local
                and accepted["action"] in {"chat", "answer"}
                and not accepted.get("slots")
                and not demo_local
            ):
                local["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=local, route="local_beats_chat")
                return local
            if (
                accepted["action"] in {"start", "fill"}
                and local["confidence"] > accepted["confidence"]
                and not (demo_local and not str(accepted.get("workflow_key") or "").startswith("demo-"))
            ):
                accepted["confidence"] = local["confidence"]
                accepted["workflow_key"] = accepted["workflow_key"] or local["workflow_key"]
            # Prefer a product start from Gemini over a demo start from the local scorer.
            if (
                demo_local
                and accepted["action"] == "start"
                and accepted.get("workflow_key")
                and not str(accepted.get("workflow_key") or "").startswith("demo-")
            ):
                accepted["model_input"] = shown
                accepted["pii"] = kinds
                accepted["tool"] = local["tool"]
                accepted["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=accepted, route="model_over_demo")
                return accepted
            accepted["model_input"] = shown
            accepted["pii"] = kinds
            accepted["tool"] = local["tool"]
            accepted["ai_steps"] = turn_trail(pii=kinds, tool=local["tool"], local=local, model=accepted, model_used=True, chosen=accepted, route="model")
            return accepted
        local["ai_steps"] = turn_trail(
            pii=kinds,
            tool=local["tool"],
            local=local,
            model_used=False,
            model_skipped="Gemini returned nothing usable",
            chosen=local,
            route="local_fallback",
        )
        if local["action"] in {"chat", "answer"} and local["needs"] == "none" and not local.get("reply"):
            local["reply"] = greeting_reply(text) or idle_line(contracts)
        return local
    if local["action"] in {"chat", "answer"} and local["needs"] == "none" and not local.get("reply"):
        local["reply"] = greeting_reply(text) or idle_line(contracts)
    local["ai_steps"] = turn_trail(
        pii=kinds,
        tool=local["tool"],
        local=local,
        model_used=False,
        model_skipped="Gemini is off",
        chosen=local,
        route="local",
    )
    return local


def _tenant_for(conversation_id: str) -> str:
    if not conversation_id:
        return ""
    with session_scope() as session:
        row = (
            session.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .first()
        )
        return str(row.tenant_id) if row is not None else ""


def _profile_for_turn(payload: dict, contracts: list[dict]) -> dict:
    profile = _chat_profile(
        payload.get("conversation_id") or "",
        persona=str(payload.get("persona") or "guest"),
        customer_id=str(payload.get("customer_id") or ""),
    )
    for item in contracts:
        rate = (item.get("ledger") or {}).get("rate")
        if rate not in (None, ""):
            profile["rate"] = rate
            break
    return profile


def _early_tool_guard(text: str, profile: dict, contracts: list[dict]) -> dict | None:
    """Resolve read-tools / guest money denials before Gemini — stamps a Tool step on the run."""
    from kai.engine.slots import wants_alert
    from kai.engine.tools import ACCOUNT_TOOLS, _money

    tool = select_tool(text)
    decision: dict[str, Any] = {"action": "chat", "workflow_key": "", "confidence": 0, "slots": {}, "needs": "none", "reply": ""}
    if tool in ACCOUNT_TOOLS | {"profile", "opt_out", "opt_in", "price"}:
        pass
    elif not profile.get("account") and (
        wants_alert(text)
        or re.search(
            r"\b(top\s*up|topup|move|transfer|sweep|fx rate|exchange|send money|watch.*(rate|account|balance))\b",
            text or "",
            re.I,
        )
    ):
        money_key = next((item.get("workflow_key") for item in contracts if _money({"workflow_key": item.get("workflow_key")}, contracts)), "")
        if money_key:
            decision = {"action": "start", "workflow_key": money_key, "confidence": 0.9, "slots": {}, "needs": "none", "reply": ""}
        else:
            return None
    else:
        return None
    return apply_guard(
        text,
        decision,
        profile,
        contracts,
        kill_switch=os.environ.get("KAI_KILL_SWITCH") == "1",
    )


def _interpret_turn_body(payload: dict) -> dict:
    from kai.conversation_graph import run_conversation

    contracts = list(payload.get("contracts") or [])
    prompt = payload.get("prompt") or {}
    if not contracts and prompt:
        tenant_id = payload.get("tenant_id") or _tenant_for(payload.get("conversation_id") or "")
        if tenant_id:
            contracts = load_contracts(
                {"tenant_id": tenant_id, "workflow_id": payload.get("pinned_workflow_id") or ""}
            ).get("contracts") or []
    turn_payload = {**payload, "contracts": contracts}
    state = _turn_state(turn_payload, contracts)
    phase = state.get("phase") or "idle"
    pinned = bool(state.get("opened_workflow_key")) and phase in {"idle", "", None} and not state.get("pending_slot")
    shown, kinds = redact_pii(payload.get("text") or "")
    open_prompt = bool(prompt)
    if not pinned and not open_prompt and phase in {"idle", "", None} and not state.get("pending_slot"):
        profile = _profile_for_turn(payload, contracts)
        early = _early_tool_guard(payload.get("text") or "", profile, contracts)
        if early:
            early["ai_steps"] = turn_trail(
                pii=kinds,
                tool=select_tool(payload.get("text") or "") or "",
                chosen=early,
                route="guard",
                guard=early,
            )
            early["route"] = "guard"
            early["source"] = "guard"
            early["model_input"] = shown
            early["pii"] = kinds
            early["tool"] = select_tool(payload.get("text") or "") or ""
            return _stamp_turn(payload.get("conversation_id") or "", payload.get("text") or "", early)
    decision = run_conversation(
        {**turn_payload, "pinned": pinned, "workflow_key": state.get("workflow_key") or ""},
        resolve_turn,
    )
    steps = list(decision.get("ai_steps") or [])
    if not steps:
        steps = turn_trail(
            pii=kinds,
            tool=decision.get("tool") or select_tool(payload.get("text") or "") or "",
            chosen=decision,
            route=decision.get("route") or phase or "idle",
            strike=decision.get("strike"),
        )
    replaced = False
    if not pinned and ((phase in {"idle", "", None} and not state.get("pending_slot")) or open_prompt):
        profile = _profile_for_turn(payload, contracts)
        before = dict(decision)
        guarded = apply_guard(
            payload.get("text") or "",
            decision,
            profile,
            contracts,
            kill_switch=os.environ.get("KAI_KILL_SWITCH") == "1",
        )
        if guarded:
            steps = turn_trail(
                pii=kinds,
                tool=before.get("tool") or "",
                local=before if before.get("source") == "deterministic" else None,
                model=before if before.get("source") == "gemini" else None,
                model_used=before.get("source") == "gemini",
                chosen=guarded,
                route=before.get("route") or "guard",
                guard=guarded,
                strike=before.get("strike"),
            )
            guarded["ai_steps"] = steps
            guarded["route"] = before.get("route") or decision.get("route") or ""
            guarded["strike"] = before.get("strike")
            guarded["source"] = before.get("source") or decision.get("source") or "guard"
            guarded["model_input"] = before.get("model_input") or shown
            guarded["pii"] = kinds
            guarded["tool"] = before.get("tool") or select_tool(payload.get("text") or "") or ""
            decision = guarded
            replaced = True
    if not replaced:
        decision["ai_steps"] = steps
    if not replaced and decision.get("needs") in {"accounts", "cards"}:
        decision["ledger"] = _booked_ledger(payload.get("conversation_id") or "", contracts)
    if not decision.get("workflow_key") and decision.get("action") != "start":
        decision["workflow_key"] = state.get("workflow_key") or ""
    if not decision.get("step_id"):
        decision["step_id"] = prompt.get("step_id") or ""
    decision["model_input"] = decision.get("model_input") or shown
    decision["pii"] = kinds
    # Always expose the selected tool on the decision for Intelligence / LangSmith.
    decision["tool"] = decision.get("tool") or select_tool(payload.get("text") or "") or ""
    if decision.get("tool") and decision.get("ai_steps"):
        ids = {row.get("id") for row in decision["ai_steps"]}
        if "tool" not in ids:
            from kai.engine.audit import step as audit_step

            trail = list(decision["ai_steps"])
            trail.insert(1, audit_step("tool", "Tool", decision["tool"], tool=decision["tool"]))
            decision["ai_steps"] = trail
    return _stamp_turn(payload.get("conversation_id") or "", payload.get("text") or "", decision)


@activity.defn(name="interpret_turn")
def interpret_turn(payload: dict) -> dict:
    from kai.config import apply_langsmith_env, langsmith_enabled

    apply_langsmith_env()
    if langsmith_enabled():
        try:
            from langsmith import traceable

            return traceable(
                name="kai.interpret_turn",
                run_type="chain",
                metadata={
                    "conversation_id": payload.get("conversation_id") or "",
                    "persona": payload.get("persona") or "guest",
                    "tool": select_tool(payload.get("text") or "") or "",
                },
                tags=["kai", "interpret"],
            )(_interpret_turn_body)(payload)
        except Exception:
            pass
    return _interpret_turn_body(payload)


def _stamp_turn(conversation_id: str, text: str, decision: dict) -> dict:
    shown, kinds = redact_pii(text)
    decision["model_input"] = decision.get("model_input") or shown
    decision["pii"] = decision.get("pii") if decision.get("pii") is not None else kinds
    decision["tool"] = decision.get("tool") or select_tool(text) or ""
    if not conversation_id:
        return decision
    slots = decision.get("slots") or {}
    slot_summary = {}
    if isinstance(slots, dict):
        for key, value in slots.items():
            if isinstance(value, dict) and "value" in value:
                slot_summary[key] = value.get("value")
            else:
                slot_summary[key] = value
    with session_scope() as session:
        row = (
            session.query(Message)
            .filter(Message.conversation_id == conversation_id, Message.role == "user", Message.text == text)
            .order_by(Message.created_at.desc())
            .first()
        )
        if row is not None:
            row.payload = {
                "tool": decision.get("tool") or "",
                "action": decision.get("action") or "",
                "workflow_key": decision.get("workflow_key") or "",
                "model_input": decision.get("model_input") or "",
                "pii": list(decision.get("pii") or []),
                "step_id": decision.get("step_id") or "",
                "route": decision.get("route") or "",
                "strike": decision.get("strike") or 0,
                "source": decision.get("source") or "",
                "confidence": decision.get("confidence"),
                "needs": decision.get("needs") or "none",
                "port": decision.get("port") or "",
                "slots": slot_summary,
                "ai_steps": list(decision.get("ai_steps") or []),
            }
            session.add(row)
    return decision


def _chat_profile(conversation_id: str, persona: str = "guest", customer_id: str = "") -> dict[str, Any]:
    """Guest chats earn an account via onboarding. Logged-in demos start with the preview ledger + completed moves."""
    from kai.engine.topup import PREVIEW_LEDGER

    mode = (persona or "guest").strip().lower()
    if mode in {"logged_in", "user", "authenticated"} or customer_id:
        ledger = _booked_ledger(conversation_id, [{"ledger": PREVIEW_LEDGER}])
        return {
            "account": True,
            "kind": "individual",
            "opted_out": False,
            "ledger": ledger,
            "rate": ledger.get("rate") if ledger.get("rate") is not None else PREVIEW_LEDGER.get("rate"),
            "customer_id": customer_id or "cust-thomas",
            "persona": "logged_in",
        }
    profile: dict[str, Any] = {"account": False, "kind": "", "opted_out": False, "ledger": {"accounts": {}}, "rate": None, "persona": "guest"}
    if not conversation_id:
        return profile
    with session_scope() as session:
        messages = session.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).all()
        opted = False
        for row in messages:
            if row.role != "user":
                continue
            if _OPT_IN.search(row.text or ""):
                opted = False
            elif _OPT_OUT.search(row.text or ""):
                opted = True
        opened = (
            session.query(Run)
            .filter(Run.conversation_id == conversation_id, Run.result == "account_opened")
            .order_by(Run.started_at.asc())
            .all()
        )
        moves = (
            session.query(Run)
            .filter(Run.conversation_id == conversation_id, Run.result.in_(("moved", "done")))
            .order_by(Run.started_at.asc())
            .all()
        )
        profile["opted_out"] = opted
        open_rows = [(row.started_at, row.workflow_id or "", dict(row.ledger or {})) for row in opened]
        move_rows = [(row.started_at, dict(row.slots or {})) for row in moves]
    if not open_rows:
        return profile
    profile["account"] = True
    profile["kind"] = "business" if "business" in open_rows[-1][1] else "individual"
    opened_at = open_rows[-1][0]
    ledger = copy.deepcopy(open_rows[-1][2] or {"accounts": {}})
    ledger.setdefault("accounts", {})
    for started, slots in move_rows:
        if opened_at is not None and started is not None and started < opened_at:
            continue
        apply_move(ledger, slots)
    profile["ledger"] = ledger
    return profile


def _booked_ledger(conversation_id: str, contracts: list[dict]) -> dict:
    """Preview balances, then every completed move in this chat."""
    from kai.engine.topup import PREVIEW_LEDGER

    base: dict[str, Any] = copy.deepcopy(PREVIEW_LEDGER)
    for item in contracts:
        ledger = item.get("ledger") or {}
        if ledger.get("accounts"):
            base = copy.deepcopy(ledger)
            # Keep mock FX knobs if a contract ledger omitted them.
            base.setdefault("rate", PREVIEW_LEDGER.get("rate"))
            base.setdefault("fee", PREVIEW_LEDGER.get("fee"))
            base.setdefault("quote_ttl", PREVIEW_LEDGER.get("quote_ttl"))
            break
    booked = base
    if not conversation_id:
        return booked
    with session_scope() as session:
        rows = (
            session.query(Run)
            .filter(Run.conversation_id == conversation_id, Run.result.in_(("moved", "done")))
            .order_by(Run.started_at.asc())
            .all()
        )
        moves = [dict(row.slots or {}) for row in rows]
    for slots in moves:
        apply_move(booked, slots)
    return booked


@activity.defn(name="booked_ledger")
def booked_ledger_activity(payload: dict) -> dict:
    """Ledger for a chat: contract/preview balances with completed moves applied."""
    contracts = list(payload.get("contracts") or [])
    if not contracts and payload.get("ledger"):
        contracts = [{"ledger": payload.get("ledger")}]
    return {"ledger": _booked_ledger(payload.get("conversation_id") or "", contracts)}


@activity.defn(name="waiting_prompt")
def waiting_prompt(payload: dict) -> dict:
    from kai.engine.prompts import build_prompt
    from kai.models import Run

    graph = payload.get("graph") or {}
    with session_scope() as session:
        row = session.get(Run, payload.get("run_id") or "")
        if row is None or row.status in {"completed", "blocked"} or row.result:
            return {"finished": True, "prompt": None}
        step = next((item for item in graph.get("steps") or [] if item.get("id") == row.step_id), None)
        if step is None or step.get("kind") in {"result", "stop"}:
            return {"finished": True, "prompt": None}
        if row.status not in {"waiting", "active"}:
            return {"finished": True, "prompt": None}
        prompt = build_prompt(step, row.slots or {}, graph)
        # A result/stop node has no customer prompt — treat the run as done even if status lags.
        if prompt is None and step.get("kind") not in {"ask", "approval", "question", "question_popup"}:
            return {"finished": True, "prompt": None}
        return {"finished": False, "prompt": prompt}


@activity.defn(name="normal_chat")
def normal_chat_activity(payload: dict) -> dict:
    text = greeting_reply(payload.get("text") or "")
    if not text and gemini_enabled():
        text = normal_chat(payload.get("text") or "", payload.get("never") or [])
    if not text:
        text = "I can help with topping up an account, a balance rule, or a sweep. Tell me what you want to do."
    return {"text": text}


@activity.defn(name="append_message")
def append_message(payload: dict) -> dict:
    from kai.engine.never import scrub_never

    text = payload.get("text") or ""
    meta = dict(payload.get("payload") or {})
    if payload.get("role") == "kai":
        text, breached, phrase = scrub_never(text, payload.get("never") or meta.get("never") or [])
        if breached:
            meta["never_breach"] = phrase
    with session_scope() as session:
        session.add(
            Message(
                tenant_id=payload["tenant_id"],
                conversation_id=payload["conversation_id"],
                role=payload["role"],
                text=text,
                payload=meta,
            )
        )
    _publish(payload.get("conversation_id"), {"role": payload["role"], "text": text})
    return {"ok": True, "text": text, "never_breach": meta.get("never_breach") or ""}


@activity.defn(name="record_event")
def record_event(payload: dict) -> dict:
    event = payload.get("event") or {}
    if not event.get("step_id") or not event.get("type"):
        return {"ok": False}
    _store_event(payload, event)
    return {"ok": True}


def _fetch_step_body(payload: dict) -> dict:
    step = payload["step"]
    slots = payload["slots"]
    ledger = payload.get("ledger") or {"accounts": {}}
    data = _fetch_via_connector(payload["tenant_id"], step, slots, ledger)
    event = run_event("fetch", step["id"], payload={"data": data, "kind": step.get("kind") or "", "type": step.get("type") or ""})
    event["payload"] = {**(event.get("payload") or {}), **(data if isinstance(data, dict) else {"data": data})}
    _store_event(payload, event)
    return {"data": data, "event": event}


@activity.defn(name="fetch_step")
def fetch_step(payload: dict) -> dict:
    from kai.config import apply_langsmith_env, langsmith_enabled

    apply_langsmith_env()
    step = payload.get("step") or {}
    if langsmith_enabled():
        try:
            from langsmith import traceable

            return traceable(
                name=f"kai.fetch.{step.get('type') or step.get('id') or 'step'}",
                run_type="tool",
                tags=["kai", "fetch", str(step.get("type") or "")],
            )(_fetch_step_body)(payload)
        except Exception:
            pass
    return _fetch_step_body(payload)


def _act_step_body(payload: dict) -> dict:
    step = payload["step"]
    existing = _existing_action(payload["run_id"], step["id"])
    if existing:
        event = run_event("action", step["id"], payload=existing, retries=1)
        return {"data": existing, "event": event}
    move_id = f"move_{payload['run_id']}:{step['id']}"
    ledger = copy.deepcopy(payload.get("ledger") or {})
    data = resolve_action(step, payload["slots"], ledger, move_id)
    if data.get("ok") and _moves_money(step):
        apply_move(ledger, payload.get("slots") or {})
    event = run_event("action", step["id"], payload={**(data if isinstance(data, dict) else {"data": data}), "kind": step.get("kind") or "", "type": step.get("type") or ""})
    _store_event(payload, event)
    return {"data": data, "event": event, "ledger": ledger}


@activity.defn(name="act_step")
def act_step(payload: dict) -> dict:
    from kai.config import apply_langsmith_env, langsmith_enabled

    apply_langsmith_env()
    step = payload.get("step") or {}
    if langsmith_enabled():
        try:
            from langsmith import traceable

            return traceable(
                name=f"kai.action.{step.get('type') or step.get('id') or 'step'}",
                run_type="tool",
                tags=["kai", "action", str(step.get("type") or "")],
            )(_act_step_body)(payload)
        except Exception:
            pass
    return _act_step_body(payload)


def _moves_money(step: dict) -> bool:
    role = str((step.get("config") or {}).get("role") or step.get("type") or "")
    return role in {"move_funds", "move_money"} or step.get("type") in {"move_money", "move_funds"}


@activity.defn(name="persist_run")
def persist_run(payload: dict) -> dict:
    with session_scope() as session:
        row = session.get(Run, payload["run_id"])
        if row is None:
            row = Run(id=payload["run_id"], tenant_id=payload["tenant_id"], workflow_id=payload.get("workflow_id") or "")
            session.add(row)
        row.version_id = payload.get("version_id")
        row.temporal_id = payload["run_id"]
        row.origin = payload.get("origin") or "chat"
        row.status = payload.get("status") or "active"
        row.step_id = payload.get("step_id") or ""
        row.slots = payload.get("slots") or {}
        row.trace = payload.get("trace") or []
        row.result = payload.get("result")
        row.ledger = payload.get("ledger") or {}
        row.messages = payload.get("messages") or []
        row.timings_ms = payload.get("timings_ms") or {}
        row.user_replies = payload.get("user_replies") or []
        row.slots_from_message = payload.get("slots_from_message") or {}
        row.missing_slot_count = int(payload.get("missing_slot_count") or 0)
        row.conversation_id = payload.get("conversation_id")
        if not row.code:
            codes = [code for (code,) in session.query(Run.code).filter(Run.tenant_id == row.tenant_id).all()]
            row.code = payload.get("code") or next_run_code(codes)
        if payload.get("customer_id"):
            row.customer_id = payload["customer_id"]
        if payload.get("rule_id"):
            row.rule_id = payload["rule_id"]
        if row.started_at is None:
            row.started_at = datetime.utcnow()
        if payload.get("result"):
            row.ended_on = payload.get("ended_on") or ended_label(payload.get("result"))
        if payload.get("status") == "completed" and row.finished_at is None:
            row.finished_at = datetime.utcnow()
        if payload.get("status") == "completed" and payload.get("graph"):
            contract = payload.get("contract") or {}
            config = payload.get("eval_config") or {}
            record = {
                "id": payload["run_id"],
                "origin": row.origin,
                "status": "completed",
                "result": row.result,
                "slots": row.slots,
                "slots_from_message": row.slots_from_message,
                "slots_confirmed": {key: row.slots.get(key) for key in row.slots_from_message},
                "user_replies": row.user_replies,
                "missing_slot_count": row.missing_slot_count,
                "source_was_short": "others" in (row.trace or []),
                "messages": row.messages,
                "events": payload.get("events") or [],
                "ledger": row.ledger,
                "timings_ms": row.timings_ms,
            }
            scored = score_run(record, payload["graph"], contract, config)
            existing = session.query(RunScore).filter(RunScore.run_id == row.id).one_or_none()
            if existing is None:
                existing = RunScore(tenant_id=row.tenant_id, run_id=row.id)
                session.add(existing)
            existing.chat = scored["chat"]
            existing.outcome = scored["outcome"]
            existing.checks = scored["checks"]
            existing.flagged = scored["flagged"]
            existing.reasons = scored["reasons"]
        _sync_run_events(session, payload["tenant_id"], payload["run_id"], payload.get("events") or [])
    if payload.get("conversation_id") and payload.get("messages"):
        _publish(payload["conversation_id"], {"event": "run", "step_id": payload.get("step_id"), "status": payload.get("status")})
    return {"ok": True}


def _sync_run_events(session, tenant_id: str, run_id: str, events: list) -> None:
    """Persist the full in-memory event trail so conditions/replies survive beside fetch/action."""
    existing = {
        (row.type, row.step_id, str((row.payload or {}).get("port") or ""), str((row.payload or {}).get("text") or "")): row
        for row in session.query(RunEvent).filter(RunEvent.run_id == run_id).all()
    }
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        step_id = str(event.get("step_id") or "")
        if not event_type or not step_id:
            continue
        body = dict(event.get("payload") or {})
        if event.get("port") and "port" not in body:
            body["port"] = event["port"]
        if event.get("text") is not None and "text" not in body:
            body["text"] = event["text"]
        key = (event_type, step_id, str(body.get("port") or ""), str(body.get("text") or ""))
        if key in existing:
            continue
        session.add(
            RunEvent(
                tenant_id=tenant_id,
                run_id=run_id,
                step_id=step_id,
                type=event_type,
                payload=body,
                retries=int(event.get("retries") or 0),
                created_at=datetime.utcnow(),
            )
        )


@activity.defn(name="judge_line")
def judge_line_activity(payload: dict) -> dict:
    ok = judge_line(payload.get("expected") or "", payload.get("actual") or "")
    if ok is None:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return {"ok": ok}


def _fetch_via_connector(tenant_id: str, step: dict, slots: dict, ledger: dict) -> dict:
    with session_scope() as session:
        connector = (
            session.query(Connector)
            .filter(Connector.tenant_id == tenant_id, Connector.type == step["type"], Connector.base_url != "")
            .one_or_none()
        )
        base_url = connector.base_url if connector else ""
        allow = list(connector.host_allowlist or []) if connector else []
    if base_url:
        host = urlparse(base_url).hostname or ""
        if allow and host not in allow:
            raise RuntimeError(f"host {host} is not on the connector allowlist")
        response = httpx.post(base_url, json={"slots": slots, "step": step["id"]}, timeout=10)
        response.raise_for_status()
        return response.json()
    return resolve_fetch(step, slots, ledger)


def _store_event(payload: dict, event: dict) -> None:
    with session_scope() as session:
        session.add(
            RunEvent(
                tenant_id=payload["tenant_id"],
                run_id=payload["run_id"],
                step_id=event["step_id"],
                type=event["type"],
                payload=event.get("payload") or {},
                retries=int(event.get("retries") or 0),
                created_at=datetime.utcnow(),
            )
        )


def _existing_action(run_id: str, step_id: str) -> dict | None:
    with session_scope() as session:
        row = (
            session.query(RunEvent)
            .filter(RunEvent.run_id == run_id, RunEvent.step_id == step_id, RunEvent.type == "action")
            .order_by(RunEvent.created_at.asc())
            .first()
        )
        if row and row.payload:
            return row.payload
    return None


def rendered(step: dict, slots: dict) -> str:
    return render_line(step.get("line"), slots)
