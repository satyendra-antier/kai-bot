from kai.engine.advance import run_until_wait
from kai.engine.catalog import stub_graph
from kai.service import preview_run, story_failures, try_utterance
from kai.engine.registry import KINDS, STEP_TYPES
from kai.engine.slots import accept_model, asks_for_accounts, decide_band, describe_accounts, extract_slots, fallback_turn, greeting_reply, grounded_reply, score_intent
from kai.engine.topup import PREVIEW_LEDGER, chat_contract, low_balance_topup_graph, stories


def test_registry_counts():
    assert len(KINDS) == 9
    assert len(STEP_TYPES) == 27
    assert len({item["kind"] for item in STEP_TYPES}) == 9
    by_type = {item["type"]: item for item in STEP_TYPES}
    assert by_type["ask_options"]["input"]["type"] == "simple_select"
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
    excluded = try_utterance(draft, "send 50 to @dana")
    assert excluded["band"] == "not_this"
    assert excluded["status"] == "idle"
    assert excluded["messages"] and "top up" in excluded["messages"][0]["text"].lower()
    ask_draft = {
        "graph": low_balance_topup_graph(),
        "chat_contract": {
            **chat_contract(),
            "examples": ["refill my account please"],
            "exclusions": [],
            "bands": {"start_at": 0.95, "ask_at": 0.5, "not_this_below": 0.5},
        },
    }
    unsure = try_utterance(ask_draft, "refill my GBP")
    assert unsure["band"] == "ask"
    assert unsure["messages"] and "is that what you want" in unsure["messages"][0]["text"].lower()
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
    assert not asks_for_accounts("tell me when my account balannce goes below 50")
    alert_line = "tell me when my account balannce goes below 50"
    from kai.engine.slots import sync_try_prompts, wants_alert, looks_like_question
    from kai.engine.catalog import bundles as catalog_bundles

    assert wants_alert(alert_line)
    assert not looks_like_question(alert_line)
    for pack in catalog_bundles():
        synced = sync_try_prompts(dict(pack["chat_contract"] or {}))
        prompts = synced.get("try_prompts") or []
        for sample in (synced.get("examples") or []) + (synced.get("exclusions") or []):
            assert sample in prompts
    packs = {item["key"]: item for item in catalog_bundles()}
    alert_pack = packs["low-balance-alert"]
    sweep_pack = packs["sweep-between-accounts"]
    assert score_intent(alert_line, alert_pack["chat_contract"]) > score_intent(alert_line, contract)
    assert score_intent(alert_line, contract) <= 0.35
    move_line = "Move €50 from EUR to USDT"
    assert score_intent(move_line, sweep_pack["chat_contract"]) > score_intent(move_line, contract)
    alert_contracts = [
        {
            "workflow_key": "low-balance-alert",
            "workflow_id": "low-balance-alert",
            "contract": alert_pack["chat_contract"],
            "ledger": alert_pack["chat_contract"]["preview_ledger"],
        }
    ]
    alerted = fallback_turn(alert_line, alert_contracts, {"phase": "idle"})
    assert alerted["action"] == "start"
    assert alerted["workflow_key"] == "low-balance-alert"
    assert alerted["needs"] == "none"
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
    fx_pack = packs["fx-rate-watch"]
    fx_contracts = [
        {
            "workflow_key": "fx-rate-watch",
            "workflow_id": "fx-rate-watch",
            "contract": fx_pack["chat_contract"],
            "ledger": fx_pack["chat_contract"]["preview_ledger"],
        }
    ]
    fx_full = extract_slots("Tell me when EUR/USD goes above 1.10")
    assert fx_full["pair"]["value"] == "EUR/USD"
    assert fx_full["target_rate"]["value"] == 1.1
    assert "target" not in fx_full and "amount" not in fx_full
    fx_start = fallback_turn("Tell me when EUR/USD goes above 1.10", fx_contracts, {"phase": "idle"})
    assert fx_start["action"] == "start"
    assert fx_start["slots"]["pair"]["value"] == "EUR/USD"
    assert fx_start["slots"]["target_rate"]["value"] == 1.1
    fx_pair = fallback_turn(
        "EUR/USD",
        fx_contracts,
        {"phase": "slot", "pending_slot": "pair", "workflow_key": "fx-rate-watch", "filled": {}},
    )
    assert fx_pair["action"] == "fill" and fx_pair["slots"]["pair"]["value"] == "EUR/USD"
    assert "target" not in fx_pair["slots"]
    fx_rate = fallback_turn(
        "1.10",
        fx_contracts,
        {"phase": "slot", "pending_slot": "target_rate", "workflow_key": "fx-rate-watch", "filled": {"pair": "EUR/USD"}},
    )
    assert fx_rate["slots"]["target_rate"]["value"] == 1.1
    alert_slots = extract_slots("Let me know if my GBP account goes under £50")
    assert alert_slots["account"]["value"] == "GBP"
    assert alert_slots["threshold"]["value"] == 50.0
    card_alert = extract_slots("Tell me when my card drops below €20")
    assert card_alert["account"]["value"] == "CARD"
    assert card_alert["threshold"]["value"] == 20.0
    threshold_only = fallback_turn(
        "£50",
        alert_contracts,
        {"phase": "slot", "pending_slot": "threshold", "workflow_key": "low-balance-alert", "filled": {"account": "GBP"}},
    )
    assert threshold_only["action"] == "fill" and threshold_only["slots"]["threshold"]["value"] == 50.0
    assert fallback_turn("asdfghjkl", list(alert_contracts) + fx_contracts + published, {"phase": "idle"})["action"] == "chat"
    assert decide_band(score_intent("tell me when", alert_pack["chat_contract"]), alert_pack["chat_contract"]["bands"]) == "ask"
    assert decide_band(score_intent("transfer please", sweep_pack["chat_contract"]), sweep_pack["chat_contract"]["bands"]) != "start"


