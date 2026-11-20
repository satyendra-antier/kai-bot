"""Chat and outcome scores for a finished or abandoned run."""

from __future__ import annotations

from typing import Any

from kai.engine.advance import render_line, run_until_wait

CHAT_CHECKS = ("intent", "slots", "turns", "lines", "guardrails")
OUTCOME_CHECKS = ("right_end", "speed", "finished", "no_errors")

CHECK_META = [
    ("chat", "intent", "Intent", "label", "Right workflow for the request. Signals: the customer corrects Kai, drops off, or ends in another workflow; settled by a reviewer on flagged runs."),
    ("chat", "slots", "Slots", "exact", "Each value Kai filled from the message, against what the customer confirmed at the read-back. Every corrected value loses its share."),
    ("chat", "turns", "Turns to confirm", "exact", "Exchanges from the first message to the yes, against the ideal: one per missing slot, plus one for the read-back, plus one if the source was short."),
    ("chat", "lines", "Lines as written", "exact + judged", "What Kai said at each step against that step’s line with the values filled in. Word for word passes; a paraphrase goes to a judge that checks meaning and that every {value} survived."),
    ("chat", "guardrails", "Guardrails", "exact + judged", "Each “Kai never” rule is a check. Order-of-events rules are exact (a message before the approval event); the rest are judged on the transcript. Any breach scores 0 and flags the run."),
    ("outcome", "right_end", "Right end", "exact", "Replay the workflow against the data as it was at the time — balances, rates, limits. The run should land on the same end as the replay."),
    ("outcome", "speed", "Speed", "exact", "Time between steps from the run log, against a target per step."),
    ("outcome", "finished", "Finished", "exact", "Did the customer leave mid-way — an approval never opened, a conversation left before the read-back."),
    ("outcome", "no_errors", "No errors", "exact", "Retries, re-quotes and failed sends counted from the step logs."),
]


