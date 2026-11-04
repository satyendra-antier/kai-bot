from kai.engine.advance import run_until_wait
from kai.engine.scoring import score_run, weights_valid
from kai.engine.topup import chat_contract, eval_config, low_balance_topup_graph, stories


def _run_record(story: dict, state: dict) -> dict:
    return {
        "id": story["id"],
        "origin": story["origin"],
        "status": "completed" if state["result"] else "waiting",
        "result": state["result"],
        "slots": state["slots"],
        "slots_from_message": {"target": "USD", "amount": story["slots"].get("amount"), "source": "EUR"} if story["origin"] == "chat" else {},
        "slots_confirmed": {"target": "USD", "amount": story["slots"].get("amount"), "source": state["slots"].get("source")},
        "user_replies": story["replies"],
        "missing_slot_count": 0,
        "source_was_short": story["id"] == "named_short_other_offered",
        "messages": state["messages"],
        "events": state["events"],
        "ledger": story["ledger"],
        "timings_ms": {step_id: 100 for step_id in state["trace"]},
    }


def test_weights_must_sum_to_100():
    config = eval_config()
    assert weights_valid(config)
    broken = eval_config()
    broken["weights"]["chat"]["intent"] = 10
    assert not weights_valid(broken)


def test_completed_chat_story_scores_clean():
    graph = low_balance_topup_graph()
    story = next(item for item in stories() if item["id"] == "chat_enough_eur")
    state = run_until_wait(graph, origin="chat", slots=story["slots"], ledger=story["ledger"], replies=story["replies"])
    scored = score_run(_run_record(story, state), graph, chat_contract(), eval_config())
    assert scored["flagged"] is False
    assert scored["chat"] == 100
    assert scored["outcome"] == 100
    assert scored["checks"]["guardrails"] == 100


def test_guardrail_breach_flags_even_when_scores_are_high():
    graph = low_balance_topup_graph()
    story = next(item for item in stories() if item["id"] == "chat_enough_eur")
    state = run_until_wait(graph, origin="chat", slots=story["slots"], ledger=story["ledger"], replies=story["replies"])
    record = _run_record(story, state)
    record["messages"] = [{"step_id": "done", "text": "This is a guaranteed profit.", "exact": "USD is back at $120.00."}]
    scored = score_run(record, graph, chat_contract(), eval_config())
    assert scored["checks"]["guardrails"] == 0
    assert scored["flagged"] is True
    assert "guardrail" in scored["reasons"]


def test_rule_run_has_no_chat_composite():
    graph = low_balance_topup_graph()
    story = stories()[0]
    state = run_until_wait(graph, origin="rule", slots=story["slots"], ledger=story["ledger"], replies=story["replies"])
    scored = score_run(_run_record(story, state), graph, chat_contract(), eval_config())
    assert scored["chat"] is None
    assert scored["outcome"] == 100
