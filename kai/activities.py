"""Temporal activities. All Gemini and database access stays here."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import copy

import httpx
from temporalio import activity

from kai.db import session_scope
from kai.engine.advance import apply_move, render_line, resolve_action, resolve_fetch
from kai.engine.scoring import score_run
from kai.engine.slots import HELP, accept_model, extract_slots, fallback_turn, greeting_reply, idle_line, pinned_turn, score_intent
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
    contract = contract or {}
    return {
        "workflow_id": row.id,
        "workflow_key": row.key,
        "version_id": version_id,
        "graph": graph or {},
        "contract": contract,
        "eval_config": eval_config or {},
        "ledger": contract.get("preview_ledger") or {"accounts": {}},
    }


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
                contracts.append(_contract_row(row, draft.get("graph") or {}, draft.get("chat_contract") or {}, draft.get("eval_config") or {}, "draft"))
                continue
            if not row.published_version_id:
                continue
            version = session.get(WorkflowVersion, row.published_version_id)
            if version is None:
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
        score = score_intent(text, item["contract"])
        if score > best_score:
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
            "reason": "no published workflow matched",
            "source": "deterministic",
        }
    return {
        "workflow_key": best["workflow_key"],
        "workflow_id": best["workflow_id"],
        "version_id": best["version_id"],
        "confidence": best_score,
        "reason": "matched the published description",
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
        return pinned
    local = fallback_turn(text, contracts, state)
    shown, kinds = redact_pii(text)
    local["model_input"] = shown
    local["pii"] = kinds
    local["tool"] = select_tool(text) or ""
    if gemini_enabled():
        accepted = accept_model(gemini_interpret(shown, _turn_catalog(contracts), state), text, contracts, state)
        if accepted:
            if accepted["action"] in {"chat", "answer"} and accepted["needs"] == "none" and not accepted.get("reply"):
                accepted["reply"] = local.get("reply") or greeting_reply(text) or idle_line(contracts)
            strong_local = local["action"] in {"start", "fill"} and local["confidence"] >= 0.75
            pending = state.get("pending_slot") or ""
            if strong_local and pending == "amount" and "amount" in (local.get("slots") or {}):
                return local
            if strong_local and pending and pending in (local.get("slots") or {}) and pending not in (accepted.get("slots") or {}):
                return local
            if accepted["needs"] != "none":
                return accepted
            if strong_local and accepted["action"] in {"chat", "answer"} and not accepted.get("slots"):
                return local
            if accepted["action"] in {"start", "fill"} and local["confidence"] > accepted["confidence"]:
                accepted["confidence"] = local["confidence"]
                accepted["workflow_key"] = accepted["workflow_key"] or local["workflow_key"]
            accepted["model_input"] = shown
            accepted["pii"] = kinds
            accepted["tool"] = local["tool"]
            return accepted
    if local["action"] in {"chat", "answer"} and local["needs"] == "none" and not local.get("reply"):
        local["reply"] = greeting_reply(text) or idle_line(contracts)
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


@activity.defn(name="interpret_turn")
def interpret_turn(payload: dict) -> dict:
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
    decision = run_conversation(
        {**turn_payload, "pinned": pinned, "workflow_key": state.get("workflow_key") or ""},
        resolve_turn,
    )
    steps = [{"id": "redact", "label": "Redact", "detail": ", ".join(kinds) if kinds else "Nothing held back"}]
    steps.extend(list(decision.get("ai_steps") or []))
    if decision.get("source"):
        steps.append(
            {
                "id": "source",
                "label": "Scorer",
                "detail": f"{decision.get('source')} · {decision.get('action')} {decision.get('workflow_key') or ''} @ {decision.get('confidence') or 0}".strip(),
            }
        )
    replaced = False
    open_prompt = bool(prompt)
    if not pinned and ((phase in {"idle", "", None} and not state.get("pending_slot")) or open_prompt):
        profile = _chat_profile(payload.get("conversation_id") or "")
        for item in contracts:
            rate = (item.get("ledger") or {}).get("rate")
            if rate not in (None, ""):
                profile["rate"] = rate
                break
        guarded = apply_guard(
            payload.get("text") or "",
            decision,
            profile,
            contracts,
            kill_switch=os.environ.get("KAI_KILL_SWITCH") == "1",
        )
        if guarded:
            steps.append(
                {
                    "id": "guard",
                    "label": "Guard",
                    "detail": f"{guarded.get('action')} {guarded.get('workflow_key') or guarded.get('reply') or ''}".strip(),
                }
            )
            guarded["ai_steps"] = steps
            guarded["route"] = decision.get("route") or ""
            guarded["strike"] = decision.get("strike")
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
    return _stamp_turn(payload.get("conversation_id") or "", payload.get("text") or "", decision)


def _stamp_turn(conversation_id: str, text: str, decision: dict) -> dict:
    shown, kinds = redact_pii(text)
    decision["model_input"] = decision.get("model_input") or shown
    decision["pii"] = decision.get("pii") if decision.get("pii") is not None else kinds
    decision["tool"] = decision.get("tool") or select_tool(text) or ""
    if not conversation_id:
        return decision
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
                "ai_steps": list(decision.get("ai_steps") or []),
            }
            session.add(row)
    return decision


def _chat_profile(conversation_id: str) -> dict[str, Any]:
    """An account exists only after onboarding in this chat finishes."""
    profile: dict[str, Any] = {"account": False, "kind": "", "opted_out": False, "ledger": {"accounts": {}}, "rate": None}
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
    base: dict[str, Any] = {"accounts": {}}
    for item in contracts:
        ledger = item.get("ledger") or {}
        if ledger.get("accounts"):
            base = ledger
            break
    booked = copy.deepcopy(base)
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


@activity.defn(name="waiting_prompt")
def waiting_prompt(payload: dict) -> dict:
    from kai.engine.prompts import build_prompt
    from kai.models import Run

    graph = payload.get("graph") or {}
    with session_scope() as session:
        row = session.get(Run, payload.get("run_id") or "")
        if row is None or row.status in {"completed", "blocked"}:
            return {"finished": True, "prompt": None}
        step = next((item for item in graph.get("steps") or [] if item.get("id") == row.step_id), None)
        if row.status not in {"waiting", "active"} or step is None:
            return {"finished": True, "prompt": None}
        return {"finished": False, "prompt": build_prompt(step, row.slots or {}, graph)}


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
    with session_scope() as session:
        session.add(
            Message(
                tenant_id=payload["tenant_id"],
                conversation_id=payload["conversation_id"],
                role=payload["role"],
                text=payload["text"],
                payload=payload.get("payload") or {},
            )
        )
    _publish(payload.get("conversation_id"), {"role": payload["role"], "text": payload["text"]})
    return {"ok": True}


@activity.defn(name="fetch_step")
def fetch_step(payload: dict) -> dict:
    step = payload["step"]
    slots = payload["slots"]
    ledger = payload.get("ledger") or {"accounts": {}}
    data = _fetch_via_connector(payload["tenant_id"], step, slots, ledger)
    event = {"type": "fetch", "step_id": step["id"], "payload": data, "retries": 0}
    _store_event(payload, event)
    return {"data": data, "event": event}


@activity.defn(name="act_step")
def act_step(payload: dict) -> dict:
    step = payload["step"]
    existing = _existing_action(payload["run_id"], step["id"])
    if existing:
        return {"data": existing, "event": {"type": "action", "step_id": step["id"], "payload": existing, "retries": 1}}
    move_id = f"move_{payload['run_id']}:{step['id']}"
    ledger = copy.deepcopy(payload.get("ledger") or {})
    data = resolve_action(step, payload["slots"], ledger, move_id)
    if data.get("ok") and _moves_money(step):
        apply_move(ledger, payload.get("slots") or {})
    event = {"type": "action", "step_id": step["id"], "payload": data, "retries": 0}
    _store_event(payload, event)
    return {"data": data, "event": event, "ledger": ledger}


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
    if payload.get("conversation_id") and payload.get("messages"):
        _publish(payload["conversation_id"], {"event": "run", "step_id": payload.get("step_id"), "status": payload.get("status")})
    return {"ok": True}


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