def rollup_checks(runs: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    weights = config.get("weights") or {}
    grouped: dict[str, list[dict[str, Any]]] = {"chat": [], "outcome": []}
    for group, key, name, mode, measure in CHECK_META:
        measured = []
        for run in runs:
            if group == "chat" and run.get("origin") != "chat":
                continue
            value = (run.get("checks") or {}).get(key)
            if value is None:
                continue
            measured.append((run.get("code") or run.get("id"), float(value)))
        lost = sorted(code for code, value in measured if value < 100)
        average = round(sum(value for _, value in measured) / len(measured)) if measured else None
        grouped[group].append(
            {
                "key": key,
                "name": name,
                "mode": mode,
                "measure": measure,
                "weight": float((weights.get(group) or {}).get(key) or 0),
                "average": average,
                "lost": len(lost),
                "of": len(measured),
                "runs": lost,
            }
        )
    return grouped


def weights_valid(config: dict[str, Any]) -> bool:
    weights = config.get("weights") or {}
    for group, names in (("chat", CHAT_CHECKS), ("outcome", OUTCOME_CHECKS)):
        values = [float((weights.get(group) or {}).get(name, 0)) for name in names]
        if abs(sum(values) - 100) > 0.01:
            return False
    return True


def score_run(run: dict[str, Any], graph: dict[str, Any], contract: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "intent": _intent(run),
        "slots": _slots(run),
        "turns": _turns(run),
        "lines": _lines(run, graph),
        "guardrails": _guardrails(run, contract, graph),
        "right_end": _right_end(run, graph),
        "speed": _speed(run, graph),
        "finished": _finished(run),
        "no_errors": _errors(run),
    }
    chat = _composite(checks, config, "chat", CHAT_CHECKS) if run.get("origin") == "chat" else None
    outcome = _composite(checks, config, "outcome", OUTCOME_CHECKS)
    guardrail_broken = checks["guardrails"] == 0
    flagged = guardrail_broken
    reasons = []
    if guardrail_broken:
        reasons.append("guardrail")
    if chat is not None and chat < float(config.get("chat_bar", 80)):
        flagged = True
        reasons.append("chat")
    if outcome < float(config.get("outcome_bar", 80)):
        flagged = True
        reasons.append("outcome")
    return {
        "chat": chat,
        "outcome": outcome,
        "checks": checks,
        "flagged": flagged,
        "reasons": reasons,
        "reviewer_label": run.get("reviewer_label"),
    }


def _composite(checks: dict[str, float], config: dict[str, Any], group: str, names: tuple[str, ...]) -> float:
    weights = (config.get("weights") or {}).get(group) or {}
    total = 0.0
    weight_sum = 0.0
    for name in names:
        weight = float(weights.get(name, 0))
        total += checks[name] * weight
        weight_sum += weight
    if weight_sum == 0:
        return 0.0
    return round(total / weight_sum, 2)


def _intent(run: dict[str, Any]) -> float:
    if run.get("reviewer_label") == "wrong":
        return 0.0
    if run.get("reviewer_label") == "right":
        return 100.0
    if run.get("customer_corrected") or run.get("ended_in_other_workflow"):
        return 0.0
    if run.get("origin") == "chat" and run.get("dropped_off"):
        return 0.0
    return 100.0


def _slots(run: dict[str, Any]) -> float:
    filled = run.get("slots_from_message") or {}
    confirmed = run.get("slots_confirmed") or {}
    if not filled:
        return 100.0
    ok = 0
    for key, value in filled.items():
        if confirmed.get(key, value) == value:
            ok += 1
    return round(100 * ok / len(filled), 2)


def _turns(run: dict[str, Any]) -> float:
    replies = run.get("user_replies") or []
    yes_at = None
    for index, reply in enumerate(replies):
        if str(reply).strip().lower() in {"yes", "y", "approve", "confirm"}:
            yes_at = index
            break
    if yes_at is None:
        return 100.0 if run.get("origin") != "chat" else 0.0
    actual = yes_at + 1
    ideal = int(run.get("missing_slot_count") or 0) + 1 + (1 if run.get("source_was_short") else 0)
    if ideal <= 0:
        return 100.0
    return round(100 * min(actual, ideal) / max(actual, ideal), 2)


def _lines(run: dict[str, Any], graph: dict[str, Any]) -> float:
    spoken = run.get("messages") or []
    if not spoken:
        return 100.0
    steps = {step["id"]: step for step in graph["steps"]}
    marks = []
    for message in spoken:
        step = steps.get(message.get("step_id"))
        if not step or not step.get("line"):
            continue
        expected = render_line(step["line"], run.get("slots") or {})
        text = message.get("text") or ""
        if text == expected or text == message.get("exact"):
            marks.append(100.0)
            continue
        if message.get("judge_ok") and _values_survived(expected, text):
            marks.append(100.0)
        else:
            marks.append(0.0)
    if not marks:
        return 100.0
    return round(sum(marks) / len(marks), 2)


def _values_survived(expected: str, actual: str) -> bool:
    tokens = [token for token in expected.replace("—", " ").split() if any(char.isdigit() for char in token) or token.isupper()]
    return all(token in actual for token in tokens)


def _guardrails(run: dict[str, Any], contract: dict[str, Any], graph: dict[str, Any] | None = None) -> float:
    from kai.engine.never import breach_never

    rules = contract.get("never") or []
    if not rules:
        return 100.0
    events = run.get("events") or []
    transcript = " ".join(message.get("text", "") for message in run.get("messages") or [])
    if breach_never(transcript, rules):
        return 0.0
    for rule in rules:
        if rule.get("mode") == "order":
            steps = {step["id"]: step for step in (graph or {}).get("steps") or []}
            approval_at = next(
                (
                    index
                    for index, event in enumerate(events)
                    if event.get("type") == "approval"
                    or event.get("step_id") in {"approve", "approve_topup", "approve_move"}
                    or (steps.get(event.get("step_id") or "") or {}).get("kind") == "approval"
                ),
                None,
            )
            message_before = any(message.get("text") for message in run.get("messages") or [])
            if approval_at is not None and not message_before:
                return 0.0
        elif rule.get("judge_breach"):
            return 0.0
    return 100.0


def _right_end(run: dict[str, Any], graph: dict[str, Any]) -> float:
    if run.get("replay_result"):
        return 100.0 if run["replay_result"] == run.get("result") else 0.0
    replayed = run_until_wait(
        graph,
        origin=run.get("origin") or "chat",
        slots=run.get("slots") or {},
        ledger=run.get("ledger") or {"accounts": {}},
        replies=list(run.get("user_replies") or []),
        run_id=run.get("id") or "replay",
    )
    return 100.0 if replayed.get("result") == run.get("result") else 0.0


def _speed(run: dict[str, Any], graph: dict[str, Any]) -> float:
    timings = run.get("timings_ms") or {}
    if not timings:
        return 100.0
    targets = {step["id"]: int(step.get("speed_target_ms") or 2000) for step in graph["steps"]}
    marks = []
    for step_id, duration in timings.items():
        marks.append(100.0 if float(duration) <= targets.get(step_id, 2000) else 0.0)
    return round(sum(marks) / len(marks), 2) if marks else 100.0


def _finished(run: dict[str, Any]) -> float:
    if run.get("status") == "completed":
        return 100.0
    if run.get("abandoned"):
        return 0.0
    return 100.0 if run.get("result") else 0.0


def _errors(run: dict[str, Any]) -> float:
    events = run.get("events") or []
    bad = 0
    for event in events:
        bad += int(event.get("retries") or 0)
        if event.get("failed"):
            bad += 1
    return 100.0 if bad == 0 else 0.0
