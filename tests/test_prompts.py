from kai.conversation_graph import run_conversation
import copy

from kai.engine.advance import apply_move, drive, initial_state, reply_port, reply_slots, run_until_wait
from kai.engine.onboarding import bundles
from kai.engine.sample import graph as sample_graph
from kai.activities import interpret_turn, resolve_turn
from kai.conversation_graph import run_conversation
from kai.engine.prompts import build_prompt, customer_ask, match_choice, question_line
from kai.engine.slots import describe_accounts, extract_slots, fallback_turn, idle_line, is_port_reply, missing_slot, pinned_turn, slot_question
from kai.engine.topup import PREVIEW_LEDGER, chat_contract


def test_a_step_label_is_not_what_the_customer_hears():
    assert customer_ask("2 · country of residence") == "What is country of residence?"
    assert customer_ask("7a · popup · what Kai offers you") == "what Kai offers you"
    assert customer_ask("What's your email? Set a password.") == "What's your email? Set a password."


def test_confirm_buttons_come_from_the_step_and_ok_is_not_yes():
    step = {"id": "confirm_request", "kind": "ask", "type": "confirm", "line": "Top up USD.", "config": {"role": "confirm_request"}}
    prompt = build_prompt(step, {})
    assert [choice["port"] for choice in prompt["choices"]] == ["yes", "no"]
    assert match_choice("Yes", prompt) == "yes"
    assert match_choice("ok", prompt) is None
    assert is_port_reply("ok") is None
    assert is_port_reply("sure") is None


def test_open_prompt_answers_other_words_in_the_thread():
    prompt = {
        "text": "Top up USD.",
        "choices": [{"port": "yes", "label": "Yes"}, {"port": "no", "label": "No"}],
        "ports": ["yes", "no", "objection"],
        "step_id": "confirm",
    }

    def understand(payload):
        return {"action": "chat", "reply": "Still waiting.", "slots": {}, "needs": "none", "workflow_key": "", "confidence": 0}

    called = {"n": 0}

    def understand_counted(payload):
        called["n"] += 1
        return understand(payload)

    other = run_conversation({"text": "what does this do?", "phase": "in_run", "prompt": prompt}, understand_counted)
    assert other["action"] == "answer"
    assert other["port"] == ""
    assert other["reply"] == "Top up USD."
    assert other["route"] == "aside"
    assert called["n"] == 0  # mistypes re-ask without Gemini
    chosen = run_conversation({"text": "Yes", "phase": "in_run", "prompt": prompt}, understand)
    assert chosen["action"] == "choice"
    assert chosen["port"] == "yes"
    first = run_conversation({"text": "i don't know", "phase": "in_run", "prompt": prompt, "objection_count": 0, "objection_step": "confirm"}, understand)
    assert first["action"] == "answer"
    assert first["strike"] == 1
    assert "pick one of the options" in first["reply"].lower()
    second = run_conversation({"text": "i don't know", "phase": "in_run", "prompt": prompt, "objection_count": 1, "objection_step": "confirm"}, understand)
    assert second["action"] == "answer"
    assert second["strike"] == 2
    left = run_conversation({"text": "i don't know", "phase": "in_run", "prompt": prompt, "objection_count": 2, "objection_step": "confirm"}, understand)
    assert left["action"] == "choice"
    assert left["port"] == "objection"
    assert left["strike"] == 3
    switched = run_conversation(
        {"text": "i want new account", "phase": "in_run", "prompt": prompt, "workflow_key": "demo-login"},
        lambda payload: {
            "action": "start",
            "workflow_key": "individual-onboarding",
            "confidence": 0.97,
            "slots": {},
            "needs": "none",
            "reply": "",
            "port": "",
        },
    )
    assert switched["action"] == "start"
    assert switched["workflow_key"] == "individual-onboarding"


def test_slot_question_and_idle_line_come_from_the_contract():
    contract = chat_contract()
    assert slot_question("amount", contract["slots"]) == "How much should I move?"
    assert "top up" in idle_line([{"contract": contract}]).lower()


