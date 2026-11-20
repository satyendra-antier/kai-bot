"""User intelligence: every customer run, scored twice, and the rules that start them."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

WORKFLOW_ORDER = [
    "low-balance-top-up",
    "low-balance-alert",
    "sweep-between-accounts",
    "fx-rate-watch",
    "jit-card-topup",
    "individual-onboarding",
    "business-onboarding",
    "demo-balance",
    "demo-alert",
    "demo-move",
    "demo-login",
    "demo-choose",
    "demo-list",
]

ENDED_ON = {
    "done": "Done",
    "not_approved": "Not approved",
    "cancelled": "Cancelled",
    "none": "Nothing covers it",
    "cannot": "Cannot cover",
    "recovered": "Recovered",
}


def ended_label(result: Optional[str]) -> str:
    if not result:
        return ""
    return ENDED_ON.get(result, result.replace("_", " "))


def run_ended_on(row: dict[str, Any]) -> str:
    """Human label for the Runs table — never leave waiting runs blank."""
    ended = str(row.get("ended_on") or "").strip() or ended_label(row.get("result"))
    if ended:
        return ended
    status = str(row.get("status") or "").strip()
    step = str(row.get("step_id") or "").strip()
    if status == "waiting":
        return f"waiting · {step}" if step else "waiting"
    if status and status not in {"completed", "blocked"}:
        return f"{status} · {step}" if step else status
    return step or ""


def turns_for_run(record: dict[str, Any], turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep chat-audit turns that fall inside this run — not the whole conversation."""
    start = record.get("started_at")
    end = record.get("finished_at")
    workflow_id = str(record.get("workflow_id") or "")
    matched: list[dict[str, Any]] = []
    for turn in turns:
        ts = turn.get("created_at")
        if start is not None and ts is not None:
            # Child run starts after the chat turn that launched it — keep a short lookback.
            if ts < start - timedelta(seconds=45):
                continue
            latest = end + timedelta(seconds=1) if end is not None else start + timedelta(minutes=30)
            if ts > latest:
                continue
        key = str(turn.get("workflow_key") or "")
        if key and workflow_id and key != workflow_id and turn.get("action") == "start":
            continue
        # Anything after the graph finished is the next idle turn (except a late-stamped choice).
        if end is not None and ts is not None and ts > end:
            if turn.get("action") != "choice" and turn.get("route") != "choice":
                continue
        cleaned = {name: value for name, value in turn.items() if name != "created_at"}
        matched.append(cleaned)
    return matched


