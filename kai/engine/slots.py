"""Deterministic slot fill and intent score for the seeded contracts.

Gemini replaces these when GEMINI_API_KEY is set. The patterns below cover the
fill hints: target after top up / refill / add to, an amount with a currency,
and the account after from.
"""

from __future__ import annotations

import re
from typing import Any

from kai.engine.advance import format_money

CURRENCIES = {
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "usdt": "USDT",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "chf": "CHF",
    "card": "CARD",
}
SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}
CODES = set(CURRENCIES.values())


def _code(token: str) -> str | None:
    cleaned = token.strip(" .,").lower()
    if cleaned in CURRENCIES:
        return CURRENCIES[cleaned]
    upper = token.strip(" .,").upper()
    if upper in CODES:
        return upper
    return None


def parse_amount(text: str, target: str | None) -> dict[str, Any] | None:
    match = re.search(
        r"([€$£])\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*([€$£])|(\d+(?:\.\d+)?)\s*(eur|euros?|usd|dollars?|usdt|gbp|pounds?|chf)?",
        text,
        re.I,
    )
    if not match:
        return None
    if match.group(1):
        currency = SYMBOLS[match.group(1)]
        number = float(match.group(2))
    elif match.group(4):
        number = float(match.group(3))
        currency = SYMBOLS[match.group(4)]
    else:
        number = float(match.group(5))
        word = match.group(6)
        currency = _code(word) if word else None
        if currency is None:
            if not target:
                return None
            currency = "USD" if target == "CARD" else target
    if number <= 0:
        return None
    return {"amount": number, "currency": currency, "display": format_money(number, currency)}


def parse_pair(text: str) -> str | None:
    """EUR/USD, eur-usd, EUR USD, EUR to USD, or EURUSD — never a money move."""
    lowered = " ".join((text or "").lower().split())
    slash = re.search(r"\b([a-z]{3})\s*[/\-]\s*([a-z]{3})\b", lowered)
    if slash:
        base, quote = _code(slash.group(1)), _code(slash.group(2))
        if base and quote and "CARD" not in {base, quote}:
            return f"{base}/{quote}"
    money_move = bool(re.search(r"\b(move|send|transfer|top\s*up|refill|add)\b", lowered))
    if not money_move:
        to_form = re.search(r"\b([a-z]{3})\s+to\s+([a-z]{3})\b", lowered)
        if to_form:
            base, quote = _code(to_form.group(1)), _code(to_form.group(2))
            if base and quote and "CARD" not in {base, quote}:
                return f"{base}/{quote}"
        # "EUR USD rate" / watch notify — spaced pair without slash.
        if re.search(r"\b(rate|pair|watch|notify|exchange|fx|goes?\s+above|goes?\s+below)\b", lowered) or wants_alert(lowered):
            spaced = re.search(
                r"\b(eur|euro|euros|usd|dollar|dollars|usdt|gbp|pound|pounds|chf)\s+"
                r"(eur|euro|euros|usd|dollar|dollars|usdt|gbp|pound|pounds|chf)\b",
                lowered,
            )
            if spaced:
                base, quote = _code(spaced.group(1)), _code(spaced.group(2))
                if base and quote and base != quote and "CARD" not in {base, quote}:
                    return f"{base}/{quote}"
    glued = re.search(r"\b([a-z]{6})\b", lowered)
    if glued:
        raw = glued.group(1)
        base, quote = _code(raw[:3]), _code(raw[3:])
        if base and quote and "CARD" not in {base, quote}:
            return f"{base}/{quote}"
    return None


def parse_target_rate(text: str) -> float | None:
    lowered = " ".join((text or "").lower().split())
    match = re.search(r"(?:above|below|over|under|at|reaches?)\s+(\d+(?:\.\d+)?)\b", lowered)
    if match:
        return float(match.group(1))
    bare = re.fullmatch(r"(\d+\.\d+)", lowered)
    if bare:
        return float(bare.group(1))
    if parse_pair(text):
        trailing = re.search(r"(\d+(?:\.\d+)?)\s*$", lowered)
        if trailing:
            return float(trailing.group(1))
    return None


def parse_threshold(text: str) -> float | None:
    lowered = " ".join((text or "").lower().split())
    match = re.search(
        r"(?:under|below|drops?\s+below|goes?\s+under|falls?\s+below|"
        r"above|over|up\s+to|goes?\s+up\s+to|goes?\s+above|reaches?)\s*[€$£]?\s*(\d+(?:\.\d+)?)",
        lowered,
    )
    if match:
        return float(match.group(1))
    money = re.search(r"[€$£]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*[€$£]", text or "")
    if money:
        return float(money.group(1) or money.group(2))
    bare = re.fullmatch(r"(\d+(?:\.\d+)?)", lowered)
    if bare:
        return float(bare.group(1))
    return None


