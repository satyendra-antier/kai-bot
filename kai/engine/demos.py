"""Six short workflows for a live demo. They do not replace the seeded seven."""

from __future__ import annotations

from kai.engine.graphlib import chain, finish_graph, layout
from kai.engine.topup import PREVIEW_LEDGER, eval_config, money


def _graph(rows: list[tuple], pairs: list[tuple[str, str, str]], entries: dict[str, str]) -> dict:
    steps = layout(rows)
    edges = chain(pairs)
    finish_graph(steps, edges, entries)
    return {"budget": 40, "entries": entries, "steps": steps, "edges": edges}


def _input(graph: dict, step_id: str, payload: dict) -> None:
    for step in graph["steps"]:
        if step["id"] == step_id:
            step["input"] = payload
            return


def balance_graph() -> dict:
    graph = _graph(
        [
            ("chat_request", "source", "chat_request", "On demand from chat"),
            ("start", "trigger", "start", "the customer asked"),
            ("get_balance", "fetch", "get_balance", "EUR unless they name one", "The balance is {target_balance}.", "get_balance"),
            ("is_low", "condition", "compare", "below 50", None, "compare"),
            ("still_fine", "result", "done", "still fine", "That balance is still fine.", None, "still_fine"),
            ("low", "result", "not_done", "under 50", "That balance is under 50.", None, "low"),
        ],
        [
            ("chat_request", "start", "continue"),
            ("start", "get_balance", "continue"),
            ("get_balance", "is_low", "continue"),
            ("is_low", "still_fine", "false"),
            ("is_low", "low", "true"),
        ],
        {"chat": "chat_request"},
    )
    _input(graph, "get_balance", {"account": "EUR"})
    _input(graph, "is_low", {"value": 0, "level": 50, "direction": "below"})
    return graph


def alert_graph() -> dict:
    graph = _graph(
        [
            ("chat_request", "source", "chat_request", "On demand from chat"),
            ("balance_rule", "source", "rule", "Triggered by rule"),
            ("start", "trigger", "start", "the customer asked, or a rule fired"),
            ("get_balance", "fetch", "get_balance", "the account they named", "The balance is {target_balance}.", "get_balance"),
            ("is_low", "condition", "compare", "below 50", None, "compare"),
            ("from_chat", "condition", "origin", "chat sets it up, a rule just tells them", None, "origin"),
            ("confirm", "ask", "confirm", "read back — nothing saved without a yes", "Tell you when {account} goes under 50?", "confirm"),
            ("saved", "result", "done", "the rule is saved", "Saved. I'll tell you if it drops under 50.", None, "alert_saved"),
            ("declined", "result", "not_done", "they said no", "Okay, I won't save that.", None, "declined"),
            ("still_fine", "result", "nothing", "nothing to say", "It's still above that level.", None, "still_fine"),
            ("alerted", "result", "done", "the rule told them", "{account} is under 50.", None, "alerted"),
        ],
        [
            ("chat_request", "start", "continue"),
            ("balance_rule", "start", "continue"),
            ("start", "get_balance", "continue"),
            ("get_balance", "is_low", "continue"),
            ("is_low", "still_fine", "false"),
            ("is_low", "from_chat", "true"),
            ("from_chat", "confirm", "true"),
            ("from_chat", "alerted", "false"),
            ("confirm", "saved", "yes"),
            ("confirm", "declined", "no"),
        ],
        {"chat": "chat_request", "rule": "balance_rule"},
    )
    _input(graph, "get_balance", {"account": "EUR"})
    _input(graph, "is_low", {"value": 0, "level": 50, "direction": "below"})
    return graph


def move_graph() -> dict:
    graph = _graph(
        [
            ("chat_request", "source", "chat_request", "On demand from chat"),
            ("get_balance", "fetch", "get_balance", "the account it comes from", "{source} has {source_balance}.", "get_source_balance"),
            ("is_enough", "condition", "enough", "spare covers the amount", None, "enough"),
            ("confirm", "ask", "confirm", "read back — nothing moves without a yes", "Move {amount} from {source} to {target}. Nothing starts until you say yes.", "confirm"),
            ("start", "trigger", "start", "they said yes"),
            ("move_money", "action", "move_money", "debit one balance, credit the other", None, "move_money"),
            ("moved", "result", "done", "the move is done", "Moved. {target} has the money.", None, "moved"),
            ("not_enough", "result", "not_done", "not enough in that account", "There isn't enough in that account.", None, "not_enough"),
            ("declined", "result", "not_done", "the customer said no", "Okay, I won't move anything.", None, "declined"),
        ],
        [
            ("chat_request", "get_balance", "continue"),
            ("get_balance", "is_enough", "continue"),
            ("is_enough", "confirm", "true"),
            ("is_enough", "not_enough", "false"),
            ("confirm", "start", "yes"),
            ("confirm", "declined", "no"),
            ("start", "move_money", "continue"),
            ("move_money", "moved", "continue"),
        ],
        {"chat": "chat_request"},
    )
    _input(graph, "is_enough", {"available": 0, "amount": 0})
    return graph


