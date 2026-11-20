"""The 9 kinds and shared step types for money canvases (Add step / Settings)."""

KINDS = [
    "source",
    "trigger",
    "fetch",
    "condition",
    "keep_matching",
    "ask",
    "approval",
    "action",
    "result",
]


def _type(step_type, kind, title, ports, purpose, summary, slug, step_input, step_output):
    return {
        "type": step_type,
        "kind": kind,
        "title": title,
        "ports": ports,
        "purpose": purpose,
        "summary": summary,
        "slug": slug,
        "input": step_input,
        "output": step_output,
    }


STEP_TYPES = [
    _type("chat_request", "source", "Chat request", ["continue"], "the customer asked", "The customer asks Kai. Slots fill from the message; what is missing Kai asks for.", "chat_request", {}, {"conv_id": "<id>", "intent": "<intent>", "intent_confidence": 0, "slots": {}, "filled_from": {}}),
    _type("rule", "source", "Rule", ["continue"], "a saved rule fired", "A saved rule fires — on a posting, a schedule, or a watched value crossing its level.", "rule", {}, {"rule_id": "<id>", "fired_at": "<time>", "values": {}}),
    _type("start", "trigger", "Start", ["continue"], "same inputs from either source", "Takes the confirmed request or the fired rule and starts the run with one set of inputs.", "start", {"origin": "chat | rule"}, {"origin": "<chat | rule>", "inputs": {}}),
    _type("get_balance", "fetch", "Get balance", ["continue"], "what one account holds right now", "What one account, wallet or card holds right now, and what it can spare above its floor.", "get_balance", {"account": "<account>"}, {"available": 0, "ccy": "<ccy>", "floor": 0, "spare": 0}),
    _type("get_accounts", "fetch", "Get accounts", ["continue"], "the customer's accounts with balances", "The customer's accounts with balances — all of them, the ones they chose, or the rest after one is excluded.", "get_accounts", {"customer": "<@handle>", "which": "chosen | all | except", "exclude": []}, {"accounts": [{"account": "<account>", "available": 0, "ccy": "<ccy>", "floor": 0}]}),
    _type("get_rate", "fetch", "Get rate", ["continue"], "Kai's rate for a pair", "Kai's rate for a currency pair now.", "get_rate", {"pair": "<BASE/QUOTE>"}, {"rate": 0, "at": "<time>"}),
    _type("get_quote", "fetch", "Get quote", ["continue"], "rate, fee, and what arrives", "A Move quote: rate, fee and what arrives, valid for a few seconds.", "get_quote", {"move_type": "<move type>", "from": "<account>", "to": "<account>", "send": 0}, {"quote_id": "<id>", "rate": 0, "fee": 0, "send": 0, "receive": 0, "valid_s": 30}),
    _type("pick", "keep_matching", "Pick", ["continue", "false"], "the first item that meets the rule", "The first item in an ordered list that meets a rule — e.g. the first account with enough above its floor.", "pick", {"list": [], "rule": "first with enough"}, {"picked": "<item>", "available": 0}),
    _type("compare", "condition", "Compare to a level", ["true", "false"], "is a value below or above a level", "Is a value below or above a level the customer set.", "compare", {"value": 0, "level": 0, "direction": "below | above"}, {"result": "yes | no"}),
    _type("enough", "condition", "Is there enough", ["true", "false"], "spare ≥ amount", "Does a balance, or any balance in a list, cover an amount.", "enough", {"available": 0, "amount": 0}, {"result": "yes | no", "short_by": 0}),
    _type("reached", "condition", "Reached target", ["true", "false"], "has the watched value reached the target", "Has a watched value reached the target the customer asked for.", "reached", {"value": 0, "target": 0, "direction": "above | below"}, {"reached": "yes | no"}),
    _type("limits", "condition", "Within limits", ["true", "false", "reoffer"], "under the cap, runs left, no loop", "Tier limits, caps, runs per day, and no loop between rules.", "limits", {"amount": 0, "limits": {"tier_left": 0, "cap": 0, "runs_left": 0}}, {"result": "yes | no", "limit": None}),
    _type("first_time", "condition", "First time", ["true", "false"], "told once for this drop", "Has this already happened for the same event — so a customer is told once, not every posting.", "first_time", {"event": "<event>", "last_time": None}, {"result": "yes | no"}),
    _type("origin", "condition", "Came from", ["true", "false"], "which source started the run", "Which source started the run, when the path after depends on it.", "origin", {"origin": "chat | rule"}, {"result": "yes | no"}),
    _type("confirm", "ask", "Read back", ["yes", "no"], "nothing starts without a yes", "Kai reads the whole request back. Nothing starts without a yes.", "confirm", {"request": {}}, {"confirmed": True}),
    _type(
        "ask_options",
        "ask",
        "Ask with options",
        ["continue"],
        "the customer picks from a list",
        "Kai asks a question and shows a fixed list (like Demo list residence/funds). Each option is a port on the canvas.",
        "ask_options",
        {"ask": "Where do you live?", "type": "simple_select", "options": [{"label": "Germany", "port": "germany"}, {"label": "Other", "port": "other"}]},
        {"chosen": "<port>", "label": "<label>"},
    ),
    _type("offer", "ask", "Offer a choice", ["continue", "yes", "no"], "the customer picks one", "Kai offers a list of values and the customer picks one — usually account options from a fetch.", "offer", {"amount": 0, "can_cover": []}, {"options": [], "chosen": "<account>"}),
    _type("approve_money", "approval", "Approve a money move", ["approve", "reject"], "amount, source, and rate", "The approval card: amount, source, rate, fee and balances after. The quote re-quotes on open.", "approve_money", {"amount": 0, "from": "<account>", "to": "<account>", "quote_id": "<id>"}, {"decision": "approve | reject", "by": "<@handle>"}),
    _type("approve_next", "approval", "Approve the next step", ["approve", "reject"], "the customer agrees to what comes next", "The customer agrees to what comes next, but nothing moves here.", "approve_next", {"next": "<what comes next>"}, {"decision": "approve | reject"}),
    _type("move_money", "action", "Move money", ["continue"], "a Move between the customer's balances", "Runs a Move between the customer's own balances — at Kai's rate, or internal when the asset is the same.", "move_money", {"send": 0, "from": "<account>", "to": "<account>", "quote_id": "<id>"}, {"received": 0, "ref": "<ref>"}),
    _type("notify", "action", "Notify", ["continue"], "a message on the channels the rule names", "Sends a message on the channels the rule names — app, Kai push, email.", "notify", {"line": "<line id>", "channels": ["app", "push", "email"]}, {"sent": {"app": True, "push": True, "email": True}}),
    _type("save_rule", "action", "Save rule", ["continue"], "writes a rule the customer agreed to", "Writes a rule the customer agreed to. It shows on Live rules and in Skills.", "save_rule", {"rule": {}}, {"rule_id": "<id>"}),
    _type("start_workflow", "action", "Start workflow", ["continue"], "hands over to another workflow", "Hands over to another workflow with the context it needs.", "start_workflow", {"workflow": "<workflow id>", "inputs": {}}, {"started": True}),
    _type("schedule", "action", "Check again", ["continue"], "schedules the next check", "Schedules the next check and says nothing — keep watching.", "schedule", {"every": "1 min"}, {"next_check": "<time>", "message": None}),
    _type("done", "result", "Done", [], "the run did what it was for", "The run did what it was for.", "done", {}, {"status": "succeeded"}),
    _type("nothing", "result", "Nothing to do", [], "nothing was needed", "Nothing was needed — already there, already told, back above the level.", "nothing", {}, {"status": "no_action", "reason": "<why>"}),
    _type("not_done", "result", "Not done", [], "it could not go ahead", "It could not or should not go ahead — declined, not approved, not enough, over a limit.", "not_done", {}, {"status": "not_done", "reason": "<why>"}),
]
