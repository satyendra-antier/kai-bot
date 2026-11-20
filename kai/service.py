"""Publish gate, Try it, and score rebuilds. All three call the pure engine."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from kai.engine.advance import run_until_wait
from kai.engine.scoring import score_run, weights_valid
from kai.engine.slots import decide_band, extract_slots, missing_slot, score_intent, slot_question
from kai.models import Run, RunScore, Workflow, WorkflowVersion


def story_failures(graph: dict, suite: list[dict]) -> list[str]:
    failed = []
    for story in suite:
        state = run_until_wait(
            graph,
            origin=story["origin"],
            slots=story.get("slots") or {},
            ledger=story.get("ledger") or {"accounts": {}},
            replies=list(story.get("replies") or []),
            run_id=story.get("id") or "story",
        )
        if state.get("result") != story.get("expected_result") or len(state.get("trace") or []) != story.get("expected_steps"):
            failed.append(story.get("id") or story.get("name") or "story")
    return failed


def try_utterance(draft: dict, text: str, workflow_key: str = "") -> dict[str, Any]:
    contract = draft.get("chat_contract") or {}
    graph = draft.get("graph") or {}
    score = score_intent(text, contract, workflow_key=workflow_key)
    band = decide_band(score, contract.get("bands") or {})
    extracted = extract_slots(text)
    slots = {key: item["value"] for key, item in extracted.items()}
    slot_from = {key: item["from"] for key, item in extracted.items()}
    if band == "not_this":
        idle = str(contract.get("idle") or "").strip()
        messages = [{"step_id": "idle", "text": idle}] if idle else []
        return {"band": band, "confidence": score, "slots": slot_from, "messages": messages, "trace": [], "status": "idle"}
    if band == "ask":
        entry = ((graph.get("entries") or {}).get("chat")) or "chat_request"
        line = str(contract.get("setup") or "").strip() or (contract.get("lines") or {}).get(entry) or "Is that what you want me to do?"
        return {"band": band, "confidence": score, "slots": slot_from, "messages": [{"step_id": "setup", "text": line}], "trace": [], "status": "ask"}
    missing = missing_slot(contract.get("slots") or [], slots)
    if missing:
        return {
            "band": band,
            "confidence": score,
            "slots": slot_from,
            "messages": [{"step_id": missing["key"], "text": slot_question(missing["key"], contract.get("slots"))}],
            "trace": [],
            "status": "slot",
            "missing": missing["key"],
        }
    state = run_until_wait(
        graph,
        origin="chat",
        slots=slots,
        ledger=contract.get("preview_ledger") or {"accounts": {}},
        replies=[],
    )
    return {
        "band": band,
        "confidence": score,
        "slots": slot_from,
        "values": slots,
        "messages": state["messages"],
        "trace": state["trace"],
        "status": state["status"],
        "step_id": state["step_id"],
        "result": state.get("result"),
    }


def preview_run(draft: dict, text: str, origin: str = "chat") -> dict[str, Any]:
    """Walk a draft. A chat utterance that already has a trace wins; otherwise the graph runs from its entry."""
    tried = try_utterance(draft, text) if text and origin == "chat" else None
    if tried and tried.get("trace"):
        return tried
    graph = draft.get("graph") or {}
    entries = graph.get("entries") or {}
    if origin not in entries and entries:
        origin = next(iter(entries))
    slots = dict((tried or {}).get("values") or {})
    slots.setdefault("origin", origin)
    ledger = (draft.get("chat_contract") or {}).get("preview_ledger") or {"accounts": {}}
    try:
        state = run_until_wait(graph, origin=origin, slots=slots, ledger=ledger, replies=[])
    except Exception as exc:
        if tried:
            return {**tried, "error": str(exc)}
        return {"status": "error", "error": str(exc), "trace": [], "messages": []}
    walked = {
        "trace": state["trace"],
        "messages": state["messages"],
        "status": state["status"],
        "step_id": state["step_id"],
        "result": state.get("result"),
    }
    if tried:
        return {**tried, **walked}
    return walked


def trace_story(draft: dict, story_id: str) -> dict[str, Any]:
    story = next(item for item in draft.get("stories") or [] if item["id"] == story_id)
    state = run_until_wait(
        draft["graph"],
        origin=story["origin"],
        slots=story.get("slots") or {},
        ledger=story.get("ledger") or {"accounts": {}},
        replies=list(story.get("replies") or []),
        run_id=story_id,
    )
    return {"trace": state["trace"], "result": state.get("result"), "messages": state["messages"], "status": state["status"]}


def rescore(session: Session, run: Run, reviewer_label: str | None = None) -> RunScore:
    workflow = session.get(Workflow, run.workflow_id)
    version = session.get(WorkflowVersion, run.version_id) if run.version_id else None
    graph = version.graph if version else (workflow.draft.get("graph") if workflow else {})
    contract = version.chat_contract if version else (workflow.draft.get("chat_contract") if workflow else {})
    config = version.eval_config if version else (workflow.draft.get("eval_config") if workflow else {})
    record = {
        "id": run.id,
        "origin": run.origin,
        "status": run.status,
        "result": run.result,
        "slots": run.slots,
        "slots_from_message": run.slots_from_message,
        "slots_confirmed": {key: (run.slots or {}).get(key) for key in (run.slots_from_message or {})},
        "user_replies": run.user_replies or [],
        "missing_slot_count": run.missing_slot_count or 0,
        "source_was_short": "check_other_accounts" in (run.trace or []) or "others" in (run.trace or []),
        "messages": run.messages or [],
        "events": [],
        "ledger": run.ledger or {},
        "timings_ms": run.timings_ms or {},
        "reviewer_label": reviewer_label,
        "customer_corrected": reviewer_label == "wrong",
    }
    scored = score_run(record, graph, contract or {}, config or {})
    row = session.query(RunScore).filter(RunScore.run_id == run.id).one_or_none()
    if row is None:
        row = RunScore(tenant_id=run.tenant_id, run_id=run.id)
        session.add(row)
    row.chat = scored["chat"]
    row.outcome = scored["outcome"]
    row.checks = scored["checks"]
    row.flagged = scored["flagged"]
    row.reasons = scored["reasons"]
    row.reviewer_label = reviewer_label
    return row


def require_weights(config: dict) -> None:
    if not weights_valid(config):
        raise ValueError("weights in each group must sum to 100")