def _contract(
    description: str,
    examples: list[str],
    slots: list[dict] | None = None,
    exclusions: list[str] | None = None,
) -> dict:
    from kai.engine.slots import sync_try_prompts

    return sync_try_prompts(
        {
            "description": description,
            "examples": examples,
            "exclusions": list(exclusions or []),
            "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
            "slots": slots or [],
            "lines": {"idle": "", "ask": "", "not_this": "", "source": ""},
            "never": [
                {
                    "id": "no_invent_balance",
                    "mode": "judged",
                    "text": "Never invent a balance Kai did not read",
                    "forbidden": ["you have €999", "you have $999"],
                },
                {
                    "id": "no_external",
                    "mode": "judged",
                    "text": "Never send to an external handle",
                    "forbidden": ["sent to @", "sending to @"],
                },
            ],
            "try_prompts": [],
            "preview_ledger": PREVIEW_LEDGER,
        }
    )


def balance_stories() -> list[dict]:
    return [
        {
            "id": "demo_balance_eur",
            "name": "EUR is still fine",
            "blurb": "€62.40 is not under 50.",
            "origin": "chat",
            "slots": {"target": "EUR"},
            "ledger": PREVIEW_LEDGER,
            "replies": [],
            "expected_steps": 5,
            "expected_result": "still_fine",
        },
        {
            "id": "demo_balance_usd",
            "name": "USD is low",
            "blurb": "$12 is under 50.",
            "origin": "chat",
            "slots": {"target": "USD"},
            "ledger": PREVIEW_LEDGER,
            "replies": [],
            "expected_steps": 5,
            "expected_result": "low",
        },
    ]


def alert_stories() -> list[dict]:
    return [
        {
            "id": "demo_alert_usd",
            "name": "Save an alert for USD",
            "blurb": "$12 is under 50, and they say yes.",
            "origin": "chat",
            "slots": {"target": "USD"},
            "ledger": PREVIEW_LEDGER,
            "replies": ["yes"],
            "expected_steps": 7,
            "expected_result": "alert_saved",
        },
        {
            "id": "demo_alert_gbp",
            "name": "GBP stays quiet",
            "blurb": "£210 is above 50, so nothing is saved.",
            "origin": "rule",
            "slots": {"target": "GBP"},
            "ledger": PREVIEW_LEDGER,
            "replies": [],
            "expected_steps": 5,
            "expected_result": "still_fine",
        },
        {
            "id": "demo_alert_rule_usd",
            "name": "A rule fires for USD",
            "blurb": "The rule tells them. It does not ask.",
            "origin": "rule",
            "slots": {"target": "USD"},
            "ledger": PREVIEW_LEDGER,
            "replies": [],
            "expected_steps": 6,
            "expected_result": "alerted",
        },
    ]


DEMO_EMAIL = "demo@kai.example"
DEMO_PASSWORD = "demo-pass"


