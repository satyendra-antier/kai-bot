"""Mock FX quote + ledger persistence must stay internally consistent."""

from __future__ import annotations

import copy

from kai.engine.advance import apply_move, resolve_action, resolve_fetch
from kai.engine.topup import PREVIEW_LEDGER, money


def _quote(slots: dict, ledger: dict | None = None) -> dict:
    book = copy.deepcopy(ledger or PREVIEW_LEDGER)
    return resolve_fetch({"type": "get_quote", "config": {"role": "get_move_quote"}}, slots, book)


def test_quote_source_fixed_eur_to_usd():
    """Top up USD with €100 from EUR — send €100, receive $108."""
    slots = {"source": "EUR", "target": "USD", "amount": money(100, "EUR")}
    quote = _quote(slots)
    assert quote["send"]["amount"] == 100
    assert quote["send"]["currency"] == "EUR"
    assert quote["est"]["amount"] == 108.0
    assert quote["est"]["currency"] == "USD"


def test_quote_target_fixed_usdt_to_eur():
    """Top up EUR with €65 from USDT — receive €65, debit ~70.20 USDT."""
    slots = {"source": "USDT", "target": "EUR", "amount": money(65, "EUR")}
    quote = _quote(slots)
    assert quote["est"]["amount"] == 65.0
    assert quote["est"]["currency"] == "EUR"
    assert quote["send"]["amount"] == 70.2
    assert quote["send"]["currency"] == "USDT"


def test_quote_same_asset_is_one_to_one():
    slots = {"source": "EUR", "target": "EUR", "amount": money(10, "EUR")}
    quote = _quote(slots)
    assert quote["send"]["amount"] == 10
    assert quote["est"]["amount"] == 10


def test_usdt_to_eur_target_after_and_apply_move():
    ledger = copy.deepcopy(PREVIEW_LEDGER)
    slots = {
        "source": "USDT",
        "target": "EUR",
        "amount": money(65, "EUR"),
        "target_balance": {"amount": 62.40, "currency": "EUR", "display": "€62.40"},
    }
    slots.update(_quote(slots, ledger))
    action = resolve_action(
        {"type": "move_money", "config": {"role": "move_funds"}},
        slots,
        ledger,
        "move_test",
    )
    assert action["target_after"]["amount"] == 127.4
    apply_move(ledger, slots)
    assert ledger["accounts"]["EUR"]["amount"] == 127.4
    assert ledger["accounts"]["USDT"]["amount"] == 879.8  # 950 - 70.2


def test_logged_in_profile_replays_completed_moves(monkeypatch):
    from kai import activities
    from kai.engine.topup import PREVIEW_LEDGER

    class _Row:
        def __init__(self, slots):
            self.slots = slots

    class _Query:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

    class _Session:
        def query(self, model):
            return _Query(
                [
                    _Row(
                        {
                            "source": "USDT",
                            "target": "EUR",
                            "amount": money(65, "EUR"),
                            "send": {"amount": 70.2, "currency": "USDT"},
                            "est": {"amount": 65.0, "currency": "EUR"},
                        }
                    )
                ]
            )

    class _Scope:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(activities, "session_scope", lambda: _Scope())
    profile = activities._chat_profile("conv-ledger", persona="logged_in", customer_id="cust-amira")
    assert profile["account"] is True
    assert profile["ledger"]["accounts"]["EUR"]["amount"] == 127.4
    assert profile["ledger"]["accounts"]["USDT"]["amount"] == 879.8
    # Fresh preview must not be returned unchanged.
    assert profile["ledger"]["accounts"]["EUR"]["amount"] != PREVIEW_LEDGER["accounts"]["EUR"]["amount"]


def test_booked_ledger_activity_applies_moves(monkeypatch):
    from kai import activities
    from kai.engine.topup import PREVIEW_LEDGER

    class _Row:
        def __init__(self, slots):
            self.slots = slots

    class _Query:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

    class _Session:
        def query(self, model):
            return _Query(
                [
                    _Row(
                        {
                            "source": "EUR",
                            "target": "USD",
                            "amount": money(100, "EUR"),
                            "send": {"amount": 100, "currency": "EUR"},
                            "est": {"amount": 108, "currency": "USD"},
                        }
                    )
                ]
            )

    class _Scope:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(activities, "session_scope", lambda: _Scope())
    out = activities.booked_ledger_activity(
        {"conversation_id": "c1", "ledger": PREVIEW_LEDGER, "contracts": [{"ledger": PREVIEW_LEDGER}]}
    )
    ledger = out["ledger"]
    assert abs(ledger["accounts"]["EUR"]["amount"] - (62.40 - 100)) < 0.001
    assert ledger["accounts"]["USD"]["amount"] == 120.0  # 12 + 108
