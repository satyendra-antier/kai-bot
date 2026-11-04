from kai.engine.advance import run_until_wait
from kai.engine.catalog import stub_graph
from kai.service import preview_run, story_failures, try_utterance
from kai.engine.registry import KINDS, STEP_TYPES
from kai.engine.slots import accept_model, asks_for_accounts, decide_band, describe_accounts, extract_slots, fallback_turn, greeting_reply, grounded_reply, score_intent
from kai.engine.topup import PREVIEW_LEDGER, chat_contract, low_balance_topup_graph, stories


def test_registry_counts():
    assert len(KINDS) == 9
    assert len(STEP_TYPES) == 26
    assert len({item["kind"] for item in STEP_TYPES}) == 9
    by_type = {item["type"]: item for item in STEP_TYPES}
    assert by_type["enough"]["title"] == "Is there enough"
    assert by_type["enough"]["purpose"] == "spare ≥ amount"
    assert by_type["enough"]["slug"] == "enough"
    assert by_type["get_accounts"]["title"] == "Get accounts"
    assert by_type["get_accounts"]["slug"] == "get_accounts"


def test_graph_has_23_steps():
    graph = low_balance_topup_graph()
    assert len(graph["steps"]) == 23


def test_seven_stories_are_the_publish_gate():
    graph = low_balance_topup_graph()
    suite = stories()
    assert len(suite) == 7
    for story in suite:
        state = run_until_wait(
            graph,
            origin=story["origin"],
            slots=story["slots"],
            ledger=story["ledger"],
            replies=story["replies"],
            run_id=story["id"],
        )
        assert state["result"] == story["expected_result"], story["name"]
        assert len(state["trace"]) == story["expected_steps"], (story["name"], state["trace"])
        if story.get("expect_source"):
            assert state["slots"]["source"] == story["expect_source"]


def test_preview_utterance_stops_at_offer():
    graph = low_balance_topup_graph()
    filled = extract_slots("Top up my USD with €100 from EUR")
    slots = {key: item["value"] for key, item in filled.items()}
    state = run_until_wait(graph, origin="chat", slots=slots, ledger=PREVIEW_LEDGER, replies=[])
    assert state["trace"] == ["chat_request", "get_source_balance", "source_enough", "check_other_accounts", "any_sufficient", "offer_account"]
    assert state["status"] == "waiting"
    assert state["messages"][0]["text"] == (
        "Your EUR account has €62.40 — not enough for €100.00. Let me check your other accounts."
    )
    assert state["messages"][1]["text"] == "USDT (950.00 USDT) or GBP (£210.00) can cover it. Which should I use?"


def test_chat_limit_failure_returns_to_offer():
    graph = low_balance_topup_graph()
    slots = {
        "target": "USD",
        "amount": {"amount": 100, "currency": "EUR", "display": "€100.00"},
        "source": "EUR",
        "runs_left": 3,
        "cap": 10000,
        "limit_failure": "balance_moved",
    }
    state = run_until_wait(
        graph,
        origin="chat",
        slots=slots,
        ledger=PREVIEW_LEDGER,
        replies=["USDT", "yes"],
    )
    assert state["trace"].count("offer_account") == 2
    assert state["step_id"] == "offer_account"
    assert state["status"] == "waiting"


def test_move_is_idempotent():
    graph = low_balance_topup_graph()
    story = stories()[0]
    moves: dict[str, str] = {}
    first = run_until_wait(graph, origin="rule", slots=story["slots"], ledger=story["ledger"], replies=["approve"], run_id="same", moves=moves)
    second = run_until_wait(graph, origin="rule", slots=story["slots"], ledger=story["ledger"], replies=["approve"], run_id="same", moves=moves)
    assert first["move_id"] == second["move_id"]


def test_publish_gate_and_try_it():
    draft = {
        "graph": low_balance_topup_graph(),
        "chat_contract": chat_contract(),
        "stories": stories(),
    }
    assert story_failures(draft["graph"], draft["stories"]) == []
    preview = try_utterance(draft, "Top up my USD with €100 from EUR")
    assert preview["band"] == "start"
    assert preview["confidence"] == 0.97
    assert preview["trace"][-1] == "offer_account"
    assert "€62.40" in preview["messages"][0]["text"]
    assert try_utterance(draft, "send 50 to @dana")["band"] == "not_this"
    assert try_utterance(draft, "refill my GBP")["status"] == "slot"


