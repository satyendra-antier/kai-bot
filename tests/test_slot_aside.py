"""Mid-slot clarifying questions must answer contextually, then re-ask."""

from __future__ import annotations

from kai.engine.slots import (
    asks_capacity,
    describe_capacity,
    fallback_turn,
    grounded_reply,
    looks_like_question,
)
from kai.engine.topup import PREVIEW_LEDGER, chat_contract


def test_how_much_can_i_move_is_a_question():
    assert looks_like_question("how much can i move")
    assert asks_capacity("how much can i move")
    assert asks_capacity("what's the most I can send")


def test_capacity_answer_uses_filled_source_then_reasks_amount():
    decision = {
        "action": "answer",
        "needs": "capacity",
        "workflow_key": "sweep-between-accounts",
        "model_input": "how much can i move",
    }
    line = grounded_reply(decision, PREVIEW_LEDGER, "amount", filled={"source": "EUR"}, text="how much can i move")
    assert "EUR" in line
    assert "62.40" in line or "€62.40" in line
    assert "How much should I move?" in line
    assert "CARD" not in line  # not a full dump


def test_fallback_during_amount_slot_answers_capacity():
    contract = chat_contract()
    contracts = [
        {
            "workflow_key": "low-balance-top-up",
            "workflow_id": "low-balance-top-up",
            "contract": contract,
            "ledger": PREVIEW_LEDGER,
        }
    ]
    state = {
        "phase": "slot",
        "pending_slot": "amount",
        "workflow_key": "low-balance-top-up",
        "filled": {"target": "USD", "source": "EUR"},
    }
    decision = fallback_turn("how much can i move", contracts, state)
    assert decision["action"] == "answer"
    assert decision["needs"] == "capacity"
    spoken = grounded_reply(decision, PREVIEW_LEDGER, "amount", filled=state["filled"], text="how much can i move")
    assert "EUR" in spoken and "How much should I move?" in spoken


def test_describe_capacity_respects_floor():
    ledger = {
        "accounts": {
            "EUR": {"amount": 100, "currency": "EUR", "floor": 40, "display": "€100.00"},
        }
    }
    line = describe_capacity(ledger, {"source": "EUR"}, "how much can i move")
    assert "EUR" in line
    assert "€60.00" in line or "60" in line