def test_signup_question_owns_its_answers_and_the_response_column():
    graph = bundles()[0]["graph"]
    question = next(step for step in graph["steps"] if step["id"] == "ask_signup_method")
    response = next(step for step in graph["steps"] if step["id"] == "signup_method_given")
    assert question["position"]["x"] == 300
    assert response["position"]["x"] == 576
    assert question["position"]["y"] == response["position"]["y"]
    assert question["position"]["y"] < 400
    columns = {}
    for item in graph["steps"]:
        columns.setdefault(item["position"]["x"], []).append(item["position"]["y"])
    assert all(len(rows) == len(set(rows)) for rows in columns.values())
    assert question["input"]["type"] == "simple_select"
    assert question["input"]["options"][0]["label"] == "Email and password"
    assert question["input"]["options"][1]["port"] == "continue_with_google"
    password = next(step for step in graph["steps"] if step["id"] == "ask_email_password")
    assert password["input"]["fields"][0]["type"] == "email"
    assert password["input"]["fields"][1]["type"] == "password"


def test_a_signup_answer_is_stored_and_the_story_path_still_fits():
    graph = bundles()[0]["graph"]
    state = initial_state(graph, "chat", {})
    assert state["step_id"] == "ask_signup_method"
    step = next(item for item in graph["steps"] if item["id"] == state["step_id"])
    text = "email_and_password"
    state = run_until_wait(
        graph,
        origin="chat",
        slots={},
        replies=[text],
    )
    assert state["slots"]["answers"]["signup_method_given"] == text
    assert state["status"] != "blocked"
    story = bundles()[0]["stories"][0]
    scripted = run_until_wait(graph, origin=story["origin"], slots=story["slots"], ledger=story["ledger"], replies=story["replies"], run_id=story["id"])
    assert len(scripted["trace"]) == story["expected_steps"]
    assert reply_port(step, None, {}) == "no"
    assert reply_slots(step, text, {})["_chosen_port"] == "email_and_password"
    assert question_line(step) == "Let's get you set up. How would you like to sign up?"
    assert any(message["text"] == question_line(step) for message in state["messages"])


def test_a_pinned_chat_starts_that_workflow():
    contracts = [{"workflow_id": "individual-onboarding", "workflow_key": "individual-onboarding", "contract": {}, "ledger": {"accounts": {}}}]
    state = {"phase": "idle", "opened_workflow_key": "individual-onboarding", "pending_slot": ""}
    assert pinned_turn("Hi", contracts, state)["workflow_key"] == "individual-onboarding"
    assert pinned_turn("Hi", contracts, {"phase": "idle"}) is None
    assert fallback_turn("Hi", contracts, {"phase": "idle"})["action"] == "chat"
    started = resolve_turn({"text": "Hi", "phase": "idle", "pinned_workflow_id": "individual-onboarding", "contracts": contracts})
    assert started["action"] == "start"
    assert started["workflow_key"] == "individual-onboarding"
    opened = interpret_turn({"text": "Hi", "phase": "idle", "pinned_workflow_id": "individual-onboarding", "contracts": contracts, "prompt": {}})
    assert opened["action"] == "start"
    assert opened["workflow_key"] == "individual-onboarding"


def test_an_option_edge_asks_the_next_question():
    graph = {
        "budget": 10,
        "entries": {"chat": "ask"},
        "steps": [
            {
                "id": "ask",
                "kind": "question",
                "type": "",
                "line": "How you sign up",
                "input": {
                    "ask": "How would you like to sign up?",
                    "type": "simple_select",
                    "options": [
                        {"label": "Email and password", "port": "email_and_password"},
                        {"label": "Apple", "port": "apple"},
                    ],
                },
            },
            {"id": "email", "kind": "user_response", "type": ""},
            {"id": "apple_step", "kind": "result", "type": "", "line": "Use Apple.", "result": "apple"},
            {"id": "done", "kind": "result", "type": "", "line": "Use email.", "result": "email"},
        ],
        "edges": [
            {"id": "a", "from": "ask", "to": "email", "port": "continue"},
            {"id": "b", "from": "ask", "to": "email", "port": "email_and_password"},
            {"id": "c", "from": "ask", "to": "apple_step", "port": "apple"},
            {"id": "d", "from": "email", "to": "done", "port": "continue"},
        ],
    }
    prompt = build_prompt(graph["steps"][0], {})
    assert [choice["label"] for choice in prompt["choices"]] == ["Email and password", "Apple"]
    state = run_until_wait(graph, origin="chat", replies=["Apple"])
    assert state["result"] == "apple"
    assert state["messages"][0]["text"] == "How would you like to sign up?"