def login_graph() -> dict:
    graph = _graph(
        [
            ("chat_request", "source", "chat_request", "On demand from chat"),
            (
                "ask_method",
                "question",
                "",
                "passkey, or email and password",
                "How do you want to sign in? The demo account is demo@kai.example, password demo-pass, and it already has a passkey.",
            ),
            (
                "confirm_passkey",
                "question",
                "",
                "the passkey already on the demo account",
                "Use the passkey saved for demo@kai.example?",
            ),
            ("ask_email", "question", "", "the email on the demo account", "What's the email?"),
            ("ask_password", "question", "", "the password on the demo account", "What's the password?"),
            ("credentials", "condition", "", "email and password match the demo account", None, "credentials"),
            ("signed_in", "result", "done", "this chat is signed in", "You're signed in as the demo account. You can check a balance, pick an option, or move money in this chat.", None, "account_opened"),
            ("wrong", "result", "not_done", "the email or password did not match", "That email or password doesn't match the demo account.", None, "denied"),
            ("cancelled", "result", "not_done", "they did not use the passkey", "Okay, you're not signed in.", None, "not_signed_in"),
        ],
        [
            ("chat_request", "ask_method", "continue"),
            ("ask_method", "confirm_passkey", "passkey"),
            ("ask_method", "ask_email", "email"),
            ("ask_method", "cancelled", "objection"),
            ("confirm_passkey", "signed_in", "use_passkey"),
            ("confirm_passkey", "cancelled", "not_now"),
            ("confirm_passkey", "cancelled", "objection"),
            ("ask_email", "ask_password", "continue"),
            ("ask_email", "cancelled", "objection"),
            ("ask_password", "credentials", "continue"),
            ("ask_password", "cancelled", "objection"),
            ("credentials", "signed_in", "true"),
            ("credentials", "wrong", "false"),
        ],
        {"chat": "chat_request"},
    )
    _input(
        graph,
        "ask_method",
        {
            "ask": "How do you want to sign in? The demo account is demo@kai.example, password demo-pass, and it already has a passkey.",
            "type": "simple_select",
            "options": [
                {"label": "Use passkey", "port": "passkey"},
                {"label": "Email and password", "port": "email"},
            ],
        },
    )
    _input(
        graph,
        "confirm_passkey",
        {
            "ask": "Use the passkey saved for demo@kai.example?",
            "type": "simple_select",
            "options": [
                {"label": "Use passkey", "port": "use_passkey"},
                {"label": "Not this account", "port": "not_now"},
            ],
        },
    )
    _input(graph, "ask_email", {"ask": "What's the email?", "type": "free_text", "fields": [{"id": "email", "ask": "What's the email?", "type": "email"}]})
    _input(graph, "ask_password", {"ask": "What's the password?", "type": "free_text", "fields": [{"id": "password", "ask": "What's the password?", "type": "password"}]})
    _input(graph, "credentials", {"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    return graph


def login_stories() -> list[dict]:
    return [
        {
            "id": "demo_login_passkey",
            "name": "Sign in with the passkey",
            "blurb": "The passkey on file opens the demo account.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Use passkey", "Use passkey"],
            "expected_steps": 4,
            "expected_result": "account_opened",
        },
        {
            "id": "demo_login_email",
            "name": "Sign in with email and password",
            "blurb": "demo@kai.example and demo-pass match.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Email and password", DEMO_EMAIL, DEMO_PASSWORD],
            "expected_steps": 6,
            "expected_result": "account_opened",
        },
        {
            "id": "demo_login_wrong",
            "name": "A wrong password stays out",
            "blurb": "The password does not match, so the account stays closed.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Email and password", DEMO_EMAIL, "nope"],
            "expected_steps": 6,
            "expected_result": "denied",
        },
    ]


def choose_graph() -> dict:
    graph = _graph(
        [
            ("chat_request", "source", "chat_request", "On demand from chat"),
            ("ask_next", "question", "", "the option decides the next step", "What should I do next?"),
            ("get_balance", "fetch", "get_balance", "the option was a balance", "The balance is {target_balance}.", "get_balance"),
            ("balance_done", "result", "done", "they picked a balance", "That was the balance option.", None, "chose_balance"),
            ("alert_done", "result", "done", "they picked an alert", "An alert it is. Say demo alert when you want the yes or no.", None, "chose_alert"),
            ("move_done", "result", "done", "they picked a move", "A move it is. Say move 10 from CHF to EUR when you want the read-back.", None, "chose_move"),
        ],
        [
            ("chat_request", "ask_next", "continue"),
            ("ask_next", "get_balance", "check_balance"),
            ("ask_next", "alert_done", "save_alert"),
            ("ask_next", "move_done", "move_money"),
            ("get_balance", "balance_done", "continue"),
        ],
        {"chat": "chat_request"},
    )
    _input(graph, "get_balance", {"account": "EUR"})
    _input(
        graph,
        "ask_next",
        {
            "ask": "What should I do next?",
            "type": "simple_select",
            "options": [
                {"label": "Check a balance", "port": "check_balance"},
                {"label": "Save an alert", "port": "save_alert"},
                {"label": "Move money", "port": "move_money"},
            ],
        },
    )
    return graph


def choose_stories() -> list[dict]:
    return [
        {
            "id": "demo_choose_balance",
            "name": "The balance option",
            "blurb": "Check a balance reads EUR.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Check a balance"],
            "expected_steps": 4,
            "expected_result": "chose_balance",
        },
        {
            "id": "demo_choose_alert",
            "name": "The alert option",
            "blurb": "Save an alert skips the balance.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Save an alert"],
            "expected_steps": 3,
            "expected_result": "chose_alert",
        },
        {
            "id": "demo_choose_move",
            "name": "The move option",
            "blurb": "Move money skips the balance.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Move money"],
            "expected_steps": 3,
            "expected_result": "chose_move",
        },
    ]


