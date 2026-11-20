from datetime import datetime, timedelta

from kai.intelligence import (
    CUSTOMERS,
    duration_label,
    live_rules,
    run_ended_on,
    run_history,
    runs_payload,
    rules_payload,
    started_label,
    turns_for_run,
)

WORKFLOWS = [
    {"id": "low-balance-top-up", "name": "Low balance top-up"},
    {"id": "low-balance-alert", "name": "Low balance alert"},
    {"id": "sweep-between-accounts", "name": "Sweep between accounts"},
    {"id": "fx-rate-watch", "name": "FX rate watch"},
    {"id": "jit-card-topup", "name": "Just-in-time card top-up"},
    {"id": "individual-onboarding", "name": "Individual onboarding"},
    {"id": "business-onboarding", "name": "Business onboarding"},
]


def _names():
    customers = {row["id"]: row for row in CUSTOMERS}
    rules = {row["id"]: row for row in live_rules()}
    records = []
    for row in run_history():
        customer = customers[row["customer_id"]]
        rule = rules.get(row["rule_id"])
        records.append({**row, "customer_name": customer["name"], "customer_handle": customer["handle"], "rule_code": rule["code"] if rule else ""})
    return records


def _rule_rows():
    customers = {row["id"]: row for row in CUSTOMERS}
    rows = []
    for rule in live_rules():
        customer = customers[rule["customer_id"]]
        rows.append({**rule, "customer_name": customer["name"], "customer_handle": customer["handle"]})
    return rows


def test_run_history_matches_the_intelligence_screen():
    records = _names()
    payload = runs_payload(records, WORKFLOWS)
    assert payload["headline"] == {"flagged": 2, "total": 19}
    counts = {row["name"]: row["count"] for row in payload["workflows"]}
    assert counts == {
        "Low balance top-up": 5,
        "Low balance alert": 4,
        "Sweep between accounts": 3,
        "FX rate watch": 4,
        "Just-in-time card top-up": 3,
        "Individual onboarding": 0,
        "Business onboarding": 0,
    }
    focused = runs_payload(records, WORKFLOWS, rule_code="LR-3120")
    assert focused["focus"] == {"code": "LR-3120", "runs": 2}
    assert focused["stats"]["chat_runs"] == 0
    assert focused["stats"]["rule_runs"] == 2
    assert focused["stats"]["chat"] is None
    assert focused["stats"]["outcome"] == 72
    assert focused["stats"]["flagged"] == 1
    by_code = {row["code"]: row for row in focused["rows"]}
    assert by_code["R-10232"]["started_label"] == "22 Sep 08:15"
    assert by_code["R-10232"]["duration_label"] == "2h 14m"
    assert by_code["R-10232"]["ended_on"] == "Rule fires, approved"
    assert by_code["R-10232"]["chat"] is None
    assert by_code["R-10232"]["outcome"] == 89
    assert by_code["R-10232"]["customer_name"] == "Thomas Reyes"
    assert by_code["R-10232"]["customer_handle"] == "thomas.r"
    assert by_code["R-10235"]["started_label"] == "20 Sep 07:58"
    assert by_code["R-10235"]["duration_label"] == "24h"
    assert by_code["R-10235"]["ended_on"] == "Not approved"
    assert by_code["R-10235"]["outcome"] == 55
    assert by_code["R-10235"]["reason"] == "outcome under 70"
    assert by_code["R-10235"]["reason_on"] == "outcome"


def test_topup_eval_scorecard():
    from kai.engine.scoring import rollup_checks
    from kai.engine.topup import eval_config

    runs = [row for row in run_history() if row["workflow_id"] == "low-balance-top-up"]
    assert len(runs) == 5
    assert sum(1 for row in runs if row["flagged"]) == 2
    chats = [row["chat"] for row in runs if row["origin"] == "chat"]
    assert round(sum(chats) / len(chats)) == 89
    assert round(sum(row["outcome"] for row in runs) / len(runs)) == 84
    groups = rollup_checks(runs, eval_config())
    chat = {row["key"]: row for row in groups["chat"]}
    outcome = {row["key"]: row for row in groups["outcome"]}
    assert chat["intent"]["average"] == 67
    assert chat["intent"]["runs"] == ["R-10234"]
    assert chat["slots"]["average"] == 100
    assert chat["turns"]["runs"] == ["R-10231"]
    assert chat["lines"]["runs"] == ["R-10233"]
    assert chat["guardrails"]["lost"] == 0
    assert outcome["right_end"]["average"] == 90
    assert outcome["right_end"]["runs"] == ["R-10234"]
    assert outcome["speed"]["runs"] == ["R-10231", "R-10232", "R-10235"]
    assert outcome["finished"]["runs"] == ["R-10235"]
    assert outcome["no_errors"]["average"] == 100
    assert sum(1 for row in run_history() if row["flagged"]) == 2


def test_live_rules_match_the_screen():
    payload = rules_payload(_rule_rows(), _names(), WORKFLOWS)
    assert payload["headline"] == {"attention": 1, "total": 10, "on": 8, "paused": 2}
    counts = {row["name"]: row["count"] for row in payload["workflows"]}
    assert counts == {
        "Low balance top-up": 2,
        "Low balance alert": 3,
        "FX rate watch": 2,
        "Just-in-time card top-up": 3,
    }
    rule = next(row for row in payload["rows"] if row["code"] == "LR-3120")
    assert rule["since_label"] == "2 Sep"
    assert rule["workflow_name"] == "Low balance top-up"
    assert rule["customer_name"] == "Thomas Reyes"
    assert rule["made_in"] == "In the app"
    assert rule["state"] == "on"
    assert rule["needs_look"] is True
    assert {item["label"]: item["value"] for item in rule["values"]} == {
        "Watches": "USD account",
        "Fires below": "$100.00",
        "Tops up to": "$400.00",
        "From": "EUR",
        "Cap": "$1,000 a run",
        "Runs": "1 a day",
        "Ask first": "always",
    }


def test_duration_labels():
    start = datetime(2026, 9, 22, 8, 15)
    assert duration_label(start, datetime(2026, 9, 22, 10, 29)) == "2h 14m"
    assert started_label(start) == "22 Sep 08:15"


def test_run_ended_on_and_turns_stay_inside_the_run():
    assert run_ended_on({"ended_on": "", "result": "not_served", "status": "completed"}) == "not served"
    assert run_ended_on({"ended_on": "", "result": None, "status": "waiting", "step_id": "ask_residence"}) == "waiting · ask_residence"
    start = datetime(2026, 11, 4, 9, 0, 0)
    end = datetime(2026, 11, 4, 9, 0, 30)
    turns = [
        {"text": "check balance", "workflow_key": "", "action": "answer", "created_at": start - timedelta(minutes=2)},
        {"text": "demo list", "workflow_key": "demo-list", "action": "start", "created_at": start},
        {"text": "Other", "workflow_key": "demo-list", "action": "choice", "created_at": start + timedelta(seconds=5)},
        {"text": "check balance", "workflow_key": "", "action": "answer", "tool": "balance", "created_at": end + timedelta(seconds=3)},
        {"text": "austria", "workflow_key": "", "action": "chat", "created_at": end + timedelta(seconds=1)},
    ]
    kept = turns_for_run({"started_at": start, "finished_at": end, "workflow_id": "demo-list"}, turns)
    assert [row["text"] for row in kept] == ["demo list", "Other"]
    assert all(row.get("tool") != "balance" for row in kept)