def parse_account(text: str) -> str | None:
    lowered = " ".join((text or "").lower().split())
    if re.search(r"\bmy\s+card\b|\bthe\s+card\b|\bcards?\b", lowered) and not re.search(r"\b(account|wallet)\b", lowered):
        if not _currency_codes(lowered):
            return "CARD"
    named = re.search(
        r"\b(?:my\s+)?(card|[a-z]{3,5})\s+(?:account|wallet|balance|card)\b|"
        r"\b(?:account|wallet|card)\s+(?:is\s+)?(card|eur|euro|euros|usd|dollar|dollars|usdt|gbp|pound|pounds|chf)\b",
        lowered,
    )
    if named:
        token = named.group(1) or named.group(2)
        if token == "card":
            return "CARD"
        code = _code(token)
        if code:
            return code
    if re.fullmatch(r"(card|my card)", lowered):
        return "CARD"
    codes = _currency_codes(lowered)
    if len(codes) == 1:
        return codes[0]
    if re.fullmatch(r"(card|[a-z]{3,5})", lowered):
        return "CARD" if lowered == "card" else _code(lowered)
    return None


def extract_slots(text: str) -> dict[str, dict[str, Any]]:
    """Return slot key -> {value, from} for whatever the message itself states."""

    found: dict[str, dict[str, Any]] = {}
    lowered = re.sub(r"\btopup\b", "top up", text.lower())
    pair = parse_pair(text)
    if pair:
        found["pair"] = {"value": pair, "from": "the message"}
        rate = parse_target_rate(text)
        if rate is not None:
            found["target_rate"] = {"value": rate, "from": "the message"}
        if re.search(r"\b(below|under)\b", lowered):
            found["direction"] = {"value": "below", "from": "the message"}
        else:
            found["direction"] = {"value": "above", "from": "the message"}
        return found

    if wants_alert(lowered):
        account = parse_account(text)
        if account:
            found["account"] = {"value": account, "from": "the message"}
            found["target"] = {"value": account, "from": "the message"}
        threshold = parse_threshold(text)
        if threshold is not None:
            found["threshold"] = {"value": threshold, "from": "the message"}
        if re.search(r"\b(above|over|up\s+to|goes?\s+up\s+to|goes?\s+above|reaches?)\b", lowered) and not re.search(
            r"\b(under|below)\b", lowered
        ):
            found["direction"] = {"value": "above", "from": "the message"}
        elif re.search(r"\b(under|below)\b", lowered):
            found["direction"] = {"value": "below", "from": "the message"}
        if found:
            return found

    target = None
    target_match = re.search(r"\b(?:top up|refill|add)\b(?:\s+my)?\s+(card|[a-z]{3,5})\b", lowered)
    leading = re.search(r"\b(card|[a-z]{3,5})\b(?:\s+\w+){0,3}\s+top up\b", lowered)
    add_to = re.search(r"\badd\b\s+\d+(?:\.\d+)?\s+to\s+(?:my\s+)?([a-z]{3,5})", lowered)
    if target_match:
        target = "CARD" if target_match.group(1) == "card" else _code(target_match.group(1))
    elif leading:
        target = "CARD" if leading.group(1) == "card" else _code(leading.group(1))
    elif add_to:
        target = _code(add_to.group(1))
    else:
        named = re.search(r"\b(?:check|balance)\s+(?:my\s+)?([a-z]{3,5})\b", lowered)
        if named:
            target = _code(named.group(1))
    if not target and re.search(r"\b(top\s*up|refill|add)\b", lowered) and ("dollar" in lowered or re.search(r"\busd\b", lowered)):
        target = "USD"
    if target:
        found["target"] = {"value": target, "from": "the message"}

    named_move = re.search(r"\b([a-z]{3,5})\s+move\s+to\s+([a-z]{3,5})\b", lowered)
    plain_move = re.search(r"\bmove\s+([a-z]{3,5})\s+to\s+([a-z]{3,5})\b", lowered)
    move = named_move or plain_move
    if move:
        source = _code(move.group(1))
        target = _code(move.group(2))
        if source and source != "CARD":
            found["source"] = {"value": source, "from": "the message"}
        if target:
            found["target"] = {"value": target, "from": "the message"}
    source_match = re.search(r"\bfrom\s+(?:my\s+)?([a-z]{3,6})\b", lowered)
    if source_match:
        source = _code(source_match.group(1))
        if source and source != "CARD":
            found["source"] = {"value": source, "from": "the message"}
    if "target" not in found:
        to_match = re.search(r"\bto\s+(?:my\s+)?([a-z]{3,5})\b", lowered)
        if to_match and not re.search(r"\b(goes?|above|below|under)\b", lowered):
            target = _code(to_match.group(1))
            if target:
                found["target"] = {"value": target, "from": "the message"}
    if "source" not in found and re.search(r"\bfrom\b", lowered):
        named = _currency_codes(lowered)
        if named:
            found["source"] = {"value": named[-1], "from": "the message"}

    amount_hint = (found.get("source") or {}).get("value") or (found.get("target") or {}).get("value") or target
    amount = parse_amount(text, amount_hint)
    if amount and re.search(r"\d", text) and re.search(r"\b(top\s*up|refill|add|move|send|transfer|from|with)\b", lowered):
        found["amount"] = {"value": amount, "from": "the message"}
    elif amount and re.search(r"[€$£]", text):
        found["amount"] = {"value": amount, "from": "the message"}
    amount = (found.get("amount") or {}).get("value") or {}
    source = (found.get("source") or {}).get("value")
    if amount and source and re.search(r"\b(move|send)\b", lowered) and not re.search(r"[€$£]", text):
        glued = re.search(r"(\d+(?:\.\d+)?)\s*(eur|euros?|usd|dollars?|usdt|gbp|pounds?|chf)\b", lowered)
        if not glued:
            number = amount["amount"]
            found["amount"]["value"] = {"amount": number, "currency": source, "display": format_money(number, source)}
    # "move 10 EUR to USD" — currency on the amount is the source.
    if re.search(r"\b(move|send|transfer)\b", lowered) and "source" not in found and isinstance(amount, dict):
        ccy = amount.get("currency")
        target = (found.get("target") or {}).get("value")
        if ccy and ccy != "CARD" and ccy != target:
            found["source"] = {"value": ccy, "from": "the message"}
    return found