def move_stories() -> list[dict]:
    return [
        {
            "id": "demo_move_yes",
            "name": "Move 10 from CHF to EUR",
            "blurb": "CHF has 18, so 10 fits, and they say yes.",
            "origin": "chat",
            "slots": {"amount": money(10, "CHF"), "source": "CHF", "target": "EUR"},
            "ledger": PREVIEW_LEDGER,
            "replies": ["yes"],
            "expected_steps": 7,
            "expected_result": "moved",
        },
        {
            "id": "demo_move_short",
            "name": "Not enough in CHF",
            "blurb": "CHF has 18, so 100 does not fit.",
            "origin": "chat",
            "slots": {"amount": money(100, "CHF"), "source": "CHF", "target": "EUR"},
            "ledger": PREVIEW_LEDGER,
            "replies": [],
            "expected_steps": 4,
            "expected_result": "not_enough",
        },
    ]


COUNTRIES = [
    {"label": "Germany", "port": "germany"},
    {"label": "France", "port": "france"},
    {"label": "Spain", "port": "spain"},
    {"label": "Italy", "port": "italy"},
    {"label": "Netherlands", "port": "netherlands"},
    {"label": "Ireland", "port": "ireland"},
    {"label": "Austria", "port": "austria"},
    {"label": "Other", "port": "other"},
]

FUND_SOURCES = [
    {"label": "Salary", "port": "salary"},
    {"label": "Savings", "port": "savings"},
    {"label": "Investments", "port": "investments"},
    {"label": "Business", "port": "business"},
    {"label": "Other", "port": "other"},
]


def list_graph() -> dict:
    country_edges = [("ask_residence", "ask_funds", item["port"]) for item in COUNTRIES if item["port"] != "other"]
    country_edges.append(("ask_residence", "not_served", "other"))
    country_edges.append(("ask_residence", "stopped", "objection"))
    fund_edges = [("ask_funds", "done", item["port"]) for item in FUND_SOURCES if item["port"] != "other"]
    fund_edges.append(("ask_funds", "other_source", "other"))
    fund_edges.append(("ask_funds", "stopped", "objection"))
    graph = _graph(
        [
            ("chat_request", "source", "chat_request", "On demand from chat"),
            (
                "ask_residence",
                "question",
                "",
                "one country from the list",
                "Where do you live?",
            ),
            (
                "ask_funds",
                "question",
                "",
                "where the money comes from",
                "Where does your money come from?",
            ),
            ("done", "result", "done", "both answers on the list", "Got it — {residence}, funds from {source_of_funds}.", None, "listed"),
            ("other_source", "result", "done", "funds not on the short list", "Got it — {residence}. We'll note that source separately.", None, "other_source"),
            ("not_served", "result", "not_done", "country not on the list", "Kai isn't available in that country yet. I can notify you when it is.", None, "not_served"),
            ("stopped", "result", "not_done", "three objections", "Okay, we can stop here.", None, "stopped"),
        ],
        [("chat_request", "ask_residence", "continue"), *country_edges, *fund_edges],
        {"chat": "chat_request"},
    )
    _input(
        graph,
        "ask_residence",
        {
            "ask": "Where do you live?",
            "type": "simple_select",
            "options": list(COUNTRIES),
        },
    )
    _input(
        graph,
        "ask_funds",
        {
            "ask": "Where does your money come from?",
            "type": "simple_select",
            "options": list(FUND_SOURCES),
        },
    )
    return graph


def list_stories() -> list[dict]:
    return [
        {
            "id": "demo_list_ok",
            "name": "Germany then salary",
            "blurb": "Both answers are on the predefined lists.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Germany", "Salary"],
            "expected_steps": 4,
            "expected_result": "listed",
        },
        {
            "id": "demo_list_other",
            "name": "A country not on the list",
            "blurb": "Other ends the residence path.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["Other"],
            "expected_steps": 3,
            "expected_result": "not_served",
        },
        {
            "id": "demo_list_three_objections",
            "name": "Three objections stop the list",
            "blurb": "The first two re-ask. The third follows the objection edge.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["i don't know", "i don't know", "i don't know"],
            "expected_steps": 3,
            "expected_result": "stopped",
        },
        {
            "id": "demo_list_one_then_ok",
            "name": "One objection then Germany",
            "blurb": "A single skip still lets them pick a country.",
            "origin": "chat",
            "slots": {},
            "ledger": PREVIEW_LEDGER,
            "replies": ["i don't know", "Germany", "Salary"],
            "expected_steps": 4,
            "expected_result": "listed",
        },
    ]


