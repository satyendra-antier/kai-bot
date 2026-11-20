import copy

from kai.engine.advance import _condition_port, run_until_wait
from kai.engine.demos import bundles
from kai.engine.flow import explain_run
from kai.engine.slots import decide_band, extract_slots, score_intent
from kai.engine.topup import PREVIEW_LEDGER
from kai.service import story_failures


def test_registry_condition_slugs_branch():
    below = {"id": "is_low", "type": "compare", "config": {"role": "compare"}, "input": {"level": 50, "direction": "below"}}
    assert _condition_port(below, {"target_balance": {"amount": 12}}) == "true"
    assert _condition_port(below, {"target_balance": {"amount": 62.4}}) == "false"
    above = {"id": "is_high", "type": "compare", "config": {"role": "compare"}, "input": {"level": 50, "direction": "above"}}
    assert _condition_port(above, {"target_balance": {"amount": 62.4}}) == "true"
    assert _condition_port(above, {"target_balance": {"amount": 12}}) == "false"
    enough = {"id": "is_enough", "type": "enough", "config": {"role": "enough"}, "input": {"amount": 0}}
    assert _condition_port(enough, {"source_balance": {"amount": 18}, "amount": {"amount": 10}}) == "true"
    assert _condition_port(enough, {"target_balance": {"amount": 12}, "amount": {"amount": 50}}) == "false"
    origin = {"id": "from_chat", "type": "origin", "config": {"role": "origin"}}
    assert _condition_port(origin, {"origin": "chat"}) == "true"
    assert _condition_port(origin, {"origin": "rule"}) == "false"
    first = {"id": "first_time", "type": "first_time", "config": {"role": "first_time"}}
    assert _condition_port(first, {}) == "true"
    assert _condition_port(first, {"already_alerted": True}) == "false"
    credentials = {"id": "credentials", "type": "compare", "config": {"role": "credentials"}, "input": {"email": "demo@kai.example", "password": "demo-pass"}}
    assert _condition_port(credentials, {"email": "demo@kai.example", "password": "demo-pass"}) == "true"
    assert _condition_port(credentials, {"email": "demo@kai.example", "password": "nope"}) == "false"


def test_every_demo_story_reaches_its_result():
    for package in bundles():
        assert story_failures(package["graph"], package["stories"]) == [], package["key"]
        for story in package["stories"]:
            state = run_until_wait(
                package["graph"],
                origin=story["origin"],
                slots=story["slots"],
                ledger=story["ledger"],
                replies=story["replies"],
                run_id=story["id"],
            )
            assert state["result"] == story["expected_result"], story["name"]
            assert len(state["trace"]) == story["expected_steps"], (story["name"], state["trace"])
            assert state["messages"], story["name"]
            assert all(message.get("text") for message in state["messages"])


def test_retargeting_the_low_edge_is_followed():
    package = next(item for item in bundles() if item["key"] == "demo-balance")
    graph = copy.deepcopy(package["graph"])
    graph["steps"].append(
        {
            "id": "custom_low",
            "kind": "result",
            "type": "not_done",
            "title": "Custom low",
            "purpose": "the edge was moved here",
            "line": "This is the new low result.",
            "position": {"x": 576, "y": 400},
            "speed_target_ms": 2000,
            "config": {},
            "result": "custom_low",
        }
    )
    for edge in graph["edges"]:
        if edge["from"] == "is_low" and edge["port"] == "true":
            edge["to"] = "custom_low"
    story = next(item for item in package["stories"] if item["id"] == "demo_balance_usd")
    state = run_until_wait(graph, origin="chat", slots=story["slots"], ledger=story["ledger"], replies=[])
    assert state["step_id"] == "custom_low"
    assert state["result"] == "custom_low"
    assert state["trace"][-1] == "custom_low"
    fine = next(item for item in package["stories"] if item["id"] == "demo_balance_eur")
    stayed = run_until_wait(graph, origin="chat", slots=fine["slots"], ledger=fine["ledger"], replies=[])
    assert stayed["result"] == "still_fine"
    assert "custom_low" not in stayed["trace"]


def test_each_example_starts_only_its_own_workflow():
    packages = bundles()
    for package in packages:
        bands = package["chat_contract"]["bands"]
        for example in package["chat_contract"]["examples"]:
            own = score_intent(example, package["chat_contract"], workflow_key=package["key"])
            assert decide_band(own, bands) == "start", example
            for other in packages:
                if other["key"] == package["key"]:
                    continue
                score = score_intent(example, other["chat_contract"], workflow_key=other["key"])
                assert decide_band(score, other["chat_contract"]["bands"]) != "start", (example, other["key"], score)


