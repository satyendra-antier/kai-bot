"""Low balance top-up: 23 steps, the reference graph."""

from __future__ import annotations

from kai.engine.advance import format_money


def money(amount: float, currency: str) -> dict:
    return {"amount": amount, "currency": currency, "display": format_money(amount, currency)}


def account(amount: float, currency: str, floor: float = 0, kind: str = "account") -> dict:
    return {"amount": amount, "currency": currency, "floor": floor, "kind": kind}


PREVIEW_LEDGER = {
    "rate": 1.08,
    "fee": 0,
    "quote_ttl": 30,
    "accounts": {
        "EUR": account(62.40, "EUR"),
        "USD": account(12, "USD"),
        "USDT": account(950, "USDT"),
        "GBP": account(210, "GBP"),
        "CHF": account(18, "CHF"),
        "CARD": account(18.40, "EUR", kind="card"),
    },
}


def _step(
    step_id: str,
    kind: str,
    step_type: str,
    title: str,
    x: int,
    y: int,
    line: str | None = None,
    role: str | None = None,
    result: str | None = None,
    purpose: str = "",
    **config: object,
) -> dict:
    stored = dict(config)
    if role:
        stored["role"] = role
    return {
        "id": step_id,
        "kind": kind,
        "type": step_type,
        "title": title,
        "purpose": purpose or title,
        "line": line,
        "position": {"x": x, "y": y},
        "speed_target_ms": 2000,
        "config": stored,
        "result": result,
    }


def _edge(from_step: str, to_step: str, port: str) -> dict:
    return {"id": f"{from_step}:{port}:{to_step}", "from": from_step, "to": to_step, "port": port}


LINES = {
    "chat_request": "I might have this wrong. Do you want to top an account up from another?",
    "check_other_accounts": "Your {source} account has {source_balance} — not enough for {amount}. Let me check your other accounts.",
    "offer_account": "{options} can cover it. Which should I use?",
    "none_sufficient": "Nothing covers {amount}. The largest I can see is {largest}.",
    "confirm_request": "Top up {target} with {amount} from {source}. Nothing moves until you say yes.",
    "cancelled": "Okay, I won't move anything.",
    "approve_topup": "Approve {amount} from {source} into {target}.",
    "cannot_cover": "{source} can no longer cover this. {options}",
    "topped_up": "{target} is back at {target_after}.",
}


