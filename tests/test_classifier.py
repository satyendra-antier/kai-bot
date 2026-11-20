"""Intent classifier: natural asks must hit the matching backoffice workflow."""

from __future__ import annotations

from kai.engine.catalog import bundles as catalog_bundles
from kai.engine.demos import bundles as demo_bundles
from kai.engine.onboarding import bundles as onboarding_bundles
from kai.engine.slots import extract_slots, fallback_turn, score_intent
from kai.service import story_failures


def _contracts() -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    for pack in list(catalog_bundles()) + list(demo_bundles()) + list(onboarding_bundles()):
        key = pack["key"]
        if key in seen:
            continue
        seen.add(key)
        contract = pack.get("chat_contract") or {}
        if "chat" not in (pack.get("channels") or []):
            continue
        rows.append(
            {
                "workflow_key": key,
                "workflow_id": key,
                "contract": contract,
                "ledger": contract.get("preview_ledger") or {"accounts": {}},
            }
        )
    return rows


EXPECTED = [
    ("tell me when my account goes under 50 EUR", "start", "low-balance-alert"),
    ("tell me when it goes up to 65", "start", "low-balance-alert"),
    ("Let me know if my GBP account goes under £50", "start", "low-balance-alert"),
    ("Tell me when my card drops below €20", "start", "low-balance-alert"),
    ("demo alert", "start", "demo-alert"),
    ("Tell me when EUR/USD goes above 1.10", "start", "fx-rate-watch"),
    ("watch the EUR USD rate and notify me at 1.12", "start", "fx-rate-watch"),
    ("top up my USD with €100 from EUR", "start", "low-balance-top-up"),
    ("top up my account", "start", "low-balance-top-up"),
    ("Move €50 from EUR to USDT", "start", "sweep-between-accounts"),
    ("move 10 EUR to USD", "start", "sweep-between-accounts"),
    ("demo move", "start", "demo-move"),
    ("sweep between my accounts", "start", "sweep-between-accounts"),
    ("demo balance", "start", "demo-balance"),
    ("check EUR", "start", "demo-balance"),
    ("demo login", "start", "demo-login"),
    ("demo choose", "start", "demo-choose"),
    ("demo list", "start", "demo-list"),
    ("I want to open a personal account", "start", "individual-onboarding"),
    ("onboarding", "start", "individual-onboarding"),
    ("business onboarding", "start", "business-onboarding"),
    ("I want to open a business account", "start", "business-onboarding"),
    ("send 50 to @dana", "chat", ""),
    ("Hi", "chat", ""),
    ("asdfghjkl", "chat", ""),
]


def test_classifier_routes_natural_asks_to_backoffice_workflows():
    contracts = _contracts()
    for text, action, key in EXPECTED:
        decision = fallback_turn(text, contracts, {"phase": "idle"})
        assert decision["action"] == action, (text, decision)
        assert (decision.get("workflow_key") or "") == key, (text, decision)


def test_check_balance_does_not_start_a_demo():
    contracts = _contracts()
    decision = fallback_turn("check balance", contracts, {"phase": "idle"})
    assert decision.get("workflow_key") != "demo-balance"
    assert decision["action"] in {"chat", "answer"}


def test_list_my_card_is_an_account_answer_not_demo_list():
    contracts = _contracts()
    decision = fallback_turn("list my card", contracts, {"phase": "idle"})
    assert decision["action"] == "answer"
    assert decision.get("workflow_key") != "demo-list"
    assert decision.get("needs") == "cards"


def test_move_and_fx_slot_fills():
    move = extract_slots("move 10 EUR to USD")
    assert move["source"]["value"] == "EUR"
    assert move["target"]["value"] == "USD"
    assert move["amount"]["value"]["amount"] == 10
    fx = extract_slots("watch the EUR USD rate and notify me at 1.12")
    assert fx["pair"]["value"] == "EUR/USD"
    assert fx["target_rate"]["value"] == 1.12


def test_demo_examples_do_not_start_sibling_demos():
    demos = {item["key"]: item for item in demo_bundles()}
    for key, pack in demos.items():
        for example in pack["chat_contract"]["examples"]:
            own = score_intent(example, pack["chat_contract"], workflow_key=key)
            assert own >= 0.75, (key, example, own)
            for other_key, other in demos.items():
                if other_key == key:
                    continue
                score = score_intent(example, other["chat_contract"], workflow_key=other_key)
                assert score < 0.75, (example, other_key, score)


def test_every_packaged_workflow_stories_pass():
    seen: set[str] = set()
    for pack in list(catalog_bundles()) + list(demo_bundles()) + list(onboarding_bundles()):
        if pack["key"] in seen:
            continue
        seen.add(pack["key"])
        failed = story_failures(pack["graph"], pack.get("stories") or [])
        assert not failed, (pack["key"], failed)