def test_intent_bands_and_slots():
    contract = chat_contract()
    text = "Top up my USD with €100 from EUR"
    assert score_intent(text, contract) == 0.97
    assert decide_band(0.97, contract["bands"]) == "start"
    assert decide_band(0.6, contract["bands"]) == "ask"
    assert decide_band(0.2, contract["bands"]) == "not_this"
    filled = extract_slots(text)
    assert filled["target"]["value"] == "USD"
    assert filled["amount"]["value"]["display"] == "€100.00"
    assert filled["source"]["value"] == "EUR"
    assert score_intent("send 50 to @dana", contract) < 0.5
    partial = extract_slots("refill my GBP")
    assert partial["target"]["value"] == "GBP"
    assert "amount" not in partial
    assert "source" not in partial
    assert asks_for_accounts("what is available ?")
    assert asks_for_accounts("list my card")
    assert asks_for_accounts("list account")
    assert not asks_for_accounts("top up my account")
    assert not asks_for_accounts("EUR")
    listed = describe_accounts(chat_contract()["preview_ledger"], "what is available ?")
    assert "EUR €62.40" in listed and "CARD €18.40" in listed
    assert describe_accounts(chat_contract()["preview_ledger"], "list my card") == "Your card is CARD €18.40."
    assert greeting_reply("Hi").startswith("Hi.")
    published = [{"workflow_key": "low-balance-top-up", "workflow_id": "low-balance-top-up", "contract": contract, "ledger": contract["preview_ledger"]}]
    waiting = {"phase": "slot", "pending_slot": "target", "workflow_key": "low-balance-top-up", "filled": {}}
    assert fallback_turn("Hi", published, {"phase": "idle"})["action"] == "chat"
    started = fallback_turn("top up my account", published, {"phase": "idle"})
    assert started["action"] == "start" and started["slots"] == {}
    amount = fallback_turn("usd topup 10 $", published, {"phase": "idle"})
    assert amount["slots"]["target"]["value"] == "USD"
    assert amount["slots"]["amount"]["value"]["display"] == "$10.00"
    listed = fallback_turn("what is available ?", published, waiting)
    assert listed["action"] == "answer" and listed["needs"] == "accounts"
    assert "EUR €62.40" in grounded_reply(listed, contract["preview_ledger"], "target")
    cards = fallback_turn("list my card", published, waiting)
    assert cards["needs"] == "cards"
    assert grounded_reply(cards, contract["preview_ledger"], "target").startswith("Your card is CARD €18.40.")
    invented = accept_model(
        {"action": "fill", "workflow_key": "low-balance-top-up", "confidence": 0.9, "slots": {"target": "CARD"}, "needs": "none", "reply": "You have €999"},
        "list my card",
        published,
        waiting,
    )
    assert invented["action"] == "answer" and invented["needs"] == "cards" and invented["slots"] == {}
    assert fallback_turn("send 50 to @dana", published, {"phase": "idle"})["action"] == "chat"
    funded = fallback_turn("what can I use to fund this?", published, waiting)
    assert funded["action"] == "answer" and funded["needs"] == "accounts"
    euros = fallback_turn("from the euro account", published, {"phase": "slot", "pending_slot": "source", "workflow_key": "low-balance-top-up", "filled": {"target": "USD"}})
    assert euros["action"] == "fill" and euros["slots"]["source"]["value"] == "EUR"


def test_a_new_workflow_can_be_run():
    draft = {
        "graph": stub_graph(["chat", "rule"], "Sweep is complete."),
        "chat_contract": {"description": "Sweep", "examples": [], "exclusions": [], "bands": {"start_at": 0.75, "ask_at": 0.5}, "slots": [], "lines": {"done": "Sweep is complete."}, "preview_ledger": {"accounts": {}}},
    }
    result = preview_run(draft, "move what you can", "chat")
    assert result["result"] == "done"
    assert result["trace"] == ["src_chat", "done"]
    assert result["messages"][0]["text"] == "Sweep is complete."
