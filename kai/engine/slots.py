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
            currency = "USD" if target == "CARD" else (target or "USD")
    if number <= 0:
        return None
    return {"amount": number, "currency": currency, "display": format_money(number, currency)}


def extract_slots(text: str) -> dict[str, dict[str, Any]]:
    """Return slot key -> {value, from} for whatever the message itself states."""

    found: dict[str, dict[str, Any]] = {}
    lowered = re.sub(r"\btopup\b", "top up", text.lower())

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
    if not target and ("dollar" in lowered or re.search(r"\busd\b", lowered)):
        target = "USD"
    if target:
        found["target"] = {"value": target, "from": "the message"}

    amount = parse_amount(text, target)
    if amount and re.search(r"\d", text):
        found["amount"] = {"value": amount, "from": "the message"}

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
        if to_match:
            target = _code(to_match.group(1))
            if target:
                found["target"] = {"value": target, "from": "the message"}
    if "source" not in found and re.search(r"\bfrom\b", lowered):
        named = _currency_codes(lowered)
        if named:
            found["source"] = {"value": named[-1], "from": "the message"}
    amount = (found.get("amount") or {}).get("value") or {}
    source = (found.get("source") or {}).get("value")
    if amount and source and re.search(r"\b(move|send)\b", lowered) and not re.search(r"[€$£]", text):
        glued = re.search(r"(\d+(?:\.\d+)?)\s*(eur|euros?|usd|dollars?|usdt|gbp|pounds?|chf)\b", lowered)
        if not glued:
            number = amount["amount"]
            found["amount"]["value"] = {"amount": number, "currency": source, "display": format_money(number, source)}
    return found


def asks_for_accounts(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    if re.search(r"\b(top\s*up|topup|top-up|refill)\b", lowered):
        return False
    listing = re.search(r"\b(list|show|see|what|which|available|availability|balances?|options?)\b", lowered)
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


def score_intent(text: str, contract: dict[str, Any]) -> float:
    lowered = " ".join(text.lower().split())
    for exclusion in contract.get("exclusions") or []:
        if exclusion.lower() in lowered or lowered in exclusion.lower():
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
    if any(word in lowered for word in ("top up", "topup", "refill")) or "top-up" in lowered:
        described = (contract.get("description") or "").lower()
        if "top it up" in described or "refill" in described:
            best = max(best, 0.8)
    elif any(word in lowered for word in ("top up", "topup", "refill", "add")) and any(code.lower() in lowered for code in CODES):
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
    if re.search(r"\b(top\s*up|topup|top-up|refill|move|send|add)\b", lowered):
        return False
    return bool(re.match(r"(what|which|who|how|show|list|do i|can you|could you|tell me)\b", lowered) or "?" in lowered)


def is_port_reply(text: str) -> str | None:
    """An exact port. Labels and synonyms belong on the step, not here."""
    lowered = " ".join(text.lower().split())
    if lowered in {"yes", "no", "approve", "reject", "all"}:
        return lowered
    return None


def _excluded(text: str, contract: dict[str, Any]) -> bool:
    lowered = " ".join(text.lower().split())
    for exclusion in contract.get("exclusions") or []:
        sample = exclusion.lower()
        if sample and (sample in lowered or lowered in sample):
            return True
    return False


def _best_contract(text: str, contracts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best = None
    score = -1.0
    for item in contracts:
        found = score_intent(text, item.get("contract") or {})
        if found > score:
            score = found
            best = item
    return best, score if best is not None else 0.0


def _ledger(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {"accounts": {}}
    return item.get("ledger") or (item.get("contract") or {}).get("preview_ledger") or {"accounts": {}}


def _needs_for(text: str) -> str:
    lowered = text.lower()
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
    if "amount" in raw and re.search(r"\d", text):
        parsed = parse_amount(text, None)
        if parsed:
            found["amount"] = {"value": parsed, "from": "the message"}
    for key, value in raw.items():
        if key == "amount":
            continue
        scalar = value.get("value") if isinstance(value, dict) else value
        code = _code(str(scalar or ""))
        if not code or code not in known:
            continue
        if mentions_code(text, code) or (key == pending and mentions_code(text, code)):
            found[str(key)] = {"value": code, "from": "the message"}
    if not looks_like_question(text):
        for key, item in extract_slots(text).items():
            found.setdefault(key, item)
    return found


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
    """Decide from the published contracts when the model is unavailable."""
    state = state or {}
    phase = state.get("phase") or "idle"
    pending = state.get("pending_slot") or ""
    current = next((item for item in contracts if item.get("workflow_key") == state.get("workflow_key")), None)
    best, score = _best_contract(text, contracts)
    greeting = greeting_reply(text)
    if greeting and phase in {"idle", ""} and not pending:
        return _decision("chat", "", 0, {}, "none", greeting, "deterministic")
    if looks_like_question(text) or asks_for_accounts(text):
        owner = current or best
        key = (owner or {}).get("workflow_key") or ""
        needs = _needs_for(text)
        if needs == "none" and re.search(r"\b(fund|funding|use|pay|cover|take|available|options?)\b", text.lower()):
            needs = "accounts"
        return _decision("answer", key, max(score, 0), {}, needs, "", "deterministic")
    slots = _clean_slots({}, text, _ledger(current or best), pending)
    if phase == "slot" and pending and not looks_like_question(text):
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
            if isinstance(value, str) and mentions_code(text, value):
                slots[pending] = {"value": value, "from": "asked"}
            elif isinstance(value, dict):
                slots[pending] = {"value": value, "from": "asked"}
    if phase == "slot" and current is not None:
        if slots:
            return _decision("fill", current.get("workflow_key") or "", max(score, 0.8), slots, "none", "", "deterministic")
        return _decision("answer", current.get("workflow_key") or "", score, {}, "none", "", "deterministic")
    if best is None or score < 0.5 or (best and _excluded(text, best.get("contract") or {})):
        return _decision("chat", "", score if score > 0 else 0, {}, "none", idle_line(contracts), "deterministic")
    return _decision("start", best.get("workflow_key") or "", score, slots, "none", "", "deterministic")


def accept_model(parsed: Any, text: str, contracts: list[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Keep a model choice only when it names a published workflow and real slots."""
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
    if looks_like_question(text) or asks_for_accounts(text):
        action = "answer"
        if needs == "none":
            needs = _needs_for(text)
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


def grounded_reply(decision: dict[str, Any], ledger: dict[str, Any], pending_key: str = "") -> str:
    """Speak balances from the workflow ledger. The model does not invent them."""
    needs = decision.get("needs") or "none"
    parts = []
    if needs == "cards":
        parts.append(describe_accounts(ledger, "list my card"))
    elif needs == "accounts":
        parts.append(describe_accounts(ledger, "list accounts"))
    elif (decision.get("reply") or "").strip():
        parts.append(str(decision["reply"]).strip())
    if pending_key and decision.get("action") in {"answer", "chat"}:
        question = slot_question(pending_key)
        if question not in " ".join(parts):
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