def bundles() -> list[dict]:
    config = eval_config()
    move_slots = [
        {"key": "amount", "ask": "How much should I move?", "fill_from": "a number in the message", "order": 1},
        {"key": "source", "ask": "Which account should I take it from?", "fill_from": "the account after from", "order": 2},
        {"key": "target", "ask": "Which account should I move it to?", "fill_from": "the account after to", "order": 3},
    ]
    return [
        {
            "key": "demo-balance",
            "name": "Demo balance",
            "group": "demo",
            "channels": ["chat"],
            "summary": "Reads one balance and says whether it is under 50. EUR is fine. USD is low.",
            "graph": balance_graph(),
            "chat_contract": _contract(
                "The customer wants the balance of one account, and whether it is under 50.",
                ["demo balance", "check EUR", "check USD"],
            ),
            "stories": balance_stories(),
            "eval_config": config,
            "sample_runs": ["demo_balance_eur", "demo_balance_usd"],
        },
        {
            "key": "demo-alert",
            "name": "Demo alert",
            "group": "demo",
            "channels": ["chat", "rule"],
            "summary": "A chat can save a below-50 alert. A rule tells them when it is already under, and stays quiet when it is not.",
            "graph": alert_graph(),
            "chat_contract": _contract(
                "Demo only — the customer typed demo alert to save a fixed below-50 watch.",
                ["demo alert"],
                [
                    {"key": "account", "order": 1, "fill_from": "the account they name", "ask": "Which account should I watch?"},
                    {"key": "threshold", "order": 2, "fill_from": "", "ask": ""},
                    {"key": "target", "order": 3, "fill_from": "", "ask": ""},
                ],
                exclusions=[
                    "tell me when my account goes under 50",
                    "tell me when it goes up to 65",
                    "tell me when my account balance goes below 50",
                    "warn me if my account drops below",
                ],
            ),
            "stories": alert_stories(),
            "eval_config": config,
            "sample_runs": ["demo_alert_usd", "demo_alert_gbp"],
        },
        {
            "key": "demo-move",
            "name": "Demo move",
            "group": "demo",
            "channels": ["chat"],
            "summary": "Moves money when the source can cover it and the customer says yes. Otherwise it stops.",
            "graph": move_graph(),
            "chat_contract": _contract(
                "The customer wants to move money from one of their accounts to another.",
                ["demo move", "move 10 from CHF to EUR"],
                move_slots,
            ),
            "stories": move_stories(),
            "eval_config": config,
            "sample_runs": ["demo_move_yes", "demo_move_short"],
        },
        {
            "key": "demo-login",
            "name": "Demo login",
            "group": "demo",
            "channels": ["chat"],
            "summary": "Signs this chat into the demo account. Use the passkey on file, or demo@kai.example and demo-pass. A wrong password stays out.",
            "graph": login_graph(),
            "chat_contract": _contract(
                "The customer wants to sign in to the demo account, with the passkey on file or with email and password.",
                ["demo login", "demo sign in"],
            ),
            "stories": login_stories(),
            "eval_config": config,
            "sample_runs": ["demo_login_passkey", "demo_login_wrong"],
        },
        {
            "key": "demo-choose",
            "name": "Demo choose",
            "group": "demo",
            "channels": ["chat"],
            "summary": "Offers three options. The one they pick is the next step.",
            "graph": choose_graph(),
            "chat_contract": _contract(
                "The customer wants to pick what happens next from a list of options.",
                ["demo choose", "demo options"],
            ),
            "stories": choose_stories(),
            "eval_config": config,
            "sample_runs": ["demo_choose_balance", "demo_choose_alert"],
        },
        {
            "key": "demo-list",
            "name": "Demo list",
            "group": "demo",
            "channels": ["chat"],
            "summary": "Asks residence and source of funds from predefined lists. Three skips stop the run.",
            "graph": list_graph(),
            "chat_contract": _contract(
                "The customer is answering residence and source of funds from a predefined list, like a short onboarding check.",
                ["demo list", "demo residence"],
            ),
            "stories": list_stories(),
            "eval_config": config,
            "sample_runs": ["demo_list_ok", "demo_list_three_objections"],
        },
    ]