def low_balance_topup_graph() -> dict:
    steps = [
        _step("chat_request", "source", "chat_request", "On demand from chat", 640, 0, purpose="On demand from chat"),
        _step("get_source_balance", "fetch", "get_balance", "the account the customer named", 640, 120, role="get_source_balance", purpose="the account the customer named"),
        _step("source_enough", "condition", "enough", "spare ≥ amount", 640, 240, role="source_enough", purpose="spare ≥ amount"),
        _step("check_other_accounts", "fetch", "get_accounts", "tell them it's short, look at the rest", 360, 360, LINES["check_other_accounts"], role="check_other_accounts", purpose="tell them it's short, look at the rest"),
        _step("any_sufficient", "condition", "enough", "another account covers €100", 360, 480, role="any_sufficient", purpose="another account covers €100"),
        _step("none_sufficient", "result", "not_done", "ends with a note", 120, 600, LINES["none_sufficient"], result="none", purpose="ends with a note"),
        _step("offer_account", "ask", "offer", "customer picks one that can cover it", 360, 600, LINES["offer_account"], role="offer_account", purpose="customer picks one that can cover it"),
        _step("confirm_request", "ask", "confirm", "read back — nothing starts without a yes", 880, 360, LINES["confirm_request"], role="confirm_request", purpose="read back — nothing starts without a yes"),
        _step("cancelled", "result", "not_done", "the customer said no", 1120, 480, LINES["cancelled"], result="cancelled", purpose="the customer said no"),
        _step("balance_rule", "source", "rule", "Triggered by rule", 80, 40, purpose="Triggered by rule"),
        _step("topup_request", "trigger", "start", "same inputs from either source", 520, 720, purpose="same inputs from either source"),
        _step("get_balance", "fetch", "get_balance", "re-read the target account now", 520, 840, role="get_balance", purpose="re-read the target account now"),
        _step("needs_topup", "condition", "compare", "still below threshold — or asked for in chat", 520, 960, role="needs_topup", purpose="still below threshold — or asked for in chat"),
        _step("recovered", "result", "nothing", "back above the threshold", 200, 1080, result="recovered", purpose="back above the threshold"),
        _step("get_source_accounts", "fetch", "get_accounts", "balances and the floor each keeps", 520, 1080, role="get_source_accounts", purpose="balances and the floor each keeps"),
        _step("pick_source", "keep_matching", "pick", "rule: first with enough · chat: re-check the chosen one", 520, 1200, role="pick_source", purpose="rule: first with enough · chat: re-check the chosen one"),
        _step("get_move_quote", "fetch", "get_quote", "Kai's rate, fee, what arrives", 520, 1320, role="get_move_quote", purpose="Kai's rate, fee, what arrives"),
        _step("within_limits", "condition", "limits", "under the cap, runs left today, no loop", 520, 1440, role="within_limits", purpose="under the cap, runs left today, no loop"),
        _step("cannot_cover", "result", "not_done", "customer told why nothing moved", 200, 1560, LINES["cannot_cover"], result="cannot", purpose="customer told why nothing moved"),
        _step("approve_topup", "approval", "approve_money", "amount, source and rate, confirmed", 520, 1560, LINES["approve_topup"], purpose="amount, source and rate, confirmed", ttl=30),
        _step("not_approved", "result", "not_done", "rejected, expired, or the quote ran out", 800, 1680, "That top-up did not go through.", result="not_approved", purpose="rejected, expired, or the quote ran out"),
        _step("move_funds", "action", "move_money", "Move · EUR → USD at Kai's rate", 520, 1680, role="move_funds", purpose="Move · EUR → USD at Kai's rate"),
        _step("topped_up", "result", "done", "USD back at $300", 520, 1800, LINES["topped_up"], result="done", purpose="USD back at $300"),
    ]
    edges = [
        _edge("chat_request", "get_source_balance", "continue"),
        _edge("get_source_balance", "source_enough", "continue"),
        _edge("source_enough", "confirm_request", "true"),
        _edge("source_enough", "check_other_accounts", "false"),
        _edge("check_other_accounts", "any_sufficient", "continue"),
        _edge("any_sufficient", "none_sufficient", "false"),
        _edge("any_sufficient", "offer_account", "true"),
        _edge("offer_account", "confirm_request", "continue"),
        _edge("offer_account", "cancelled", "no"),
        _edge("confirm_request", "topup_request", "yes"),
        _edge("confirm_request", "cancelled", "no"),
        _edge("balance_rule", "topup_request", "continue"),
        _edge("topup_request", "get_balance", "continue"),
        _edge("get_balance", "needs_topup", "continue"),
        _edge("needs_topup", "recovered", "false"),
        _edge("needs_topup", "get_source_accounts", "true"),
        _edge("get_source_accounts", "pick_source", "continue"),
        _edge("pick_source", "get_move_quote", "continue"),
        _edge("pick_source", "cannot_cover", "false"),
        _edge("get_move_quote", "within_limits", "continue"),
        _edge("within_limits", "approve_topup", "true"),
        _edge("within_limits", "cannot_cover", "false"),
        _edge("within_limits", "offer_account", "reoffer"),
        _edge("approve_topup", "move_funds", "approve"),
        _edge("approve_topup", "not_approved", "reject"),
        _edge("move_funds", "topped_up", "continue"),
    ]
    from kai.engine.graphlib import finish_graph

    finish_graph(steps, edges)
    return {"budget": 40, "entries": {"chat": "chat_request", "rule": "balance_rule"}, "steps": steps, "edges": edges}


def chat_contract() -> dict:
    return {
        "description": "The customer wants money moved from one of their accounts onto another account or their card, because they asked to top it up, refill it, or add a sum to it.",
        "examples": [
            "Top up my USD with €100 from EUR",
            "Top up my card with €50 from EUR",
            "My dollar account is almost empty, move 200 from euros",
            "Refill USDT from GBP",
            "Add 50 to my GBP account",
        ],
        "exclusions": ["send 50 to @dana"],
        "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
        "idle": "I can top up an account, set a balance rule, or run a sweep. What would you like to do?",
        "slots": [
            {"key": "target", "order": 1, "fill_from": 'the account or card after "top up", "refill", or "add … to"', "ask": "Which account or card should I top up?"},
            {"key": "amount", "order": 2, "fill_from": "a number, with € $ £ or a currency name; no currency means the target's", "ask": "How much should I move?"},
            {"key": "source", "order": 3, "fill_from": 'the account after "from"', "ask": "Which account should I take it from?"},
        ],
        "lines": LINES,
        "never": [
            {"id": "msg_before_approval", "mode": "order", "text": "A customer-facing message before the approval event"},
            {"id": "no_guaranteed_profit", "mode": "judged", "text": "Never promise a profit", "forbidden": ["guaranteed profit"]},
        ],
        "try_prompts": [
            "Top up my USD with €100 from EUR",
            "top up my card with 50 from GBP",
            "top up USD with 100 from USDT",
            "refill my GBP",
            "send 50 to @dana",
        ],
        "preview_ledger": PREVIEW_LEDGER,
    }


SUMMARY = (
    "Keeps one of the customer’s accounts topped up from their other accounts — when a rule they set sees it run low, "
    "or when they ask Kai in chat. A card top-up asked for in chat lands here too. Crosses assets with a Move at Kai’s rate."
)


def bundle() -> dict:
    return {
        "key": "low-balance-top-up",
        "name": "Low balance top-up",
        "group": "",
        "channels": ["chat", "rule"],
        "summary": SUMMARY,
        "graph": low_balance_topup_graph(),
        "chat_contract": chat_contract(),
        "stories": stories(),
        "eval_config": eval_config(),
    }


