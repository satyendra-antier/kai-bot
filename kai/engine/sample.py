"""A short chat workflow for trying questions, answers, and a written amount."""

from __future__ import annotations

from kai.engine.graphlib import edge, finish_graph, step


def graph() -> dict:
    steps = [
        step("src_chat", "source", "chat_request", "On demand from chat", 24, 56),
        step("ask_what", "question", "", "what this sample should do", 300, 56, "What should this sample do?"),
        step("ask_amount", "question", "", "how much to move", 300, 224, "How much should I move?"),
        step("amount_given", "user_response", "", "the amount", 576, 224),
        step("balance_done", "result", "", "balance checked", 852, 56, "You asked to check a balance. This sample ends here.", result="balance"),
        step("hello_done", "result", "", "hello said", 852, 224, "Hello. This sample ends here.", result="hello"),
        step("move_done", "result", "", "amount recorded", 852, 392, "Recorded. This sample does not move money.", result="recorded"),
    ]
    steps[1]["input"] = {
        "ask": "What should this sample do?",
        "type": "simple_select",
        "options": [
            {"label": "Check a balance", "port": "check_balance"},
            {"label": "Move money", "port": "move_money"},
            {"label": "Say hello", "port": "say_hello"},
        ],
    }
    steps[2]["input"] = {
        "ask": "How much should I move?",
        "type": "free_text",
        "fields": [{"id": "amount", "ask": "How much should I move?", "type": "text"}],
    }
    edges = [
        edge("src_chat", "ask_what", "continue"),
        edge("ask_what", "balance_done", "check_balance"),
        edge("ask_what", "ask_amount", "move_money"),
        edge("ask_what", "hello_done", "say_hello"),
        edge("ask_what", "ask_amount", "continue"),
        edge("ask_amount", "amount_given", "continue"),
        edge("amount_given", "move_done", "continue"),
    ]
    entries = {"chat": "src_chat"}
    finish_graph(steps, edges, entries)
    return {"budget": 20, "entries": entries, "steps": steps, "edges": edges}


def contract() -> dict:
    return {
        "description": "A short sample. Say sample, pick one answer, and the chat follows that answer.",
        "examples": ["sample", "try the sample", "run the sample", "test the sample"],
        "exclusions": [],
        "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
        "slots": [],
        "lines": {},
        "never": [],
        "try_prompts": ["sample"],
        "preview_ledger": {"accounts": {}},
        "idle": "Say sample to try the short workflow.",
    }
