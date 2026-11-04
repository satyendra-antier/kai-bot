"""Low balance alert, sweep, FX rate watch, and just-in-time card top-up."""

from __future__ import annotations

from kai.engine.graphlib import chain, finish_graph, layout
from kai.engine.topup import PREVIEW_LEDGER, RICH_LEDGER, account, eval_config, money

ALERT_LINE = "Your {account} is at {balance} — below your {threshold}. Want to top it up?"


def _graph(rows: list[tuple], pairs: list[tuple[str, str, str]], entries: dict[str, str], budget: int = 40) -> dict:
    steps = layout(rows)
    edges = chain(pairs)
    finish_graph(steps, edges)
    return {"budget": budget, "entries": entries, "steps": steps, "edges": edges}


def alert_graph() -> dict:
    rows = [
        ("chat_request", "source", "chat_request", "On demand from chat"),
        ("get_balance", "fetch", "get_balance", "the account's balance now", None, "get_balance"),
        ("above_threshold", "condition", "compare", "still above the level", None, "above"),
        ("already_below", "result", "nothing", "Kai tells them, offers a top-up", "{account} is already at {balance} — below {threshold}.", None, "already_below"),
        ("confirm_alert", "ask", "confirm", "read back — nothing saved without a yes", "I'll tell you when {account} goes under {threshold}.", "confirm_alert"),
        ("cancelled", "result", "not_done", "the customer said no", "Okay, I won't save that alert.", None, "cancelled"),
        ("alert_saved", "result", "done", "rule saved, listed on Live rules", "I'll watch {account}.", None, "alert_saved"),
        ("balance_alert_rule", "source", "rule", "Triggered by rule"),
        ("balance_changed", "trigger", "start", "a posting hit the account"),
        ("get_balance_now", "fetch", "get_balance", "balance after the posting", None, "get_balance"),
        ("below_threshold", "condition", "compare", "available < threshold", None, "below"),
        ("still_fine", "result", "nothing", "nothing to say", None, None, "still_fine"),
        ("first_this_drop", "condition", "first_time", "not alerted since it went below", None, "first"),
        ("already_alerted", "result", "nothing", "told once already — stay quiet", None, None, "already_alerted"),
        ("send_alert", "action", "notify", "push and in-app message", ALERT_LINE),
        ("offer_topup", "approval", "approve_next", "the customer taps Top up, or leaves it"),
        ("alerted", "result", "done", "told, no top-up asked for", None, None, "alerted"),
        ("start_workflow", "action", "start_workflow", "hands over to Low balance top-up"),
        ("handed_off", "result", "done", "the top-up takes it from here", None, None, "handed_off"),
    ]
    pairs = [
        ("chat_request", "get_balance", "continue"),
        ("get_balance", "above_threshold", "continue"),
        ("above_threshold", "confirm_alert", "true"),
        ("above_threshold", "already_below", "false"),
        ("confirm_alert", "alert_saved", "yes"),
        ("confirm_alert", "cancelled", "no"),
        ("balance_alert_rule", "balance_changed", "continue"),
        ("balance_changed", "get_balance_now", "continue"),
        ("get_balance_now", "below_threshold", "continue"),
        ("below_threshold", "first_this_drop", "true"),
        ("below_threshold", "still_fine", "false"),
        ("first_this_drop", "send_alert", "true"),
        ("first_this_drop", "already_alerted", "false"),
        ("send_alert", "offer_topup", "continue"),
        ("offer_topup", "start_workflow", "approve"),
        ("offer_topup", "alerted", "reject"),
        ("start_workflow", "handed_off", "continue"),
    ]
    return _graph(rows, pairs, {"chat": "chat_request", "rule": "balance_alert_rule"})