def started_label(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return f"{value.day} {value.strftime('%b %H:%M')}"


def since_label(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return f"{value.day} {value.strftime('%b')}"


def duration_label(start: Optional[datetime], end: Optional[datetime]) -> str:
    if start is None or end is None:
        return ""
    minutes = int((end - start).total_seconds() // 60)
    if minutes <= 0:
        return "1m"
    hours, mins = divmod(minutes, 60)
    if hours >= 24 and mins == 0 and hours % 24 == 0:
        return f"{hours}h"
    if hours >= 24:
        days, hours = divmod(hours, 24)
        if hours:
            return f"{days}d {hours}h"
        return f"{days}d"
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def show_score(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    rounded = round(float(value), 2)
    if abs(rounded - round(rounded)) < 0.001:
        return int(round(rounded))
    return rounded


def next_run_code(codes: list[str]) -> str:
    numbers = [int(code[2:]) for code in codes if code and code.startswith("R-") and code[2:].isdigit()]
    return f"R-{(max(numbers) if numbers else 10000) + 1}"


def _avg(values: list[Optional[float]]) -> Optional[float]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return show_score(sum(clean) / len(clean))


def _ordered(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in workflows}
    ordered = [by_id[key] for key in WORKFLOW_ORDER if key in by_id]
    rest = [row for row in workflows if row["id"] not in {item["id"] for item in ordered}]
    rest.sort(key=lambda row: row.get("name") or "")
    return ordered + rest


def _stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    chat_runs = [row for row in records if row.get("origin") == "chat"]
    rule_runs = [row for row in records if row.get("origin") == "rule"]
    return {
        "runs": len(records),
        "chat_runs": len(chat_runs),
        "rule_runs": len(rule_runs),
        "chat": _avg([row.get("chat") for row in chat_runs]),
        "outcome": _avg([row.get("outcome") for row in records]),
        "flagged": sum(1 for row in records if row.get("flagged")),
    }


def _decorate_run(row: dict[str, Any]) -> dict[str, Any]:
    reason = ""
    if row.get("flagged"):
        reasons = row.get("reasons") or []
        reason = reasons[0] if reasons else ""
    placed = ""
    if reason:
        placed = "outcome" if reason.startswith("outcome") or row.get("origin") != "chat" else "chat"
    return {
        "id": row["id"],
        "code": row.get("code") or row["id"],
        "started_label": started_label(row.get("started_at")),
        "duration_label": duration_label(row.get("started_at"), row.get("finished_at")),
        "customer_name": row.get("customer_name") or "",
        "customer_handle": row.get("customer_handle") or "",
        "workflow_id": row.get("workflow_id") or "",
        "workflow_name": row.get("workflow_name") or "",
        "origin": row.get("origin") or "",
        "rule_id": row.get("rule_id") or "",
        "rule_code": row.get("rule_code") or "",
        "ended_on": run_ended_on(row),
        "chat": show_score(row.get("chat")),
        "outcome": show_score(row.get("outcome")),
        "flagged": bool(row.get("flagged")),
        "reason": reason,
        "reason_on": placed,
        "status": row.get("status") or "",
        "checks": row.get("checks") or {},
        "trace": list(row.get("trace") or []),
        "messages": list(row.get("messages") or []),
        "conversation_id": row.get("conversation_id") or "",
        "step_id": row.get("step_id") or "",
        "turns": list(row.get("turns") or []),
        "flow": list(row.get("flow") or []),
    }


def runs_payload(
    records: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    workflow_id: str = "",
    rule_code: str = "",
    origin: str = "",
    flagged_only: bool = False,
) -> dict[str, Any]:
    names = {row["id"]: row.get("name") or row["id"] for row in workflows}
    prepared = []
    for row in records:
        item = dict(row)
        item["workflow_name"] = item.get("workflow_name") or names.get(item.get("workflow_id"), "")
        prepared.append(item)
    counts: dict[str, int] = {}
    for row in prepared:
        key = row.get("workflow_id") or ""
        counts[key] = counts.get(key, 0) + 1
    sidebar = [{"id": row["id"], "name": row.get("name") or row["id"], "count": counts.get(row["id"], 0)} for row in _ordered(workflows)]
    focus_rows = [row for row in prepared if rule_code and row.get("rule_code") == rule_code]
    selected = prepared
    if workflow_id:
        selected = [row for row in selected if row.get("workflow_id") == workflow_id]
    if rule_code:
        selected = [row for row in selected if row.get("rule_code") == rule_code]
    if origin in {"chat", "rule"}:
        selected = [row for row in selected if row.get("origin") == origin]
    if flagged_only:
        selected = [row for row in selected if row.get("flagged")]
    selected.sort(key=lambda row: row.get("started_at") or datetime.min, reverse=True)
    focus = None
    if rule_code:
        focus_stats = _stats(focus_rows)
        focus = {"code": rule_code, "runs": focus_stats["runs"]}
    return {
        "headline": {"flagged": sum(1 for row in prepared if row.get("flagged")), "total": len(prepared)},
        "workflows": sidebar,
        "focus": focus,
        "stats": _stats(selected),
        "rows": [_decorate_run(row) for row in selected],
    }


def rules_payload(
    rules: list[dict[str, Any]],
    records: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    workflow_id: str = "",
) -> dict[str, Any]:
    names = {row["id"]: row.get("name") or row["id"] for row in workflows}
    flagged_codes = {row.get("rule_code") for row in records if row.get("flagged") and row.get("rule_code")}
    counts: dict[str, int] = {}
    for rule in rules:
        key = rule.get("workflow_id") or ""
        counts[key] = counts.get(key, 0) + 1
    sidebar = []
    for row in _ordered(workflows):
        count = counts.get(row["id"], 0)
        if count:
            sidebar.append({"id": row["id"], "name": row.get("name") or row["id"], "count": count})
    selected = [rule for rule in rules if not workflow_id or rule.get("workflow_id") == workflow_id]
    selected.sort(key=lambda rule: rule.get("since") or datetime.min, reverse=True)
    rows = []
    for rule in selected:
        rows.append(
            {
                "id": rule["id"],
                "code": rule.get("code") or "",
                "since_label": since_label(rule.get("since")),
                "workflow_id": rule.get("workflow_id") or "",
                "workflow_name": names.get(rule.get("workflow_id"), ""),
                "customer_name": rule.get("customer_name") or "",
                "customer_handle": rule.get("customer_handle") or "",
                "made_in": rule.get("made_in") or "",
                "values": rule.get("values") or [],
                "state": "on" if rule.get("enabled", True) else "paused",
                "needs_look": (rule.get("code") or "") in flagged_codes,
            }
        )
    return {
        "headline": {
            "attention": sum(1 for rule in rules if (rule.get("code") or "") in flagged_codes),
            "total": len(rules),
            "on": sum(1 for rule in rules if rule.get("enabled", True)),
            "paused": sum(1 for rule in rules if not rule.get("enabled", True)),
        },
        "workflows": sidebar,
        "rows": rows,
    }


def _at(month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, month, day, hour, minute)


def _finished(start: datetime, hours: int = 0, minutes: int = 0) -> datetime:
    return start + timedelta(hours=hours, minutes=minutes)


CUSTOMERS = [
    {"id": "cust-thomas", "name": "Thomas Reyes", "handle": "thomas.r"},
    {"id": "cust-amira", "name": "Amira Shah", "handle": "amira.sh"},
    {"id": "cust-jonah", "name": "Jonah Park", "handle": "jonah.p"},
    {"id": "cust-elena", "name": "Elena Voss", "handle": "elena.v"},
    {"id": "cust-chris", "name": "Chris Adeyemi", "handle": "chris.ad"},
    {"id": "cust-priya", "name": "Priya Nair", "handle": "priya.n"},
]

_THOMAS_TOPUP = [
    {"label": "Watches", "value": "USD account"},
    {"label": "Fires below", "value": "$100.00"},
    {"label": "Tops up to", "value": "$400.00"},
    {"label": "From", "value": "EUR"},
    {"label": "Cap", "value": "$1,000 a run"},
    {"label": "Runs", "value": "1 a day"},
    {"label": "Ask first", "value": "always"},
]


def live_rules() -> list[dict[str, Any]]:
    from kai.engine.topup import money

    return [
        {
            "id": "rule-usd",
            "code": "LR-3120",
            "workflow_id": "low-balance-top-up",
            "customer_id": "cust-thomas",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(9, 2, 0, 0),
            "values": _THOMAS_TOPUP,
            "params": {
                "target": "USD",
                "amount": money(250, "EUR"),
                "threshold": 100,
                "top_up_to": 400,
                "runs_left": 1,
                "cap": 1000,
                "source": "EUR",
            },
        },
        {
            "id": "rule-gbp",
            "code": "LR-3188",
            "workflow_id": "low-balance-top-up",
            "customer_id": "cust-amira",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(8, 18, 0, 0),
            "values": [
                {"label": "Watches", "value": "GBP account"},
                {"label": "Fires below", "value": "£80.00"},
                {"label": "Tops up to", "value": "£250.00"},
                {"label": "From", "value": "EUR"},
                {"label": "Cap", "value": "£500 a run"},
                {"label": "Runs", "value": "2 a day"},
                {"label": "Ask first", "value": "always"},
            ],
            "params": {"target": "GBP", "threshold": 80, "top_up_to": 250, "runs_left": 2, "cap": 500, "source": "EUR"},
        },
        {
            "id": "rule-alert-eur",
            "code": "LR-2204",
            "workflow_id": "low-balance-alert",
            "customer_id": "cust-amira",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(7, 14, 0, 0),
            "values": [
                {"label": "Watches", "value": "EUR account"},
                {"label": "Fires below", "value": "€40.00"},
                {"label": "Tells them", "value": "in chat"},
            ],
            "params": {},
        },
        {
            "id": "rule-alert-usd",
            "code": "LR-2210",
            "workflow_id": "low-balance-alert",
            "customer_id": "cust-jonah",
            "source_step": "balance_rule",
            "enabled": False,
            "made_in": "In the app",
            "since": _at(6, 3, 0, 0),
            "values": [
                {"label": "Watches", "value": "USD account"},
                {"label": "Fires below", "value": "$25.00"},
                {"label": "Tells them", "value": "in chat"},
            ],
            "params": {},
        },
        {
            "id": "rule-alert-chf",
            "code": "LR-2218",
            "workflow_id": "low-balance-alert",
            "customer_id": "cust-elena",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(8, 2, 0, 0),
            "values": [
                {"label": "Watches", "value": "CHF account"},
                {"label": "Fires below", "value": "30.00 CHF"},
                {"label": "Tells them", "value": "in chat"},
            ],
            "params": {},
        },
        {
            "id": "rule-fx-eur",
            "code": "LR-4410",
            "workflow_id": "fx-rate-watch",
            "customer_id": "cust-chris",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(5, 21, 0, 0),
            "values": [
                {"label": "Pair", "value": "EUR/USD"},
                {"label": "Tell me above", "value": "1.12"},
            ],
            "params": {},
        },
        {
            "id": "rule-fx-gbp",
            "code": "LR-4419",
            "workflow_id": "fx-rate-watch",
            "customer_id": "cust-priya",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(9, 9, 0, 0),
            "values": [
                {"label": "Pair", "value": "GBP/USD"},
                {"label": "Tell me below", "value": "1.24"},
            ],
            "params": {},
        },
        {
            "id": "rule-jit-thomas",
            "code": "LR-5502",
            "workflow_id": "jit-card-topup",
            "customer_id": "cust-thomas",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(4, 11, 0, 0),
            "values": [
                {"label": "Watches", "value": "card"},
                {"label": "Fires below", "value": "$20.00"},
                {"label": "From", "value": "USD"},
                {"label": "Cap", "value": "$200 a run"},
            ],
            "params": {},
        },
        {
            "id": "rule-jit-jonah",
            "code": "LR-5511",
            "workflow_id": "jit-card-topup",
            "customer_id": "cust-jonah",
            "source_step": "balance_rule",
            "enabled": True,
            "made_in": "In the app",
            "since": _at(8, 28, 0, 0),
            "values": [
                {"label": "Watches", "value": "card"},
                {"label": "Fires below", "value": "€15.00"},
                {"label": "From", "value": "EUR"},
                {"label": "Cap", "value": "€150 a run"},
            ],
            "params": {},
        },
        {
            "id": "rule-jit-priya",
            "code": "LR-5520",
            "workflow_id": "jit-card-topup",
            "customer_id": "cust-priya",
            "source_step": "balance_rule",
            "enabled": False,
            "made_in": "In the app",
            "since": _at(3, 19, 0, 0),
            "values": [
                {"label": "Watches", "value": "card"},
                {"label": "Fires below", "value": "£10.00"},
                {"label": "From", "value": "GBP"},
                {"label": "Cap", "value": "£100 a run"},
            ],
            "params": {},
        },
    ]


def _history_run(
    code: str,
    workflow_id: str,
    customer_id: str,
    origin: str,
    started: datetime,
    ended_on: str,
    outcome: float,
    chat: Optional[float] = None,
    rule_id: str = "",
    flagged: bool = False,
    reasons: Optional[list[str]] = None,
    result: str = "done",
    hours: int = 0,
    minutes: int = 4,
    checks: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "id": f"run:demo:{code}",
        "code": code,
        "workflow_id": workflow_id,
        "customer_id": customer_id,
        "rule_id": rule_id,
        "origin": origin,
        "status": "completed",
        "result": result,
        "ended_on": ended_on,
        "started_at": started,
        "finished_at": _finished(started, hours=hours, minutes=minutes),
        "chat": chat,
        "outcome": outcome,
        "flagged": flagged,
        "reasons": reasons or [],
        "checks": checks or {},
    }


def run_history() -> list[dict[str, Any]]:
    approved = "Rule fires, approved"
    return [
        _history_run(
            "R-10232",
            "low-balance-top-up",
            "cust-thomas",
            "rule",
            _at(9, 22, 8, 15),
            approved,
            89,
            rule_id="rule-usd",
            hours=2,
            minutes=14,
            checks={"right_end": 100, "speed": 56, "finished": 100, "no_errors": 100},
        ),
        _history_run(
            "R-10235",
            "low-balance-top-up",
            "cust-thomas",
            "rule",
            _at(9, 20, 7, 58),
            "Not approved",
            55,
            rule_id="rule-usd",
            flagged=True,
            reasons=["outcome under 70"],
            result="not_approved",
            hours=24,
            minutes=0,
            checks={"right_end": 100, "speed": 20, "finished": 0, "no_errors": 100},
        ),
        _history_run(
            "R-10231",
            "low-balance-top-up",
            "cust-amira",
            "chat",
            _at(9, 18, 11, 2),
            "Asked in chat, enough in EUR",
            92,
            chat=100,
            checks={"intent": 100, "slots": 100, "turns": 79, "lines": 100, "guardrails": 100, "right_end": 100, "speed": 69, "finished": 100, "no_errors": 100},
        ),
        _history_run(
            "R-10233",
            "low-balance-top-up",
            "cust-amira",
            "chat",
            _at(9, 14, 9, 40),
            "Named account short, another offered",
            100,
            chat=100,
            checks={"intent": 100, "slots": 100, "turns": 100, "lines": 61, "guardrails": 100, "right_end": 100, "speed": 100, "finished": 100, "no_errors": 100},
        ),
        _history_run(
            "R-10234",
            "low-balance-top-up",
            "cust-thomas",
            "chat",
            _at(9, 8, 9, 12),
            "Asked in chat, enough in EUR",
            84,
            chat=67,
            flagged=True,
            reasons=["chat under 80"],
            checks={"intent": 0, "slots": 100, "turns": 100, "lines": 100, "guardrails": 100, "right_end": 50, "speed": 100, "finished": 100, "no_errors": 100},
        ),
        _history_run("R-10301", "low-balance-alert", "cust-amira", "rule", _at(9, 17, 9, 40), "Told the customer", 93, rule_id="rule-alert-eur"),
        _history_run("R-10302", "low-balance-alert", "cust-elena", "rule", _at(9, 11, 10, 18), "Told the customer", 88, rule_id="rule-alert-chf"),
        _history_run("R-10303", "low-balance-alert", "cust-jonah", "chat", _at(9, 6, 19, 14), "Customer asked", 86, chat=90),
        _history_run("R-10304", "low-balance-alert", "cust-amira", "rule", _at(9, 3, 16, 2), "Told the customer", 84, rule_id="rule-alert-eur"),
        _history_run("R-10410", "sweep-between-accounts", "cust-chris", "chat", _at(9, 16, 15, 22), "Sweep complete", 90, chat=86),
        _history_run(
            "R-10411",
            "sweep-between-accounts",
            "cust-priya",
            "chat",
            _at(9, 7, 12, 30),
            "Sweep complete",
            88,
            chat=86,
        ),
        _history_run("R-10412", "sweep-between-accounts", "cust-elena", "chat", _at(9, 2, 8, 44), "Sweep complete", 95, chat=94),
        _history_run("R-10501", "fx-rate-watch", "cust-chris", "rule", _at(9, 15, 8, 3), "Rate crossed", 92, rule_id="rule-fx-eur"),
        _history_run("R-10502", "fx-rate-watch", "cust-priya", "chat", _at(9, 9, 17, 5), "Rate crossed", 90, chat=91),
        _history_run("R-10503", "fx-rate-watch", "cust-priya", "rule", _at(9, 4, 11, 11), "Rate crossed", 87, rule_id="rule-fx-gbp"),
        _history_run("R-10504", "fx-rate-watch", "cust-chris", "chat", _at(9, 19, 14, 12), "Rate crossed", 91, chat=89),
        _history_run("R-10601", "jit-card-topup", "cust-thomas", "rule", _at(9, 10, 13, 45), "Card topped up", 90, rule_id="rule-jit-thomas"),
        _history_run("R-10602", "jit-card-topup", "cust-jonah", "rule", _at(9, 5, 7, 50), "Card topped up", 86, rule_id="rule-jit-jonah"),
        _history_run("R-10603", "jit-card-topup", "cust-thomas", "rule", _at(9, 21, 18, 5), "Card topped up", 93, rule_id="rule-jit-thomas"),
    ]