def eval_config() -> dict:
    return {
        "chat_bar": 80,
        "outcome_bar": 80,
        "weights": {
            "chat": {"intent": 20, "slots": 20, "turns": 20, "lines": 20, "guardrails": 20},
            "outcome": {"right_end": 25, "speed": 25, "finished": 25, "no_errors": 25},
        },
    }


RICH_LEDGER = {
    "rate": 1.08,
    "fee": 0,
    "accounts": {
        "EUR": account(2000, "EUR", floor=50),
        "USD": account(12, "USD"),
        "USDT": account(950, "USDT"),
        "GBP": account(210, "GBP"),
        "CHF": account(18, "CHF"),
        "CARD": account(0, "USD", kind="card"),
    },
}


def stories() -> list[dict]:
    return [
        {
            "id": "rule_approved",
            "name": "Rule fires, approved",
            "blurb": "USD drops below $50. EUR has spare above its floor, the quote is inside the limits, the customer approves the rate and amount, and USD is back at $300.",
            "origin": "rule",
            "slots": {"target": "USD", "amount": money(250, "EUR"), "threshold": 50, "top_up_to": 300, "runs_left": 3, "cap": 10000},
            "ledger": RICH_LEDGER,
            "replies": ["approve"],
            "expected_steps": 11,
            "expected_result": "done",
        },
        {
            "id": "chat_enough_eur",
            "name": "Asked in chat, enough in EUR",
            "blurb": "“Top up USD with €100 from EUR.” Kai checks EUR, it has enough, Kai reads the request back, the customer says yes, and the run goes through to the same approval card in the conversation.",
            "origin": "chat",
            "slots": {"target": "USD", "amount": money(100, "EUR"), "source": "EUR", "runs_left": 3, "cap": 10000},
            "ledger": {**RICH_LEDGER, "accounts": {**RICH_LEDGER["accounts"], "EUR": account(500, "EUR")}},
            "replies": ["yes", "approve"],
            "expected_steps": 14,
            "expected_result": "done",
        },
        {
            "id": "named_short_other_offered",
            "name": "Named account short, another offered",
            "blurb": "EUR only has €62.40. Kai says so, checks the other accounts, offers USDT and GBP, the customer picks USDT, confirms the read-back, and the run tops up from USDT.",
            "origin": "chat",
            "slots": {"target": "USD", "amount": money(100, "EUR"), "source": "EUR", "runs_left": 3, "cap": 10000},
            "ledger": PREVIEW_LEDGER,
            "replies": ["USDT", "yes", "approve"],
            "expected_steps": 17,
            "expected_result": "done",
            "expect_source": "USDT",
        },
        {
            "id": "none_cover",
            "name": "No account can cover it",
            "blurb": "EUR is short and so is every other account. The flow ends with a note: Kai tells the customer, names the largest balance, and offers that smaller amount or adding money first.",
            "origin": "chat",
            "slots": {"target": "USD", "amount": money(100, "EUR"), "source": "EUR"},
            "ledger": {
                "accounts": {
                    "EUR": account(10, "EUR"),
                    "USD": account(5, "USD"),
                    "USDT": account(8, "USDT"),
                    "GBP": account(9, "GBP"),
                    "CHF": account(4, "CHF"),
                }
            },
            "replies": [],
            "expected_steps": 6,
            "expected_result": "none",
        },
        {
            "id": "recovered",
            "name": "Recovered on its own",
            "blurb": "The rule fired, but money landed before the run re-read the balance. Nothing is asked, nothing moves.",
            "origin": "rule",
            "slots": {"target": "USD", "amount": money(100, "EUR"), "threshold": 50},
            "ledger": {"accounts": {"USD": account(400, "USD"), "EUR": account(2000, "EUR")}},
            "replies": [],
            "expected_steps": 5,
            "expected_result": "recovered",
        },
        {
            "id": "cannot_cover",
            "name": "Nothing can cover it",
            "blurb": "No source has enough above its floor, the amount is over the cap, the daily runs are used up, or the move would feed another rule’s source. The customer is told why.",
            "origin": "rule",
            "slots": {"target": "USD", "amount": money(250, "EUR"), "threshold": 50, "top_up_to": 300, "runs_left": 0, "cap": 10000},
            "ledger": RICH_LEDGER,
            "replies": [],
            "expected_steps": 9,
            "expected_result": "cannot",
        },
        {
            "id": "not_approved",
            "name": "Not approved",
            "blurb": "The customer rejects, leaves it past the window, or the 30-second quote runs out without a confirm. No answer counts as a no.",
            "origin": "rule",
            "slots": {"target": "USD", "amount": money(250, "EUR"), "threshold": 50, "top_up_to": 300, "runs_left": 3, "cap": 10000},
            "ledger": RICH_LEDGER,
            "replies": ["reject"],
            "expected_steps": 10,
            "expected_result": "not_approved",
        },
    ]