def alert_contract() -> dict:
    return {
        "setup": "Chat sets up the alert and ends there. The alert comes from the rule; a top-up is handed to Low balance top-up.",
        "description": "The customer wants to be told when an account, wallet, or card falls below a level they name.",
        "examples": [
            "Let me know if my GBP account goes under £50",
            "Warn me if my USDT wallet goes under 100",
            "Tell me when my card drops below €20",
        ],
        "exclusions": ["top up my USD"],
        "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
        "idle": "I can top up an account, set a balance rule, or run a sweep. What would you like to do?",
        "slots": [
            {"key": "account", "order": 1, "fill_from": "the account, wallet, or card they name", "ask": "Which account should I watch?"},
            {"key": "threshold", "order": 2, "fill_from": "the level after under, below, or drops below", "ask": "What level should I watch for?"},
        ],
        "lines": {
            "confirm_alert": "I'll tell you when {account} goes under {threshold}.",
            "already_below": "{account} is already at {balance} — below {threshold}.",
            "send_alert": ALERT_LINE,
            "alert_saved": "I'll watch {account}.",
            "cancelled": "Okay, I won't save that alert.",
        },
        "never": [{"id": "no_move", "mode": "order", "text": "This workflow never moves money"}],
        "try_prompts": [
            "Let me know if my GBP account goes under £50",
            "Warn me if my USDT wallet goes under 100",
            "Tell me when my card drops below €20",
            "warn me when my euro balance drops below",
            "top up my USD",
        ],
        "preview_ledger": PREVIEW_LEDGER,
    }


def alert_stories() -> list[dict]:
    gbp = {"accounts": {"GBP": account(210, "GBP"), "USD": account(12, "USD")}}
    low = {"accounts": {"GBP": account(40, "GBP")}}
    return [
        {"id": "set_up", "name": "Set up in chat", "blurb": "The customer asks to be told when GBP goes under £50. It has £210, so Kai reads the alert back and saves it on a yes.", "origin": "chat", "slots": {"account": "GBP", "target": "GBP", "threshold": 50}, "ledger": gbp, "replies": ["yes"], "expected_steps": 5, "expected_result": "alert_saved"},
        {"id": "already_below", "name": "Already below", "blurb": "The account is already under the level. No alert is saved — Kai says so and offers a top-up now.", "origin": "chat", "slots": {"account": "GBP", "target": "GBP", "threshold": 50}, "ledger": low, "replies": [], "expected_steps": 4, "expected_result": "already_below"},
        {"id": "topup_started", "name": "Alerted, top-up started", "blurb": "A card payment takes USD to $12. The customer is told once, taps Top up, and Low balance top-up takes over in chat.", "origin": "rule", "slots": {"account": "USD", "target": "USD", "threshold": 50}, "ledger": PREVIEW_LEDGER, "replies": ["approve"], "expected_steps": 9, "expected_result": "handed_off"},
        {"id": "left_alone", "name": "Alerted, left alone", "blurb": "The customer is told and does nothing. The alert stays in the inbox until the balance recovers.", "origin": "rule", "slots": {"account": "USD", "target": "USD", "threshold": 50}, "ledger": PREVIEW_LEDGER, "replies": ["reject"], "expected_steps": 8, "expected_result": "alerted"},
        {"id": "still_above", "name": "Still above", "blurb": "A posting lands but the balance is still over the level. Nothing is sent.", "origin": "rule", "slots": {"account": "GBP", "target": "GBP", "threshold": 50}, "ledger": gbp, "replies": [], "expected_steps": 5, "expected_result": "still_fine"},
        {"id": "already_told", "name": "Already told", "blurb": "Still below, but the customer was told on this drop already. Kai stays quiet until it recovers and drops again.", "origin": "rule", "slots": {"account": "USD", "target": "USD", "threshold": 50, "already_alerted": True}, "ledger": PREVIEW_LEDGER, "replies": [], "expected_steps": 6, "expected_result": "already_alerted"},
    ]