def test_an_empty_source_stays_silent_and_the_result_is_spoken():
    package = next(item for item in bundles() if item["key"] == "demo-balance")
    story = package["stories"][0]
    state = run_until_wait(
        package["graph"],
        origin="chat",
        slots=story["slots"],
        ledger=story["ledger"],
        replies=[],
    )
    assert all(message["step_id"] != "chat_request" for message in state["messages"])
    assert state["messages"][-1]["text"] == "That balance is still fine."
    assert state["messages"][-1]["step_id"] == "still_fine"


def test_a_run_explains_each_step_by_its_registry_type():
    balance = next(item for item in bundles() if item["key"] == "demo-balance")
    story = next(item for item in balance["stories"] if item["id"] == "demo_balance_usd")
    state = run_until_wait(balance["graph"], origin="chat", slots=story["slots"], ledger=story["ledger"], replies=[])
    flow = explain_run(balance["graph"], state["trace"], state["slots"], state["messages"], [], state["events"])
    low = next(item for item in flow if item["id"] == "is_low")
    assert low["kind"] == "condition"
    assert low["type"] == "compare"
    assert low["type_label"] == "Compare to a level"
    assert low["port"] == "true"
    assert "below 50" in low["detail"]
    fetched = next(item for item in flow if item["id"] == "get_balance")
    assert fetched["kind"] == "fetch"
    assert "USD" in fetched["detail"]
    choose = next(item for item in bundles() if item["key"] == "demo-choose")
    picked = next(item for item in choose["stories"] if item["id"] == "demo_choose_balance")
    walked = run_until_wait(choose["graph"], origin="chat", slots={}, ledger=picked["ledger"], replies=picked["replies"])
    asked = next(item for item in explain_run(choose["graph"], walked["trace"], walked["slots"], walked["messages"], picked["replies"], walked["events"]) if item["id"] == "ask_next")
    assert "Check a balance" in asked["detail"]
    assert "check_balance" in asked["detail"]


def test_named_accounts_fill_without_stealing_a_move():
    assert extract_slots("check EUR")["target"]["value"] == "EUR"
    assert extract_slots("check USD")["target"]["value"] == "USD"
    moved = extract_slots("move 10 from CHF to EUR")
    assert moved["source"]["value"] == "CHF"
    assert moved["target"]["value"] == "EUR"
    assert moved["amount"]["value"]["amount"] == 10
    assert moved["amount"]["value"]["currency"] == "CHF"


def test_demo_login_objection_leaves_without_signing_in():
    package = next(item for item in bundles() if item["key"] == "demo-login")
    one = run_until_wait(package["graph"], origin="chat", slots={}, ledger=package["stories"][0]["ledger"], replies=["i don't know"])
    assert one["status"] == "waiting"
    assert one["step_id"] == "ask_method"
    assert one["slots"].get("_objection_count") == 1
    state = run_until_wait(
        package["graph"],
        origin="chat",
        slots={},
        ledger=package["stories"][0]["ledger"],
        replies=["i don't know", "i don't know", "i don't know"],
    )
    assert state["result"] == "not_signed_in"
    assert "cancelled" in state["trace"]
    assert "signed_in" not in state["trace"]


def test_demo_list_uses_predefined_options_and_three_strikes():
    package = next(item for item in bundles() if item["key"] == "demo-list")
    residence = next(step for step in package["graph"]["steps"] if step["id"] == "ask_residence")
    assert residence["input"]["type"] == "simple_select"
    assert any(option["label"] == "Germany" for option in residence["input"]["options"])
    funds = next(step for step in package["graph"]["steps"] if step["id"] == "ask_funds")
    assert any(option["label"] == "Salary" for option in funds["input"]["options"])
    ok = run_until_wait(package["graph"], origin="chat", slots={}, ledger=PREVIEW_LEDGER, replies=["Germany", "Salary"])
    assert ok["result"] == "listed"
    assert ok["slots"]["residence"] == "Germany"
    assert ok["slots"]["source_of_funds"] == "Salary"
    stopped = run_until_wait(
        package["graph"],
        origin="chat",
        slots={},
        ledger=PREVIEW_LEDGER,
        replies=["i don't know", "i don't know", "i don't know"],
    )
    assert stopped["result"] == "stopped"
