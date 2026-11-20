"""Kai never — live enforcement and shared helpers for guardrail scoring."""

from __future__ import annotations

from typing import Any


SAFE_FALLBACK = "I can't say that. Let's stay with what this workflow allows."


def forbidden_phrases(never: list[dict[str, Any]] | None) -> list[str]:
    phrases: list[str] = []
    for rule in never or []:
        if not isinstance(rule, dict):
            continue
        for phrase in rule.get("forbidden") or []:
            cleaned = str(phrase or "").strip()
            if cleaned and cleaned.lower() not in {item.lower() for item in phrases}:
                phrases.append(cleaned)
        text = str(rule.get("text") or "").strip()
        if rule.get("mode") == "judged" and text and text.lower() not in {item.lower() for item in phrases}:
            # Exact rule text also counts — admin chips often only set `text`.
            phrases.append(text)
    return phrases


def breach_never(text: str, never: list[dict[str, Any]] | None) -> str | None:
    """Return the forbidden phrase if Kai's line breaks a never rule."""
    lowered = (text or "").lower()
    if not lowered:
        return None
    for phrase in forbidden_phrases(never):
        if phrase.lower() in lowered:
            return phrase
    return None


def scrub_never(text: str, never: list[dict[str, Any]] | None) -> tuple[str, bool, str]:
    """Replace a breaching line. Returns (text, breached, phrase)."""
    hit = breach_never(text, never)
    if not hit:
        return text or "", False, ""
    return SAFE_FALLBACK, True, hit


def with_forbidden(rule: dict[str, Any]) -> dict[str, Any]:
    """Ensure judged rules carry at least their own text as a forbidden phrase."""
    row = dict(rule)
    forbidden = [str(item).strip() for item in (row.get("forbidden") or []) if str(item).strip()]
    text = str(row.get("text") or "").strip()
    if row.get("mode") != "order" and text and text.lower() not in {item.lower() for item in forbidden}:
        forbidden.append(text)
    row["forbidden"] = forbidden
    return row