def sweep_graph() -> dict:
    rows = [
        ("chat_request", "source", "chat_request", "On demand from chat"),
        ("get_source_balance", "fetch", "get_balance", "the account it comes from", None, "get_source_balance"),
        ("source_enough", "condition", "enough", "spare ≥ amount", None, "source_enough"),
        ("offer_alternative", "ask", "offer", "move what's there, or pick another", "I can move what's there, or you can pick another account.", "offer_alternative"),
        ("declined", "result", "not_done", "neither — nothing moves", "Okay, nothing moves.", None, "declined"),
        ("confirm_request", "ask", "confirm", "read back — nothing starts without a yes", "Move {amount} from {source} to {target}. Nothing starts until you say yes.", "confirm_request"),
        ("cancelled", "result", "not_done", "the customer said no", "Okay, I won't move anything.", None, "cancelled"),
        ("move_request", "trigger", "start", "the confirmed chat request"),
        ("get_balance", "fetch", "get_balance", "re-read the source now", None, "get_source_balance"),
        ("covers_amount", "condition", "enough", "source still covers it", None, "covers"),
        ("balance_moved", "result", "not_done", "changed since the check — back to chat", "The balance changed. Let me offer what's there now.", None, "balance_moved"),
        ("get_move_quote", "fetch", "get_quote", "Kai's rate, fee, what arrives", None, "get_move_quote"),
        ("within_limits", "condition", "limits", "tier limits and card cap", None, "within_limits"),
        ("over_limit", "result", "not_done", "customer told which limit", "That would go over a limit, so nothing moves.", None, "over_limit"),
        ("approve_move", "approval", "approve_money", "from, to, amount and rate, confirmed"),
        ("not_approved", "result", "not_done", "rejected, expired, or the quote ran out", None, None, "not_approved"),
        ("move_funds", "action", "move_money", "Move · at Kai's rate, or internal if same asset", None, "move_funds"),
        ("moved", "result", "done", "both balances updated", "{target} is updated.", None, "moved"),
    ]
    pairs = [
        ("chat_request", "get_source_balance", "continue"),
        ("get_source_balance", "source_enough", "continue"),
        ("source_enough", "confirm_request", "true"),
        ("source_enough", "offer_alternative", "false"),
        ("offer_alternative", "confirm_request", "continue"),
        ("offer_alternative", "declined", "no"),
        ("confirm_request", "move_request", "yes"),
        ("confirm_request", "cancelled", "no"),
        ("move_request", "get_balance", "continue"),
        ("get_balance", "covers_amount", "continue"),
        ("covers_amount", "get_move_quote", "true"),
        ("covers_amount", "balance_moved", "false"),
        ("get_move_quote", "within_limits", "continue"),
        ("within_limits", "approve_move", "true"),
        ("within_limits", "over_limit", "false"),
        ("approve_move", "move_funds", "approve"),
        ("approve_move", "not_approved", "reject"),
        ("move_funds", "moved", "continue"),
    ]
    return _graph(rows, pairs, {"chat": "chat_request"})


def sweep_contract() -> dict:
    return {
        "setup": "Moves money between the customer's own balances. Chat only — a one-off move, no rule.",
        "description": "The customer wants to move money from one of their accounts to another, or onto the card.",
        "examples": ["Move €50 from EUR to USDT", "Move €200 from EUR to USD"],
        "exclusions": ["send 50 to @dana"],
        "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
        "idle": "I can top up an account, set a balance rule, or run a sweep. What would you like to do?",
        "slots": [
            {"key": "amount", "order": 1, "fill_from": "the amount", "ask": "How much should I move?"},
            {"key": "source", "order": 2, "fill_from": "the account after from", "ask": "Which account should I take it from?"},
            {"key": "target", "order": 3, "fill_from": "the account after to", "ask": "Which account should I move it to?"},
        ],
        "lines": {"confirm_request": "Move {amount} from {source} to {target}. Nothing starts until you say yes.", "moved": "{target} is updated."},
        "never": [],
        "try_prompts": ["Move €50 from EUR to USDT"],
        "preview_ledger": PREVIEW_LEDGER,
    }