def test_a_greeting_does_not_count_as_the_answer():
    prompt = {"text": "The code we emailed", "fields": [{"id": "answer", "ask": "The code we emailed"}], "choices": [], "skip": False}
    stayed = run_conversation({"text": "hi", "phase": "in_run", "prompt": prompt}, lambda payload: {"action": "chat", "reply": "Hello", "port": ""})
    assert stayed["action"] == "answer"
    assert stayed["reply"] == "The code we emailed"
    balance = run_conversation({"text": "fetch balance", "phase": "in_run", "prompt": prompt}, lambda payload: {"action": "chat", "reply": "You have €1", "port": ""})
    assert balance["action"] == "answer"
    assert balance["reply"] == "The code we emailed"
    skipped = run_conversation({"text": "skip", "phase": "in_run", "prompt": prompt}, lambda payload: {"action": "chat", "reply": "", "port": ""})
    assert skipped["action"] == "answer"
    allowed = run_conversation({"text": "skip", "phase": "in_run", "prompt": {**prompt, "skip": True}}, lambda payload: {"action": "chat", "reply": "", "port": ""})
    assert allowed["action"] == "choice"
    assert allowed["port"] == "continue"
    assert match_choice("Continue with Satya AI", {"choices": [{"port": "continue_with_satya_ai", "label": "Continue with Satya AI"}]}) == "continue_with_satya_ai"


def test_the_chat_source_says_its_line_before_the_first_question():
    graph = copy.deepcopy(sample_graph())
    graph["steps"][0]["line"] = "This is the sample."
    state = run_until_wait(graph, origin="chat", replies=[])
    assert [message["text"] for message in state["messages"]][:2] == ["This is the sample.", "What should this sample do?"]
    assert state["step_id"] == "ask_what"


def test_the_sample_follows_the_answer_you_pick():
    graph = sample_graph()
    balance = run_until_wait(graph, origin="chat", replies=["Check a balance"])
    assert balance["result"] == "balance"
    hello = run_until_wait(graph, origin="chat", replies=["Say hello"])
    assert hello["result"] == "hello"
    moved = run_until_wait(graph, origin="chat", replies=["Move money", "25"])
    assert moved["result"] == "recorded"
    assert moved["step_id"] == "move_done"
    assert "ask_amount" in moved["trace"]
    assert "balance_done" not in moved["trace"]


def test_a_bare_amount_is_taken_from_the_source_account():
    ledger = {"rate": 1.08, "accounts": {"CHF": {"amount": 18, "currency": "CHF"}, "EUR": {"amount": 62.4, "currency": "EUR"}}}
    contracts = [{"workflow_key": "sweep-between-accounts", "contract": {"preview_ledger": ledger}, "ledger": ledger}]
    state = {"phase": "slot", "pending_slot": "amount", "workflow_key": "sweep-between-accounts", "filled": {"source": "CHF", "target": "EUR"}}
    decision = fallback_turn("18", contracts, state)
    assert decision["slots"]["amount"]["value"]["display"] == "18.00 CHF"
    found = extract_slots("CHF move to EUR")
    assert found["source"]["value"] == "CHF"
    assert found["target"]["value"] == "EUR"


def test_a_completed_move_changes_what_fetch_balance_says():
    original = describe_accounts(PREVIEW_LEDGER, "fetch balance")
    assert "CHF 18.00 CHF" not in original
    assert "18.00 CHF" in original
    ledger = copy.deepcopy(PREVIEW_LEDGER)
    apply_move(
        ledger,
        {
            "source": "CHF",
            "target": "EUR",
            "amount": {"amount": 18, "currency": "USD", "display": "$18.00"},
            "est": {"amount": 19.44, "currency": "EUR", "display": "€19.44"},
        },
    )
    assert PREVIEW_LEDGER["accounts"]["CHF"]["amount"] == 18
    assert ledger["accounts"]["CHF"]["amount"] == 0
    assert ledger["accounts"]["EUR"]["amount"] == 81.84
    listed = describe_accounts(ledger, "fetch balance")
    assert "0.00 CHF" in listed
    assert "€81.84" in listed


