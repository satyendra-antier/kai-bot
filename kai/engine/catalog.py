"""Published workflow bundles. The same RunWorkflow interprets every graph."""

from __future__ import annotations


def stub_graph(origins: list[str], done_line: str = "Done.") -> dict:
    steps = []
    edges = []
    entries = {}
    if "chat" in origins:
        steps.append(
            {
                "id": "src_chat",
                "kind": "source",
                "type": "chat_request",
                "title": "Chat",
                "line": None,
                "position": {"x": 80, "y": 40},
                "speed_target_ms": 2000,
                "config": {},
            }
        )
        entries["chat"] = "src_chat"
    if "rule" in origins:
        steps.append(
            {
                "id": "balance_rule",
                "kind": "source",
                "type": "balance_rule",
                "title": "Rule",
                "line": None,
                "position": {"x": 80, "y": 180},
                "speed_target_ms": 2000,
                "config": {},
            }
        )
        entries["rule"] = "balance_rule"
    steps.append(
        {
            "id": "done",
            "kind": "result",
            "type": "result_done",
            "title": "Done",
            "line": done_line,
            "position": {"x": 360, "y": 110},
            "speed_target_ms": 2000,
            "config": {},
        }
    )
    for entry in entries.values():
        edges.append({"id": f"{entry}:continue:done", "from": entry, "to": "done", "port": "continue"})
    return {"budget": 40, "entries": entries, "steps": steps, "edges": edges}


def bundles() -> list[dict]:
    from kai.engine.flows import bundles as money
    from kai.engine.onboarding import bundles as onboarding
    from kai.engine.topup import bundle as topup

    return [topup(), *money(), *onboarding()]


CATALOG = [
    {
        "key": "low-balance-alert",
        "name": "Low balance alert",
        "group": "",
        "channels": ["chat", "rule"],
        "summary": "Tells the customer an account has fallen below the line they set.",
    },
    {
        "key": "sweep-between-accounts",
        "name": "Sweep between accounts",
        "group": "move",
        "channels": ["chat"],
        "summary": "Moves spare money between the customer's accounts when they ask.",
    },
    {
        "key": "fx-rate-watch",
        "name": "FX rate watch",
        "group": "fiat",
        "channels": ["chat", "rule"],
        "summary": "Watches a rate and tells the customer, or acts, when it crosses their line.",
    },
    {
        "key": "jit-card-topup",
        "name": "Just-in-time card top-up",
        "group": "cards",
        "channels": ["rule"],
        "summary": "Tops up a card from another account when a rule sees the card run low.",
    },
    {
        "key": "individual-onboarding",
        "name": "Individual onboarding",
        "group": "onboarding",
        "channels": ["chat"],
        "summary": "Walks a person through opening an account.",
    },
    {
        "key": "business-onboarding",
        "name": "Business onboarding",
        "group": "onboarding",
        "channels": ["chat"],
        "summary": "Walks a business through opening an account.",
    },
]