def sweep_stories() -> list[dict]:
    rich = {**RICH_LEDGER, "accounts": {**RICH_LEDGER["accounts"], "EUR": account(500, "EUR")}}
    return [
        {"id": "moved", "name": "Asked in chat, moved", "blurb": "“Move €50 from EUR to USDT.” EUR covers it, Kai reads it back, the customer approves the rate, and USDT is credited.", "origin": "chat", "slots": {"source": "EUR", "target": "USDT", "amount": money(50, "EUR"), "runs_left": 3, "cap": 10000}, "ledger": rich, "replies": ["yes", "approve"], "expected_steps": 12, "expected_result": "moved"},
        {"id": "moved_what_was_there", "name": "Source short, moved what was there", "blurb": "The customer asks for €200 but EUR only has €62.40. Kai offers to move all of it or pick another account; they take all of it, confirm, and approve.", "origin": "chat", "slots": {"source": "EUR", "target": "USD", "amount": money(200, "EUR"), "runs_left": 3, "cap": 10000}, "ledger": PREVIEW_LEDGER, "replies": ["all", "yes", "approve"], "expected_steps": 13, "expected_result": "moved"},
        {"id": "declined", "name": "Source short, nothing moved", "blurb": "EUR is short and the customer wants neither option. Nothing moves.", "origin": "chat", "slots": {"source": "EUR", "target": "USD", "amount": money(200, "EUR")}, "ledger": PREVIEW_LEDGER, "replies": ["no"], "expected_steps": 5, "expected_result": "declined"},
        {"id": "balance_moved", "name": "Balance moved before the run", "blurb": "Confirmed, but the EUR balance dropped between the check and the run. Nothing moves; Kai goes back to offering what is there now or another account.", "origin": "chat", "slots": {"source": "EUR", "target": "USDT", "amount": money(50, "EUR"), "runs_left": 3, "cap": 10000, "balance_dropped": True}, "ledger": rich, "replies": ["yes"], "expected_steps": 8, "expected_result": "balance_moved"},
        {"id": "over_limit", "name": "Over a limit", "blurb": "The move would go over the customer's tier limit or the card cap. They are told which, and nothing moves.", "origin": "chat", "slots": {"source": "EUR", "target": "USDT", "amount": money(50, "EUR"), "runs_left": 3, "cap": 10}, "ledger": rich, "replies": ["yes"], "expected_steps": 10, "expected_result": "over_limit"},
        {"id": "not_approved", "name": "Not approved", "blurb": "The customer rejects the approval card, or the 30-second quote runs out without a confirm. Nothing moves.", "origin": "chat", "slots": {"source": "EUR", "target": "USDT", "amount": money(50, "EUR"), "runs_left": 3, "cap": 10000}, "ledger": rich, "replies": ["yes", "reject"], "expected_steps": 11, "expected_result": "not_approved"},
    ]


def fx_graph() -> dict:
    rows = [
        ("chat_request", "source", "chat_request", "On demand from chat"),
        ("confirm_watch", "ask", "confirm", "read back the rate to watch", "I'll tell you when {pair} goes {direction} {target_rate}.", "confirm_watch"),
        ("cancelled", "result", "not_done", "the customer said no", None, None, "cancelled"),
        ("get_rate", "fetch", "get_rate", "checked right after the yes", None, "get_rate"),
        ("reached_target", "condition", "reached", "is the rate at the target?", None, "reached"),
        ("already_reached", "result", "nothing", "at the target — Kai says so, request finished", "That rate is already there.", None, "already_reached"),
        ("keep_watching", "action", "schedule", "not there yet — check again in 1 min, no message"),
        ("rate_watch_rule", "source", "rule", "Triggered by rule"),
        ("watch_request", "trigger", "start", "a chat request, or a saved rule"),
        ("get_rate_tick", "fetch", "get_rate", "Kai's rate, every minute", None, "get_rate"),
        ("reached_now", "condition", "reached", "is the rate at the target?", None, "reached_now"),
        ("send_notification", "action", "notify", "app · Kai push · email"),
        ("from_chat", "condition", "origin", "a one-off request, not a rule", None, "from_chat"),
        ("rule_rearmed", "result", "nothing", "rule stays on in Skills", None, None, "rule_rearmed"),
        ("offer_rule", "ask", "offer", "save it as a rule — and at what rate", "Want me to keep this as a rule?", "offer_rule"),
        ("finished", "result", "done", "notified, no rule", None, None, "finished"),
        ("save_rule", "action", "save_rule", "written as a rule, listed in Skills"),
        ("rule_saved", "result", "done", "the customer can change it in Skills", None, None, "rule_saved"),
    ]
    pairs = [
        ("chat_request", "confirm_watch", "continue"),
        ("confirm_watch", "get_rate", "yes"),
        ("confirm_watch", "cancelled", "no"),
        ("get_rate", "reached_target", "continue"),
        ("reached_target", "already_reached", "true"),
        ("reached_target", "keep_watching", "false"),
        ("keep_watching", "watch_request", "continue"),
        ("rate_watch_rule", "watch_request", "continue"),
        ("watch_request", "get_rate_tick", "continue"),
        ("get_rate_tick", "reached_now", "continue"),
        ("reached_now", "send_notification", "true"),
        ("reached_now", "keep_watching", "false"),
        ("send_notification", "from_chat", "continue"),
        ("from_chat", "offer_rule", "true"),
        ("from_chat", "rule_rearmed", "false"),
        ("offer_rule", "save_rule", "yes"),
        ("offer_rule", "finished", "no"),
        ("save_rule", "rule_saved", "continue"),
    ]
    return _graph(rows, pairs, {"chat": "chat_request", "rule": "rate_watch_rule"})


