from kai.engine.tools import apply_guard, redact_pii, select_tool


def _contracts():
    return [
        {"workflow_key": "individual-onboarding", "contract": {"slots": []}, "ledger": {"rate": 1.08, "accounts": {"EUR": {"amount": 62.4, "currency": "EUR"}}}},
        {"workflow_key": "sweep-between-accounts", "contract": {"slots": [{"key": "amount"}, {"key": "source"}, {"key": "target"}]}, "ledger": {"rate": 1.08, "accounts": {}}},
    ]


def _profile(**extra):
    profile = {"account": False, "kind": "", "opted_out": False, "ledger": {"accounts": {}}, "rate": 1.08}
    profile.update(extra)
    return profile


def test_no_account_means_no_balance_and_onboarding_is_the_way_in():
    denied = apply_guard("fetch balance", {"action": "answer", "needs": "accounts", "reply": "You have €62.40"}, _profile(), _contracts())
    assert denied["needs"] == "none"
    assert "don't have an account" in denied["reply"]
    assert "€" not in denied["reply"]
    started = apply_guard("onboarding", {"action": "chat", "workflow_key": "", "needs": "none"}, _profile(), _contracts())
    assert started["action"] == "start"
    assert started["workflow_key"] == "individual-onboarding"
    new_account = apply_guard("i want new account", {"action": "answer", "workflow_key": "", "needs": "none"}, _profile(), _contracts())
    assert new_account["action"] == "start"
    assert new_account["workflow_key"] == "individual-onboarding"


def test_an_open_account_is_not_asked_to_onboard_again():
    profile = _profile(account=True, kind="individual", ledger={"accounts": {}})
    again = apply_guard("onboarding", {"action": "start", "workflow_key": "individual-onboarding"}, profile, _contracts())
    assert again["action"] == "answer"
    assert "already have" in again["reply"]
    empty = apply_guard("fetch balance", {"action": "answer", "needs": "accounts"}, profile, _contracts())
    assert "no balance" in empty["reply"]
    assert "€62.40" not in empty["reply"]


def test_money_without_an_account_is_denied_and_price_is_the_quote_on_file():
    denied = apply_guard(
        "CHF move to EUR",
        {"action": "start", "workflow_key": "sweep-between-accounts", "needs": "none"},
        _profile(),
        _contracts(),
    )
    assert "can't do that" in denied["reply"]
    quote = apply_guard("what's the rate", {"action": "chat"}, _profile(), _contracts())
    assert quote["reply"] == "The quote on file is 1.08 for EUR to USD."
    paused = apply_guard("fetch balance", {"action": "answer", "needs": "accounts"}, _profile(), _contracts(), kill_switch=True)
    assert "paused" in paused["reply"]
    assert select_tool("fetch balance") == "balance"
    assert select_tool("Yes") is None
    shown, kinds = redact_pii("my email is ada@example.com and my card is 4242424242424242")
    assert "ada@example.com" not in shown
    assert "4242424242424242" not in shown
    assert kinds == ["email", "card"]