def wants_alert(text: str) -> bool:
    """True when the customer is asking to be told later, not to see balances now."""
    lowered = " ".join((text or "").lower().split())
    if re.search(r"\b(warn|notify|alert|watch)\b", lowered):
        return True
    if re.search(r"\b(tell me when|let me know (if|when)|notify me (if|when)|warn me (if|when))\b", lowered):
        return True
    if re.search(
        r"\b(goes?\s+under|falls?\s+below|drops?\s+below|goes?\s+below|"
        r"goes?\s+above|goes?\s+up\s+to|rises?\s+above|goes?\s+over)\b",
        lowered,
    ):
        return True
    if re.search(r"\b(under|below|above|over|up\s+to)\s*[€$£]?\s*\d", lowered):
        return True
    return False


def asks_for_accounts(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    if re.search(r"\b(top\s*up|topup|top-up|refill)\b", lowered):
        return False
    if wants_alert(lowered):
        return False
    # "check balance" / "demo balance" are tools or demos — not an account listing.
    if re.search(r"\b(check\s+)?balance\b", lowered) and not re.search(r"\b(list|show|what|which|available)\b", lowered):
        return False
    listing = re.search(r"\b(list|show|see|what|which|available|availability|options?)\b", lowered)
    about = re.search(r"\b(accounts?|cards?|balances?|available|availability|options?|have)\b", lowered)
    return bool(listing and about)


def describe_accounts(ledger: dict[str, Any], text: str = "") -> str:
    accounts = (ledger or {}).get("accounts") or {}
    lowered = text.lower()
    cards_only = bool(re.search(r"\bcards?\b", lowered)) and not re.search(r"\baccounts?\b", lowered)
    parts = []
    for code, row in accounts.items():
        kind = str((row or {}).get("kind") or "account")
        if cards_only and kind != "card" and str(code).upper() != "CARD":
            continue
        shown = (row or {}).get("display")
        if not shown:
            shown = format_money(float((row or {}).get("amount") or 0), str((row or {}).get("currency") or code))
        shown = str(shown)
        parts.append(shown if code.upper() in shown.upper() else f"{code} {shown}")
    if not parts:
        return "I don't have a card on file." if cards_only else "I don't have a list of accounts yet."
    if cards_only:
        lead = "Your card is" if len(parts) == 1 else "Your cards are"
        return f"{lead} " + ", ".join(parts) + "."
    return "You have " + ", ".join(parts) + "."


def greeting_reply(text: str) -> str | None:
    lowered = re.sub(r"[^a-z\s']", "", text.lower()).strip()
    if lowered in {"hi", "hello", "hey", "hiya", "good morning", "good afternoon", "good evening"} or re.fullmatch(r"h+i+", lowered):
        return "Hi. I can top up an account, set a balance rule, or run a sweep. What would you like to do?"
    return None


def whole_balance(text: str, source: str, ledger: dict[str, Any]) -> dict[str, Any] | None:
    """'all' while an amount is missing means the whole source balance."""
    lowered = " ".join(text.lower().split())
    if lowered not in {"all", "all of it", "everything", "the lot", "entire balance"}:
        return None
    row = ((ledger or {}).get("accounts") or {}).get(source) or {}
    number = float(row.get("amount") or 0)
    if number <= 0:
        return None
    currency = str(row.get("currency") or source)
    return {"amount": number, "currency": currency, "display": row.get("display") or format_money(number, currency)}


def fill_reply(key: str, text: str, target: str | None) -> Any:
    cleaned = text.strip()
    if key == "amount":
        return parse_amount(cleaned, target if target != "CARD" else "USD")
    if key == "pair":
        return parse_pair(cleaned)
    if key == "target_rate":
        return parse_target_rate(cleaned)
    if key == "threshold":
        return parse_threshold(cleaned)
    if key == "account":
        return parse_account(cleaned)
    if key == "target" and cleaned.lower() in {"card", "my card"}:
        return "CARD"
    return _code(cleaned.split()[-1]) or _code(cleaned)


def missing_slot(order: list[dict[str, Any]], filled: dict[str, Any]) -> dict[str, Any] | None:
    """Ask before the graph only for a slot that says how to fill it. A blank fill rule stays on the contract and does not block the steps."""
    for slot in sorted(order, key=lambda item: item.get("order") or 0):
        if not str(slot.get("fill_from") or "").strip():
            continue
        if slot["key"] not in filled or filled[slot["key"]] in (None, ""):
            return slot
    return None


def slot_question(key: str, slots: list[dict[str, Any]] | None = None) -> str:
    for slot in slots or []:
        if slot.get("key") == key and slot.get("ask"):
            return str(slot["ask"])
    return {
        "target": "Which account or card should I top up?",
        "amount": "How much should I move?",
        "source": "Which account should I take it from?",
        "account": "Which account should I watch?",
        "threshold": "What level should I watch for?",
        "pair": "Which currency pair should I watch?",
        "target_rate": "What rate should I tell you about?",
    }.get(key, f"What should I use for {key}?")


def idle_line(contracts: list[dict[str, Any]] | None) -> str:
    for item in contracts or []:
        contract = item.get("contract") if isinstance(item, dict) and isinstance(item.get("contract"), dict) else item
        if isinstance(contract, dict) and contract.get("idle"):
            return str(contract["idle"])
    return HELP


def sync_try_prompts(contract: dict[str, Any]) -> dict[str, Any]:
    """Keep Try-it chips aligned with classifier examples (plus exclusions for negatives)."""
    examples = [str(item) for item in (contract.get("examples") or []) if str(item).strip()]
    exclusions = [str(item) for item in (contract.get("exclusions") or []) if str(item).strip()]
    prompts: list[str] = []
    for item in examples + exclusions:
        if item not in prompts:
            prompts.append(item)
    contract["try_prompts"] = prompts
    return contract


def score_intent(text: str, contract: dict[str, Any], workflow_key: str = "") -> float:
    lowered = " ".join(text.lower().split())
    described = (contract.get("description") or "").lower()
    key = str(workflow_key or "")
    is_demo = key.startswith("demo-")
    for exclusion in contract.get("exclusions") or []:
        sample = exclusion.lower().strip()
        # Utterance must contain the exclusion phrase — not the other way around
        # ("onboarding" must not match exclusion "business onboarding").
        if sample and sample in lowered:
            return 0.2
    examples = [example.lower() for example in contract.get("examples") or []]
    if lowered in examples:
        return 0.97
    tokens = set(re.findall(r"[a-z€$£]+|\d+", lowered))
    best = 0.0
    for example in examples:
        example_tokens = set(re.findall(r"[a-z€$£]+|\d+", example))
        if not example_tokens:
            continue
        overlap = len(tokens & example_tokens) / len(example_tokens)
        best = max(best, overlap)
    # Demo scaffolds only start from their own examples — never steal product intents.
    if is_demo:
        if any(example.startswith("demo") and (example in lowered or lowered in example) for example in examples):
            return 0.97 if lowered in examples else max(best, 0.9)
        # Exact non-demo examples like "check EUR" still start that demo.
        if lowered in examples:
            return 0.97
        demo_label = key.replace("demo-", "").replace("-", " ")
        if "demo" not in lowered:
            return round(min(best, 0.45), 2)
        if demo_label and demo_label.split()[0] not in lowered:
            return round(min(best, 0.4), 2)
        return round(best, 2)
    top_up_words = any(word in lowered for word in ("top up", "topup", "refill")) or "top-up" in lowered
    add_money = bool(re.search(r"\badd\b", lowered) and any(code.lower() in lowered for code in CODES))
    sweep_ask = bool(
        re.search(r"\bmove\b.+\bfrom\b.+\bto\b", lowered)
        or re.search(r"\bmove\b.+\bto\b", lowered)
        or re.search(r"\bsweep\b", lowered)
        or (re.search(r"\btransfer\b", lowered) and re.search(r"\b(from|to)\b", lowered))
    )
    alert_ask = wants_alert(lowered)
    top_up_contract = any(word in described for word in ("top it up", "refill", "add a sum", "topped up"))
    sweep_contract = "move money" in described and "top it up" not in described
    named_money = bool(_currency_codes(lowered) or re.search(r"[€$£]|\d", lowered) or re.search(r"\bcard\b", lowered))
    if not alert_ask and (top_up_words or add_money) and top_up_contract:
        best = max(best, 0.8 if named_money else 0.6)
    if not alert_ask and not top_up_words and sweep_ask and sweep_contract:
        best = max(best, 0.86 if (re.search(r"\b(from|to)\b", lowered) or named_money) else 0.6)
    if alert_ask:
        pair = parse_pair(text)
        rate_ask = bool(
            pair
            or re.search(r"\b(rate|pair|eur/usd|usd/eur|exchange|fx)\b", lowered)
            or "/" in lowered
        )
        rate_contract = "exchange rate" in described or "rate reaches" in described
        balance_contract = (
            any(word in described for word in ("be told", "told when", "falls below", "below a level", "goes under"))
            and "exchange rate" not in described
            and any(word in described for word in ("account", "wallet", "card", "balance", "under 50"))
        )
        has_level = bool(
            re.search(r"\b(under|below|above|over|up\s+to)\b", lowered)
            or re.search(r"\d", lowered)
            or _currency_codes(lowered)
        )
        if rate_ask and rate_contract and (pair or re.search(r"\b(exchange|pair|rate|fx|/)\b", lowered)):
            best = max(best, 0.86 if (pair or re.search(r"\d", lowered)) else 0.6)
        elif not rate_ask and balance_contract:
            best = max(best, 0.86 if has_level else 0.55)
        elif top_up_contract or sweep_contract:
            best = min(best, 0.35)
    # Onboarding: keep personal vs business from colliding on the shared word "onboarding".
    if key == "business-onboarding":
        if re.search(r"\b(business|company)\b", lowered):
            best = max(best, 0.9)
        elif re.search(r"\b(personal|myself|individual)\b", lowered):
            best = min(best, 0.2)
    if key == "individual-onboarding":
        if re.search(r"\b(business|company)\b", lowered) and not re.search(r"\b(personal|myself|individual)\b", lowered):
            best = min(best, 0.2)
        elif re.search(r"\b(personal|myself|individual)\b", lowered) or re.search(
            r"\bopen (an |a )?account\b", lowered
        ):
            best = max(best, 0.86)
        elif re.fullmatch(r"onboarding", lowered):
            best = max(best, 0.8)
    return round(best, 2)


def decide_band(score: float, bands: dict[str, float]) -> str:
    not_this = float(bands.get("not_this_below", 0.5))
    ask_at = float(bands.get("ask_at", 0.5))
    start_at = float(bands.get("start_at", 0.75))
    if score < not_this:
        return "not_this"
    if score < start_at and score >= ask_at:
        return "ask"
    if score >= start_at:
        return "start"
    return "not_this"


HELP = "I can help with topping up an account, a balance rule, or a sweep. Tell me what you want to do."


def _currency_codes(lowered: str) -> list[str]:
    found = []
    for word in re.findall(r"[a-z]+", lowered):
        code = CURRENCIES.get(word)
        if code and code != "CARD" and code not in found:
            found.append(code)
    return found


def mentions_code(text: str, code: str) -> bool:
    lowered = text.lower()
    if code.lower() in lowered:
        return True
    if code == "CARD" and re.search(r"\bcards?\b", lowered):
        return True
    return any(re.search(rf"\b{word}\b", lowered) for word, mapped in CURRENCIES.items() if mapped == code)


def looks_like_question(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    if wants_alert(lowered):
        return False
    # Interrogatives win — "how much can I move?" is a question, not a move start.
    if re.match(r"(what|which|who|how|show|list|do i|can you|could you|tell me)\b", lowered) or "?" in lowered:
        return True
    if re.search(r"\b(top\s*up|topup|top-up|refill|move|send|add)\b", lowered):
        return False
    return False


def asks_capacity(text: str) -> bool:
    """True when the customer asks how much they can move/use from a balance."""
    lowered = " ".join((text or "").lower().split())
    if re.search(r"\bhow much (can|could) i\b", lowered):
        return True
    if re.search(r"\bhow much .{0,24}\b(move|send|transfer|top\s*up|take)\b", lowered):
        return True
    if re.search(r"\b(can i move|could i move|max(imum)? (i can )?move|all i can move)\b", lowered):
        return True
    if re.search(r"\b(how much (is )?(spare|left)|what('?s| is) the (max|maximum|most|limit))\b", lowered):
        return True
    return False


def describe_capacity(ledger: dict[str, Any], filled: dict[str, Any] | None = None, text: str = "") -> str:
    """Say what one relevant account can spare — used mid-slot when they ask how much they can move."""
    accounts = (ledger or {}).get("accounts") or {}
    filled = filled or {}
    named = parse_account(text) or ""
    preferred = []
    for key in ("source", "account", "target"):
        value = filled.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str) and value:
            preferred.append(value.upper())
    if named:
        preferred.insert(0, named.upper())
    codes = preferred or [str(code).upper() for code in accounts]
    parts = []
    seen: set[str] = set()
    for code in codes:
        if code in seen or code not in {str(k).upper() for k in accounts}:
            continue
        seen.add(code)
        row = accounts.get(code) or next((accounts[k] for k in accounts if str(k).upper() == code), {}) or {}
        amount = float(row.get("amount") or 0)
        floor = float(row.get("floor") or 0)
        spare = max(0.0, amount - floor)
        currency = str(row.get("currency") or code)
        shown = row.get("display") or format_money(amount, currency)
        spare_shown = format_money(spare, currency)
        if floor > 0:
            parts.append(f"{code} has {shown} (you can move up to {spare_shown} above its floor)")
        else:
            parts.append(f"{code} has {shown} — you can move up to {spare_shown}")
        if named or preferred:
            break
    if not parts:
        return describe_accounts(ledger, text or "list accounts")
    if len(parts) == 1:
        return parts[0] + "."
    return "You have " + "; ".join(parts) + "."


def is_port_reply(text: str) -> str | None:
    """An exact port. Labels and synonyms belong on the step, not here."""
    lowered = " ".join(text.lower().split())
    if lowered in {"yes", "no", "approve", "reject", "all"}:
        return lowered
    return None


def _excluded(text: str, contract: dict[str, Any]) -> bool:
    lowered = " ".join(text.lower().split())
    for exclusion in contract.get("exclusions") or []:
        sample = exclusion.lower().strip()
        if sample and sample in lowered:
            return True
    return False


def _best_contract(text: str, contracts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for item in contracts:
        key = str(item.get("workflow_key") or "")
        found = score_intent(text, item.get("contract") or {}, workflow_key=key)
        # Prefer product workflows over demo-* when scores tie.
        product_boost = 0 if key.startswith("demo-") else 1
        ranked.append((found, product_boost, item))
    if not ranked:
        return None, 0.0
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    score, _, best = ranked[0]
    return best, score


def _ledger(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {"accounts": {}}
    return item.get("ledger") or (item.get("contract") or {}).get("preview_ledger") or {"accounts": {}}


def _needs_for(text: str) -> str:
    lowered = text.lower()
    if wants_alert(lowered):
        return "none"
    if asks_capacity(lowered):
        return "capacity"
    if re.search(r"\bcards?\b", lowered) and not re.search(r"\baccounts?\b", lowered):
        return "cards"
    if asks_for_accounts(text) or re.search(r"\b(balances?|available|accounts?|cards?)\b", lowered):
        return "accounts"
    return "none"


def _clean_slots(raw: Any, text: str, ledger: dict[str, Any], pending: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raw = {}
    known = {str(code).upper() for code in (ledger.get("accounts") or {})} | set(CODES)
    found: dict[str, dict[str, Any]] = {}
    if not looks_like_question(text):
        for key, item in extract_slots(text).items():
            found[key] = item
    if "amount" in raw and "amount" not in found and re.search(r"\d", text):
        parsed = parse_amount(text, None)
        if parsed:
            found["amount"] = {"value": parsed, "from": "the message"}
    for key, value in raw.items():
        if key in found:
            continue
        scalar = value.get("value") if isinstance(value, dict) else value
        if key == "pair":
            pair = parse_pair(str(scalar or "")) or parse_pair(text)
            if pair:
                found["pair"] = {"value": pair, "from": "the message"}
            continue
        if key in {"target_rate", "threshold"}:
            try:
                number = float(scalar)
            except (TypeError, ValueError):
                number = parse_target_rate(text) if key == "target_rate" else parse_threshold(text)
            if number is not None and float(number) > 0:
                found[key] = {"value": float(number), "from": "the message"}
            continue
        code = _code(str(scalar or ""))
        if not code or code not in known:
            continue
        if mentions_code(text, code) or (key == pending and mentions_code(text, code)):
            found[str(key)] = {"value": code, "from": "the message"}
    return found


def _slots_for_contract(slots: dict[str, dict[str, Any]], contract: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Keep only keys this workflow declares — stop FX fills becoming target/amount."""
    allowed = {str(slot.get("key") or "") for slot in (contract or {}).get("slots") or [] if slot.get("key")}
    if not allowed:
        return slots
    return {key: value for key, value in slots.items() if key in allowed}


def pinned_turn(text: str, contracts: list[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A chat opened on one workflow starts that workflow, draft and all."""
    state = state or {}
    opened = state.get("opened_workflow_key") or ""
    phase = state.get("phase") or "idle"
    if not opened or phase not in {"idle", "", None} or state.get("pending_slot"):
        return None
    match = next((item for item in contracts if item.get("workflow_key") == opened), None)
    if match is None:
        return None
    slots = _clean_slots({}, text, _ledger(match), "")
    return _decision("start", opened, 1, slots, "none", "", "pinned")


def fallback_turn(text: str, contracts: list[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Decide from the loaded chat contracts when the model is unavailable."""
    state = state or {}
    phase = state.get("phase") or "idle"
    pending = state.get("pending_slot") or ""
    current = next((item for item in contracts if item.get("workflow_key") == state.get("workflow_key")), None)
    best, score = _best_contract(text, contracts)
    greeting = greeting_reply(text)
    if greeting and phase in {"idle", ""} and not pending:
        return _decision("chat", "", 0, {}, "none", greeting, "deterministic")
    if looks_like_question(text) or asks_for_accounts(text) or asks_capacity(text):
        owner = current or best
        key = (owner or {}).get("workflow_key") or ""
        # Don't hang a weak demo match onto a listing/tool-style question.
        if str(key).startswith("demo-") and "demo" not in text.lower() and score < 0.75:
            key = ""
        needs = _needs_for(text)
        if needs == "none" and re.search(r"\b(fund|funding|use|pay|cover|take|available|options?)\b", text.lower()):
            needs = "accounts"
        if phase == "slot" and pending and needs == "none":
            needs = "capacity" if asks_capacity(text) else "accounts"
        return _decision("answer", key, max(score, 0), {}, needs, "", "deterministic")
    owner = current or best
    slots = _clean_slots({}, text, _ledger(owner), pending)
    slots = _slots_for_contract(slots, (owner or {}).get("contract"))
    if phase == "slot" and pending and not looks_like_question(text) and not asks_capacity(text):
        filled = state.get("filled") or {}
        source = filled.get("source") if isinstance(filled.get("source"), str) else ""
        target = filled.get("target") if isinstance(filled.get("target"), str) else ""
        named = _currency_codes(text.lower())
        if pending == "amount" and not re.search(r"[€$£]|\b(eur|euros?|usd|dollars?|usdt|gbp|pounds?|chf)\b", text.lower()):
            whole = whole_balance(text, source, _ledger(current or best))
            if whole:
                slots["amount"] = {"value": whole, "from": "asked"}
            else:
                value = fill_reply("amount", text, source or target or None)
                if isinstance(value, dict):
                    slots["amount"] = {"value": value, "from": "asked"}
        if pending in slots:
            named = []
        if pending in {"target", "source", "account"} and len(named) == 1:
            slots[pending] = {"value": named[0], "from": "asked"}
        elif pending not in slots:
            hint = (source or target) if pending == "amount" else target
            value = fill_reply(pending, text, hint or None)
            if pending in {"pair", "account"} and isinstance(value, str) and value:
                slots[pending] = {"value": value, "from": "asked"}
            elif pending in {"target_rate", "threshold"} and isinstance(value, (int, float)):
                slots[pending] = {"value": float(value), "from": "asked"}
            elif isinstance(value, str) and mentions_code(text, value):
                slots[pending] = {"value": value, "from": "asked"}
            elif isinstance(value, dict):
                slots[pending] = {"value": value, "from": "asked"}
        slots = _slots_for_contract(slots, (current or {}).get("contract"))
    if phase == "slot" and current is not None:
        if slots:
            return _decision("fill", current.get("workflow_key") or "", max(score, 0.8), slots, "none", "", "deterministic")
        return _decision("answer", current.get("workflow_key") or "", score, {}, "none", "", "deterministic")
    if best is None or score < 0.5 or (best and _excluded(text, best.get("contract") or {})):
        return _decision("chat", "", score if score > 0 else 0, {}, "none", idle_line(contracts), "deterministic")
    slots = _slots_for_contract(slots, (best or {}).get("contract"))
    return _decision("start", best.get("workflow_key") or "", score, slots, "none", "", "deterministic")


def accept_model(parsed: Any, text: str, contracts: list[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Keep a model choice only when it names a loaded workflow and real slots."""
    if not isinstance(parsed, dict):
        return None
    state = state or {}
    action = str(parsed.get("action") or "").strip().lower()
    if action not in {"start", "fill", "answer", "chat"}:
        return None
    by_key = {item.get("workflow_key"): item for item in contracts}
    key = str(parsed.get("workflow_key") or "")
    if key not in by_key:
        key = str(state.get("workflow_key") or "")
    if key not in by_key:
        key = ""
    match = by_key.get(key)
    phase = state.get("phase") or "idle"
    if match and phase in {"idle", ""} and _excluded(text, match.get("contract") or {}):
        return _decision("chat", "", 0.2, {}, "none", idle_line(contracts), "gemini")
    try:
        confidence = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0.0, min(confidence, 1.0))
    needs = str(parsed.get("needs") or "none").strip().lower()
    if needs not in {"none", "accounts", "cards"}:
        needs = "none"
    pending = state.get("pending_slot") or ""
    if looks_like_question(text) or asks_for_accounts(text) or asks_capacity(text):
        action = "answer"
        if needs == "none":
            needs = _needs_for(text)
        if phase == "slot" and pending and needs == "none":
            needs = "capacity" if asks_capacity(text) else "accounts"
        slots: dict[str, dict[str, Any]] = {}
    elif action in {"start", "fill"}:
        slots = _clean_slots(parsed.get("slots") or {}, text, _ledger(match), pending)
    else:
        slots = {}
    if greeting_reply(text) and phase in {"idle", ""} and not slots:
        action = "chat"
        key = ""
        needs = "none"
    if action == "start" and confidence < 0.5 and not slots:
        action = "chat"
        key = ""
    reply = str(parsed.get("reply") or parsed.get("text") or "").strip()
    if len(reply) > 400:
        reply = reply[:400].rstrip()
    if action == "chat" and not reply:
        reply = greeting_reply(text) or idle_line(contracts)
    return _decision(action, key if action != "chat" else "", confidence, slots, needs, reply, "gemini")


def grounded_reply(
    decision: dict[str, Any],
    ledger: dict[str, Any],
    pending_key: str = "",
    filled: dict[str, Any] | None = None,
    text: str = "",
) -> str:
    """Speak balances from the workflow ledger. The model does not invent them."""
    needs = decision.get("needs") or "none"
    parts = []
    ask_text = text or str(decision.get("model_input") or decision.get("reply") or "")
    if needs == "capacity" or (needs == "accounts" and asks_capacity(ask_text)):
        parts.append(describe_capacity(ledger, filled or {}, ask_text))
    elif needs == "cards":
        parts.append(describe_accounts(ledger, "list my card"))
    elif needs == "accounts":
        parts.append(describe_accounts(ledger, ask_text or "list accounts"))
    elif (decision.get("reply") or "").strip():
        parts.append(str(decision["reply"]).strip())
    if pending_key and decision.get("action") in {"answer", "chat"}:
        question = slot_question(pending_key)
        if question and question not in " ".join(parts):
            parts.append(question)
    return " ".join(part for part in parts if part).strip()


def _decision(action: str, workflow_key: str, confidence: float, slots: dict, needs: str, reply: str, source: str) -> dict[str, Any]:
    return {
        "action": action,
        "workflow_key": workflow_key,
        "confidence": round(float(confidence), 2),
        "slots": slots,
        "needs": needs,
        "reply": reply,
        "source": source,
    }
