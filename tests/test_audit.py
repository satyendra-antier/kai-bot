from kai.activities import resolve_turn
from kai.engine.advance import run_until_wait
from kai.engine.audit import run_event, step, turn_trail
from kai.engine.demos import bundles
from kai.engine.topup import PREVIEW_LEDGER


def test_turn_trail_covers_redact_local_model_guard_decision():
    trail = turn_trail(
        pii=["email"],
        tool="balance",
        local={"action": "start", "workflow_key": "demo-balance", "confidence": 0.9, "slots": {"target": "EUR"}},
        model={"action": "chat", "workflow_key": "", "confidence": 0.2, "slots": {}},
        model_used=True,
        chosen={"action": "start", "workflow_key": "demo-balance", "confidence": 0.9, "slots": {"target": {"value": "EUR"}}},
        route="local_beats_chat",
        guard={"action": "answer", "reply": "paused"},
    )
    ids = [row["id"] for row in trail]
    assert ids == ["redact", "tool", "local", "model", "route", "guard", "decision"]
    assert "email" in trail[0]["detail"]
    assert "demo-balance" in trail[-1]["detail"]


def test_run_event_carries_port_and_payload():
    event = run_event("reply", "confirm", port="yes", text="yes", payload={"kind": "ask"})
    assert event["type"] == "reply"
    assert event["step_id"] == "confirm"
    assert event["payload"]["port"] == "yes"
    assert event["payload"]["text"] == "yes"
    assert event["payload"]["kind"] == "ask"
    assert event["at"].endswith("Z")


def test_resolve_turn_stamps_ai_steps_without_gemini(monkeypatch):
    monkeypatch.setattr("kai.activities.gemini_enabled", lambda: False)
    contracts = [
        {
            "workflow_id": "demo-balance",
            "workflow_key": "demo-balance",
            "contract": {
                "description": "balance check",
                "examples": ["demo balance", "check EUR"],
                "exclusions": [],
                "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
                "slots": [],
            },
            "ledger": PREVIEW_LEDGER,
            "graph": {},
        }
    ]
    decision = resolve_turn({"text": "demo balance", "phase": "idle", "contracts": contracts})
    assert decision["workflow_key"] == "demo-balance"
    assert decision["confidence"] >= 0.75
    assert decision.get("ai_steps")
    ids = [row["id"] for row in decision["ai_steps"]]
    assert ids[0] == "redact"
    assert "local" in ids
    assert ids[-1] == "decision"
    assert any(row["id"] == "model" and (row.get("data") or {}).get("skipped") for row in decision["ai_steps"])


def test_demo_balance_run_records_condition_events():
    package = next(item for item in bundles() if item["key"] == "demo-balance")
    story = next(item for item in package["stories"] if item["id"] == "demo_balance_usd")
    state = run_until_wait(
        package["graph"],
        origin="chat",
        slots=story["slots"],
        ledger=story["ledger"],
        replies=[],
        run_id=story["id"],
    )
    types = [event["type"] for event in state["events"]]
    assert "condition" in types
    low = next(event for event in state["events"] if event["type"] == "condition" and event["step_id"] == "is_low")
    assert low["port"] == "true"
    assert low["payload"]["port"] == "true"


def test_audit_step_helper_drops_empty_data():
    row = step("route", "Route", "choice", route="choice", unused="", empty_list=[])
    assert row["id"] == "route"
    assert row["data"]["route"] == "choice"
    assert "unused" not in row["data"]
    assert "empty_list" not in row["data"]
