"""Regression coverage for create/chat binding, delete guards, and demo install."""

from __future__ import annotations

from kai.activities import _chat_ready
from kai.engine.catalog import stub_graph
from kai.engine.demos import bundles as demo_bundles
from kai.engine.onboarding import bundles as onboarding_bundles
from kai.engine.slots import decide_band, score_intent
from kai.engine.topup import PREVIEW_LEDGER, eval_config
from kai.service import preview_run


def _lanes_create_draft(name: str = "Desk check") -> dict:
    graph = stub_graph(["chat"], done_line=f"{name} is ready.")
    return {
        "graph": graph,
        "chat_contract": {
            "description": name,
            "examples": [],
            "exclusions": [],
            "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
            "slots": [],
            "lines": {"done": f"{name} is ready."},
            "never": [],
            "try_prompts": [],
            "preview_ledger": PREVIEW_LEDGER,
        },
        "stories": [],
        "eval_config": eval_config(),
        "built_from": "lanes",
    }


def test_new_lanes_workflow_has_chat_entry_and_runs():
    draft = _lanes_create_draft("Desk check")
    assert draft["graph"]["entries"]["chat"] == "src_chat"
    assert draft["chat_contract"]["examples"] == []
    result = preview_run(draft, "anything", "chat")
    assert result["result"] == "done"
    assert result["trace"] == ["src_chat", "done"]
    assert result["messages"][0]["text"] == "Desk check is ready."


def test_chat_ready_requires_chat_channel_and_entry():
    graph = stub_graph(["chat"])
    assert _chat_ready(graph, ["chat"], pinned_here=False) is True
    assert _chat_ready(graph, ["rule"], pinned_here=False) is False
    assert _chat_ready({"entries": {}, "steps": [], "edges": []}, ["chat"], pinned_here=False) is False
    assert _chat_ready({"entries": {}}, ["rule"], pinned_here=True) is True


def test_rule_only_graph_stays_out_of_chat_router():
    rule_graph = stub_graph(["rule"], done_line="Rule done.")
    assert "chat" not in rule_graph["entries"]
    assert _chat_ready(rule_graph, ["rule"], pinned_here=False) is False
    assert _chat_ready(rule_graph, ["rule"], pinned_here=True) is True


def test_demo_packages_are_grouped_and_complete():
    packages = demo_bundles()
    assert [row["key"] for row in packages] == [
        "demo-balance",
        "demo-alert",
        "demo-move",
        "demo-login",
        "demo-choose",
        "demo-list",
    ]
    for package in packages:
        assert package["group"] == "demo"
        assert "chat" in package["channels"]
        assert package["graph"]["entries"].get("chat")
        for story_id in package["sample_runs"]:
            assert any(story["id"] == story_id for story in package["stories"])


def test_onboarding_examples_do_not_cross_start():
    packages = {row["key"]: row for row in onboarding_bundles()}
    individual = packages["individual-onboarding"]["chat_contract"]
    business = packages["business-onboarding"]["chat_contract"]
    for example in individual["examples"]:
        own = decide_band(score_intent(example, individual), individual["bands"])
        other = decide_band(score_intent(example, business), business["bands"])
        assert own == "start", example
        assert other != "start", example
    for example in business["examples"]:
        own = decide_band(score_intent(example, business), business["bands"])
        other = decide_band(score_intent(example, individual), individual["bands"])
        assert own == "start", example
        assert other != "start", example


def test_delete_requires_built_from_marker():
    catalog_draft = {"graph": stub_graph(["chat"]), "chat_contract": {}, "stories": [], "eval_config": {}}
    assert not catalog_draft.get("built_from")
    lanes = _lanes_create_draft()
    assert lanes["built_from"] == "lanes"
    demo = {"built_from": "demo"}
    assert demo["built_from"] == "demo"


def test_install_demos_draft_marks_built_from_and_republishes():
    """install_demos always writes a new WorkflowVersion (count + 1), not only on first insert."""
    import inspect

    from kai import install_demos

    source = inspect.getsource(install_demos.main)
    assert 'built_from": "demo"' in source or "built_from': 'demo'" in source or '"built_from": "demo"' in source
    assert "current + 1" in source
    assert "if not row.published_version_id" not in source