def test_all_means_the_whole_source_balance():
    ledger = {"accounts": {"CHF": {"amount": 18, "currency": "CHF"}}}
    contracts = [{"workflow_key": "sweep-between-accounts", "contract": {"preview_ledger": ledger}, "ledger": ledger}]
    state = {"phase": "slot", "pending_slot": "amount", "workflow_key": "sweep-between-accounts", "filled": {"source": "CHF", "target": "EUR"}}
    decision = fallback_turn("all", contracts, state)
    assert decision["action"] == "fill"
    assert decision["slots"]["amount"]["value"]["amount"] == 18
    assert decision["slots"]["amount"]["value"]["currency"] == "CHF"


def test_the_arrow_on_an_option_is_the_next_step():
    graph = copy.deepcopy(bundles()[0]["graph"])
    question = next(step for step in graph["steps"] if step["id"] == "ask_signup_method")
    question["input"]["options"].append({"label": "continue with satya ai", "port": "continue_with_satya_ai"})
    graph["edges"].append({"id": "satya", "from": "ask_signup_method", "to": "ask_residence", "port": "continue_with_satya_ai"})
    state = run_until_wait(graph, origin="chat", replies=["continue with satya ai"])
    assert state["step_id"] == "ask_residence"
    assert "offer_passkey" not in state["trace"]
    assert state["messages"][-1]["text"] == "What is country of residence?"


def test_the_signup_answer_chooses_the_questions_that_follow():
    graph = bundles()[0]["graph"]
    email = run_until_wait(graph, origin="chat", replies=["Email and password"])
    assert email["step_id"] == "ask_email_password"
    assert email["messages"][-1]["text"] == "What's your email? Set a password."
    assert "google_sign_in" not in email["trace"]
    google = run_until_wait(graph, origin="chat", replies=["Continue with Google"])
    assert google["step_id"] == "offer_passkey"
    assert "ask_email_password" not in google["trace"]
    assert "What is face ID or a passkey?" in [message["text"] for message in google["messages"]]
    code = run_until_wait(graph, origin="chat", replies=["Email and password", "ada@example.com\nsecret"])
    assert code["step_id"] == "ask_email_code"
    assert code["messages"][-1]["text"] == "What is the code we emailed?"


def test_a_balance_step_with_nowhere_to_go_says_the_balance_and_finishes():
    graph = {
        "budget": 10,
        "entries": {"chat": "src_chat"},
        "steps": [
            {"id": "src_chat", "kind": "source", "type": "chat_request", "line": None},
            {"id": "get_balance-2", "kind": "fetch", "type": "get_balance", "line": None, "config": {"role": "get_balance"}, "input": {"account": "<account>"}},
        ],
        "edges": [{"id": "e", "from": "src_chat", "to": "get_balance-2", "port": "continue"}],
    }
    state = run_until_wait(graph, origin="chat", ledger=PREVIEW_LEDGER, replies=[])
    assert state["status"] == "completed"
    assert state["result"] == "done"
    assert state["step_id"] == "get_balance-2"
    assert "€62.40" in state["messages"][-1]["text"]
    assert "missing_edge" != state["result"]


def test_a_step_that_still_has_another_arrow_blocks_when_this_port_is_open():
    graph = {
        "budget": 5,
        "entries": {"chat": "get_balance"},
        "steps": [
            {"id": "get_balance", "kind": "fetch", "type": "get_balance", "line": "Held."},
            {"id": "done", "kind": "result", "line": "Done.", "result": "done"},
        ],
        "edges": [{"id": "e", "from": "get_balance", "to": "done", "port": "other"}],
    }
    state = {
        "step_id": "get_balance",
        "status": "active",
        "slots": {},
        "trace": ["get_balance"],
        "messages": [],
        "events": [],
        "result": None,
        "pending": "fetch",
        "entered": "get_balance",
        "spoke": False,
        "move_id": None,
    }
    done = drive(state, graph, {"step_id": "get_balance", "port": "continue", "data": {}})
    assert done["result"] == "missing_edge"
    assert done["status"] == "blocked"


def test_a_slot_without_a_fill_rule_does_not_block_the_workflow():
    slots = [{"key": "H", "order": 1, "ask": "asdasds", "fill_from": ""}, {"key": "amount", "order": 2, "ask": "How much?", "fill_from": "the amount"}]
    assert missing_slot(slots, {})["key"] == "amount"
    assert missing_slot([{"key": "H", "order": 1, "ask": "asdasds", "fill_from": ""}], {}) is None
