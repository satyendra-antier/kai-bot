"""Read-only tools for the customer chat.

A balance, a holding, or a performance figure is spoken only when this chat has
an opened account. Until then the answer is a denial, and onboarding is how an
account gets created. Price is the quote on file, which does not need an account.
"""

from __future__ import annotations

import re
from typing import Any

from kai.engine.slots import asks_for_accounts, describe_accounts

ACCOUNT_TOOLS = {"balance", "holdings", "performance"}
_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?<!\d)\+\d[\d\s\-()]{8,}\d(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){12,19}(?!\d)")
_OPT_OUT = re.compile(r"\b(opt[\s-]?out|stop using my data|forget me)\b", re.I)
_OPT_IN = re.compile(r"\bopt[\s-]?in\b", re.I)


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Hide emails, phone numbers, and card numbers before a model sees the message."""
    shown = text or ""
    kinds: list[str] = []
    for label, pattern in (("email", _EMAIL), ("phone", _PHONE), ("card", _CARD)):
        if pattern.search(shown):
            kinds.append(label)
            shown = pattern.sub(f"[{label}]", shown)
    return shown, kinds


def select_tool(text: str) -> str | None:
    """Pick the read tool this message is asking for, if it is a question rather than an answer."""
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return None
    if _OPT_OUT.search(lowered):
        return "opt_out"
    if _OPT_IN.search(lowered):
        return "opt_in"
    if re.search(r"\b(profile|who am i|whoami|do i have an account|my account status)\b", lowered):
        return "profile"
    if re.search(r"\b(holdings?|portfolio|positions?)\b", lowered):
        return "holdings"
    if re.search(r"\b(performance|how did i do|my return)\b", lowered):
        return "performance"
    if re.search(r"\b(price|quote|exchange rate|market)\b", lowered) or re.search(r"\brate\b", lowered):
        if not re.search(r"\b(move|top\s*up|topup|transfer|send)\b", lowered):
            return "price"
    if asks_for_accounts(lowered) or re.search(r"\b(fetch|show|check)\b", lowered) and re.search(r"\bbalances?\b", lowered):
        return "balance"
    return None


def _answer(reply: str, needs: str = "none", **extra: Any) -> dict[str, Any]:
    decision = {
        "action": "answer",
        "workflow_key": "",
        "confidence": 1,
        "slots": {},
        "needs": needs,
        "reply": reply,
        "port": "",
    }
    decision.update(extra)
    return decision


def _start(key: str) -> dict[str, Any]:
    return {"action": "start", "workflow_key": key, "confidence": 0.97, "slots": {}, "needs": "none", "reply": "", "port": ""}


def _has(contracts: list[dict[str, Any]], key: str) -> bool:
    return any(item.get("workflow_key") == key for item in contracts)


def _money(decision: dict[str, Any], contracts: list[dict[str, Any]]) -> bool:
    key = str(decision.get("workflow_key") or "")
    if "onboarding" in key:
        return False
    match = next((item for item in contracts if item.get("workflow_key") == key), None)
    if match is None:
        return False
    names = {slot.get("key") for slot in ((match.get("contract") or {}).get("slots") or []) if isinstance(slot, dict)}
    return bool(names & {"amount", "source", "target", "account"})


def _no_account(tool: str) -> str:
    subject = {
        "balance": "show a balance",
        "holdings": "show holdings",
        "performance": "show performance",
    }.get(tool, "do that")
    return f"You don't have an account yet, so I can't {subject}. Say onboarding to open one."


def _with_account(tool: str, profile: dict[str, Any]) -> dict[str, Any]:
    kind = profile.get("kind") or "Kai"
    ledger = profile.get("ledger") or {"accounts": {}}
    accounts = ledger.get("accounts") or {}
    if tool == "performance":
        return _answer(f"Your {kind} account is open. I don't have a performance record yet.")
    if not accounts:
        if tool == "holdings":
            return _answer(f"Your {kind} account is open. There are no holdings yet.")
        return _answer(f"Your {kind} account is open. There's no balance on it yet.")
    listed = describe_accounts(ledger, "list accounts")
    if tool == "holdings":
        return _answer(listed.replace("You have ", "Your holdings are ", 1))
    return _answer(listed, needs="accounts", ledger=ledger)


def apply_guard(
    text: str,
    decision: dict[str, Any],
    profile: dict[str, Any],
    contracts: list[dict[str, Any]] | None = None,
    kill_switch: bool = False,
) -> dict[str, Any] | None:
    """Replace a turn that would invent account data or start money with no account."""
    contracts = contracts or []
    lowered = " ".join((text or "").lower().split())
    tool = select_tool(text)
    if kill_switch and (tool or decision.get("action") in {"start", "fill"}):
        return _answer("Kai is paused. I can't look anything up or start anything right now.")
    if tool == "opt_out":
        return _answer("You've opted out. I won't look up your account until you say opt in.")
    if tool == "opt_in":
        return _answer("You've opted in. I can look up your account when you have one.")
    if profile.get("opted_out") and tool in ACCOUNT_TOOLS | {"profile"}:
        return _answer("You opted out, so I won't look up your account. Say opt in to allow it again.")
    if not profile.get("account") and re.search(
        r"\b(onboarding|open an account|new account|create an account|sign\s*up|register)\b",
        lowered,
    ):
        key = "business-onboarding" if "business" in lowered else "individual-onboarding"
        if _has(contracts, key):
            return _start(key)
    if tool == "profile":
        if profile.get("account"):
            return _answer(f"You have a {profile.get('kind') or 'Kai'} account. I can show your balance, holdings, or profile.")
        return _answer("There's no account on this chat yet. Say onboarding to open one.")
    if tool == "price":
        rate = profile.get("rate")
        if rate in (None, ""):
            return _answer("I don't have a quote on file.")
        return _answer(f"The quote on file is {rate} for EUR to USD.")
    if tool in ACCOUNT_TOOLS:
        if not profile.get("account"):
            return _answer(_no_account(tool))
        return _with_account(tool, profile)
    if decision.get("action") in {"start", "fill"} and not profile.get("account") and _money(decision, contracts):
        return _answer("You don't have an account yet, so I can't do that. Say onboarding to open one.")
    if decision.get("action") == "start" and profile.get("account") and "onboarding" in str(decision.get("workflow_key") or ""):
        return _answer(f"You already have a {profile.get('kind') or 'Kai'} account. I can show your balance, holdings, or profile.")
    return None