def fx_contract() -> dict:
    return {
        "setup": "Tells the customer when an exchange rate reaches the level they asked for. Nothing is converted here.",
        "description": "The customer wants to be told when an exchange rate reaches a level they name.",
        "examples": ["Tell me when EUR/USD goes above 1.10"],
        "exclusions": ["convert 100 euros to dollars"],
        "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
        "idle": "I can top up an account, set a balance rule, or run a sweep. What would you like to do?",
        "slots": [
            {"key": "pair", "order": 1, "fill_from": "the currency pair", "ask": "Which currency pair should I watch?"},
            {"key": "target_rate", "order": 2, "fill_from": "the rate they name", "ask": "What rate should I tell you about?"},
        ],
        "lines": {"confirm_watch": "I'll tell you when {pair} goes above {target_rate}."},
        "never": [],
        "try_prompts": ["Tell me when EUR/USD goes above 1.10"],
        "preview_ledger": PREVIEW_LEDGER,
    }


def fx_stories() -> list[dict]:
    short = {"pair": "EUR/USD", "target_rate": 1.10, "rate_now": 1.05, "rate_later": 1.10, "direction": "above"}
    there = {"pair": "EUR/USD", "target_rate": 1.10, "rate_now": 1.10, "direction": "above"}
    return [
        {"id": "saved", "name": "Asked in chat, saved as a rule", "blurb": "The customer asks to be told when EUR/USD goes above 1.10. It reaches the target, they are notified, and they save 1.12 as a rule in Skills.", "origin": "chat", "slots": short, "replies": ["yes", "yes"], "expected_steps": 13, "expected_result": "rule_saved"},
        {"id": "no_rule", "name": "Notified, no rule", "blurb": "Same watch, same notification. The customer says no to a rule, and the request is finished.", "origin": "chat", "slots": short, "replies": ["yes", "no"], "expected_steps": 12, "expected_result": "finished"},
        {"id": "already", "name": "Already there", "blurb": "The customer confirms the rate to watch, Kai checks it, and it has already reached the target. Nothing is watched.", "origin": "chat", "slots": there, "replies": ["yes"], "expected_steps": 5, "expected_result": "already_reached"},
        {"id": "rule_fires", "name": "Rule fires", "blurb": "A saved rule sees the rate reach the target. The customer is notified on all three channels, and the rule stays on in Skills.", "origin": "rule", "slots": short, "replies": [], "expected_steps": 7, "expected_result": "rule_rearmed"},
        {"id": "not_yet", "name": "Not yet", "blurb": "The rate is short of the target, so it goes to keep watching. Nothing is sent — Kai checks again in a minute.", "origin": "chat", "slots": {**short, "pause_at": "watch_request"}, "replies": ["yes"], "expected_steps": 6, "expected_result": "watch_request"},
    ]


def jit_graph() -> dict:
    rows = [
        ("card_rule", "source", "rule", "Triggered by rule"),
        ("card_balance_low", "trigger", "start", "balance fell below the threshold"),
        ("get_card_balance", "fetch", "get_balance", "re-read the balance now", None, "get_card_balance"),
        ("still_low", "condition", "compare", "card_available < threshold", None, "still_low"),
        ("recovered", "result", "nothing", "back above the threshold", None, None, "recovered"),
        ("get_funding_accounts", "fetch", "get_accounts", "the accounts they chose, in order", None, "get_funding_accounts"),
        ("pick_source", "keep_matching", "pick", "first account with enough", None, "pick_source"),
        ("can_cover", "condition", "enough", "a source found, and amount ≤ €500", None, "can_cover"),
        ("cannot_cover", "result", "not_done", "customer told the card is low", None, None, "cannot_cover"),
        ("approve_topup", "approval", "approve_money", "amount and source, confirmed in the app"),
        ("not_approved", "result", "not_done", "rejected, or expired unanswered", None, None, "not_approved"),
        ("fund_card", "action", "move_money", "Card funding · same asset", None, "move_funds"),
        ("funded", "result", "done", "card back at €200", None, None, "funded"),
    ]
    pairs = [
        ("card_rule", "card_balance_low", "continue"),
        ("card_balance_low", "get_card_balance", "continue"),
        ("get_card_balance", "still_low", "continue"),
        ("still_low", "get_funding_accounts", "true"),
        ("still_low", "recovered", "false"),
        ("get_funding_accounts", "pick_source", "continue"),
        ("pick_source", "can_cover", "continue"),
        ("pick_source", "can_cover", "false"),
        ("can_cover", "approve_topup", "true"),
        ("can_cover", "cannot_cover", "false"),
        ("approve_topup", "fund_card", "approve"),
        ("approve_topup", "not_approved", "reject"),
        ("fund_card", "funded", "continue"),
    ]
    return _graph(rows, pairs, {"rule": "card_rule"})


