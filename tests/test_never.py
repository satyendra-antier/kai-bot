from kai.activities import append_message
from kai.engine.catalog import bundles
from kai.engine.demos import bundles as demo_bundles
from kai.engine.never import SAFE_FALLBACK, breach_never, scrub_never, with_forbidden
from kai.engine.scoring import score_run
from kai.engine.topup import chat_contract, eval_config, low_balance_topup_graph, stories
from kai.engine.advance import run_until_wait


def test_every_chat_workflow_declares_kai_never():
    packages = [item for item in bundles() if "chat" in (item.get("channels") or [])]
    packages += [item for item in demo_bundles() if "chat" in (item.get("channels") or [])]
    assert packages
    for package in packages:
        never = (package.get("chat_contract") or {}).get("never") or []
        assert never, package["key"]
        for rule in never:
            assert rule.get("text") or rule.get("id")
            if rule.get("mode") != "order":
                assert with_forbidden(rule).get("forbidden")


def test_scrub_never_blocks_forbidden_phrase():
    never = [{"id": "x", "mode": "judged", "text": "Never promise a profit", "forbidden": ["guaranteed profit"]}]
    assert breach_never("This is a guaranteed profit.", never) == "guaranteed profit"
    text, breached, phrase = scrub_never("This is a guaranteed profit.", never)
    assert breached and phrase == "guaranteed profit"
    assert text == SAFE_FALLBACK
    clean, ok, _ = scrub_never("USD is back at $300.", never)
    assert not ok and clean.startswith("USD")


def test_append_message_scrubs_kai_never(monkeypatch):
    saved = {}

    class FakeSession:
        def add(self, row):
            saved["text"] = row.text
            saved["payload"] = row.payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("kai.activities.session_scope", lambda: FakeSession())
    monkeypatch.setattr("kai.activities._publish", lambda *args, **kwargs: None)
    result = append_message(
        {
            "tenant_id": "demo",
            "conversation_id": "c1",
            "role": "kai",
            "text": "This is a guaranteed profit.",
            "never": [{"mode": "judged", "text": "Never promise a profit", "forbidden": ["guaranteed profit"]}],
        }
    )
    assert result["never_breach"] == "guaranteed profit"
    assert saved["text"] == SAFE_FALLBACK
    assert saved["payload"]["never_breach"] == "guaranteed profit"


def test_guardrails_score_uses_never_forbidden():
    graph = low_balance_topup_graph()
    story = next(item for item in stories() if item["id"] == "chat_enough_eur")
    state = run_until_wait(graph, origin="chat", slots=story["slots"], ledger=story["ledger"], replies=story["replies"])
    record = {
        "id": story["id"],
        "origin": "chat",
        "status": "completed",
        "result": state["result"],
        "slots": state["slots"],
        "slots_from_message": {},
        "slots_confirmed": {},
        "user_replies": story["replies"],
        "missing_slot_count": 0,
        "source_was_short": False,
        "messages": [{"step_id": "done", "text": "This is a guaranteed profit."}],
        "events": state["events"],
        "ledger": story["ledger"],
        "timings_ms": {},
    }
    scored = score_run(record, graph, chat_contract(), eval_config())
    assert scored["checks"]["guardrails"] == 0
    assert scored["flagged"] is True