def test_natural_alerts_prefer_product_over_demo_alert():
    from kai.engine.catalog import bundles as catalog_bundles
    from kai.engine.demos import bundles as demo_bundles

    packs = {item["key"]: item for item in catalog_bundles()}
    demos = {item["key"]: item for item in demo_bundles()}
    alert_pack = packs["low-balance-alert"]
    demo_alert = demos["demo-alert"]
    contracts = [
        {
            "workflow_key": "low-balance-alert",
            "workflow_id": "low-balance-alert",
            "contract": alert_pack["chat_contract"],
            "ledger": alert_pack["chat_contract"]["preview_ledger"],
        },
        {
            "workflow_key": "demo-alert",
            "workflow_id": "demo-alert",
            "contract": demo_alert["chat_contract"],
            "ledger": demo_alert["chat_contract"]["preview_ledger"],
        },
    ]
    under = "tell me when my account goes under 50 EUR"
    above = "tell me when it goes up to 65"
    assert score_intent(under, alert_pack["chat_contract"], workflow_key="low-balance-alert") > score_intent(
        under, demo_alert["chat_contract"], workflow_key="demo-alert"
    )
    assert score_intent(above, demo_alert["chat_contract"], workflow_key="demo-alert") < 0.5
    started = fallback_turn(under, contracts, {"phase": "idle"})
    assert started["action"] == "start"
    assert started["workflow_key"] == "low-balance-alert"
    assert started["slots"]["account"]["value"] == "EUR"
    assert started["slots"]["threshold"]["value"] == 50.0
    follow = fallback_turn(above, contracts, {"phase": "idle"})
    assert follow["action"] == "start"
    assert follow["workflow_key"] == "low-balance-alert"
    assert follow["slots"]["threshold"]["value"] == 65.0
    assert extract_slots(above)["direction"]["value"] == "above"
    demo_only = fallback_turn("demo alert", contracts, {"phase": "idle"})
    assert demo_only["action"] == "start"
    assert demo_only["workflow_key"] == "demo-alert"


def test_a_new_workflow_can_be_run():
    draft = {
        "graph": stub_graph(["chat", "rule"], "Sweep is complete."),
        "chat_contract": {"description": "Sweep", "examples": [], "exclusions": [], "bands": {"start_at": 0.75, "ask_at": 0.5}, "slots": [], "lines": {"done": "Sweep is complete."}, "preview_ledger": {"accounts": {}}},
    }
    result = preview_run(draft, "move what you can", "chat")
    assert result["result"] == "done"
    assert result["trace"] == ["src_chat", "done"]
    assert result["messages"][0]["text"] == "Sweep is complete."