def jit_stories() -> list[dict]:
    low = {"accounts": {"CARD": account(12, "EUR", kind="card"), "EUR": account(2000, "EUR", floor=50)}}
    high = {"accounts": {"CARD": account(250, "EUR", kind="card"), "EUR": account(2000, "EUR")}}
    empty = {"accounts": {"CARD": account(12, "EUR", kind="card"), "EUR": account(10, "EUR", floor=50)}}
    base = {"target": "CARD", "threshold": 50, "amount": money(200, "EUR"), "cap": 500, "top_up_to": 200}
    return [
        {"id": "funded", "name": "Approved and funded", "blurb": "The card falls below the threshold, an account can cover the top-up, the customer approves, and the card is back at its top-up level.", "origin": "rule", "slots": base, "ledger": low, "replies": ["approve"], "expected_steps": 10, "expected_result": "funded"},
        {"id": "recovered", "name": "Recovered on its own", "blurb": "By the time the workflow re-reads it, the card is back above the threshold. Nothing is asked, nothing moves.", "origin": "rule", "slots": base, "ledger": high, "replies": [], "expected_steps": 5, "expected_result": "recovered"},
        {"id": "cannot", "name": "Nothing can cover it", "blurb": "None of the accounts the customer chose holds enough, or the top-up is over the €500 cap. The customer is only told the card is low.", "origin": "rule", "slots": base, "ledger": empty, "replies": [], "expected_steps": 8, "expected_result": "cannot_cover"},
        {"id": "not_approved", "name": "Not approved", "blurb": "The customer rejects the top-up, or leaves it unanswered for 24 hours. No answer counts as a no.", "origin": "rule", "slots": base, "ledger": low, "replies": ["reject"], "expected_steps": 9, "expected_result": "not_approved"},
    ]


def bundles() -> list[dict]:
    config = eval_config()
    return [
        {"key": "low-balance-alert", "name": "Low balance alert", "group": "", "channels": ["chat", "rule"], "summary": "Tells the customer when any of their balances — a fiat account, a crypto wallet or the card — drops below a level they set, and offers a top-up — which hands over to Low balance top-up. Nothing moves in this workflow.", "graph": alert_graph(), "chat_contract": alert_contract(), "stories": alert_stories(), "eval_config": config},
        {"key": "sweep-between-accounts", "name": "Sweep between accounts", "group": "move", "channels": ["chat"], "summary": "Moves money between the customer’s own balances — any fiat account or crypto wallet to any other, or onto the card. Chat only — a one-off move the customer asks for, no rule. Crosses assets with a Move at Kai’s rate.", "graph": sweep_graph(), "chat_contract": sweep_contract(), "stories": sweep_stories(), "eval_config": config},
        {"key": "fx-rate-watch", "name": "FX rate watch", "group": "fiat", "channels": ["chat", "rule"], "summary": "Tells the customer when an exchange rate reaches the level they asked for — by app notification, Kai push and email. Nothing is converted here.", "graph": fx_graph(), "chat_contract": fx_contract(), "stories": fx_stories(), "eval_config": config},
        {"key": "jit-card-topup", "name": "Just-in-time card top-up", "group": "cards", "channels": ["rule"], "summary": "Tops the card back up when its balance falls below the threshold the customer set, from the accounts they chose, once they approve the amount and the source. Trigger only — a card top-up asked for in chat is Low balance top-up.", "graph": jit_graph(), "chat_contract": {"description": "", "examples": [], "exclusions": [], "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5}, "slots": [], "lines": {}, "never": [], "try_prompts": [], "preview_ledger": PREVIEW_LEDGER}, "stories": jit_stories(), "eval_config": config},
    ]
