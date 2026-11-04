"""Upsert the three demo workflows and two scored runs each. Does not reseed."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _load_env() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_env()
    from kai.db import session_scope
    from kai.engine.advance import run_until_wait
    from kai.engine.demos import bundles
    from kai.engine.scoring import score_run
    from kai.models import Chatbot, Customer, Run, RunScore, Workflow, WorkflowVersion
    from kai.service import story_failures

    now = datetime.utcnow()
    with session_scope() as session:
        bot = session.query(Chatbot).filter(Chatbot.tenant_id == "demo").one()
        if session.get(Customer, "cust-demo-passkey") is None:
            session.add(Customer(id="cust-demo-passkey", tenant_id="demo", name="Demo account", handle="demo-passkey"))
        for package in bundles():
            failed = story_failures(package["graph"], package["stories"])
            if failed:
                raise SystemExit(f"{package['key']} stories failed: {', '.join(failed)}")
            draft = {
                "graph": package["graph"],
                "chat_contract": package["chat_contract"],
                "stories": package["stories"],
                "eval_config": package["eval_config"],
            }
            row = session.get(Workflow, package["key"])
            if row is None:
                row = Workflow(
                    id=package["key"],
                    tenant_id="demo",
                    chatbot_id=bot.id,
                    key=package["key"],
                    name=package["name"],
                    summary=package["summary"],
                    group_name=package["group"],
                    channels=package["channels"],
                    draft=draft,
                )
                session.add(row)
                session.flush()
            else:
                row.name = package["name"]
                row.summary = package["summary"]
                row.group_name = package["group"]
                row.channels = list(package["channels"])
                row.draft = draft
            if not row.published_version_id:
                published = WorkflowVersion(
                    tenant_id="demo",
                    workflow_id=row.id,
                    version=1,
                    graph=package["graph"],
                    chat_contract=package["chat_contract"],
                    stories=package["stories"],
                    eval_config=package["eval_config"],
                    created_at=now,
                )
                session.add(published)
                session.flush()
                row.published_version_id = published.id
            version_id = row.published_version_id
            by_id = {story["id"]: story for story in package["stories"]}
            for index, story_id in enumerate(package["sample_runs"], start=1):
                story = by_id[story_id]
                run_id = f"run:demo:{story_id}"
                if session.get(Run, run_id) is not None:
                    continue
                state = run_until_wait(
                    package["graph"],
                    origin=story["origin"],
                    slots=story.get("slots") or {},
                    ledger=story.get("ledger") or {},
                    replies=list(story.get("replies") or []),
                    run_id=run_id,
                )
                run = {
                    "id": run_id,
                    "origin": story["origin"],
                    "status": state["status"],
                    "result": state.get("result"),
                    "slots": state.get("slots") or {},
                    "messages": state.get("messages") or [],
                    "events": state.get("events") or [],
                    "user_replies": list(story.get("replies") or []),
                    "ledger": story.get("ledger") or {},
                    "missing_slot_count": 0,
                }
                scored = score_run(run, package["graph"], package["chat_contract"], package["eval_config"])
                session.add(
                    Run(
                        id=run_id,
                        tenant_id="demo",
                        workflow_id=row.id,
                        version_id=version_id,
                        temporal_id=run_id,
                        origin=story["origin"],
                        status=state["status"],
                        step_id=state.get("step_id") or "",
                        slots=state.get("slots") or {},
                        trace=state.get("trace") or [],
                        result=state.get("result"),
                        ledger=story.get("ledger") or {},
                        messages=state.get("messages") or [],
                        timings_ms={},
                        user_replies=list(story.get("replies") or []),
                        slots_from_message={},
                        missing_slot_count=0,
                        code={
                            "demo-balance": "D-BAL",
                            "demo-alert": "D-ALT",
                            "demo-move": "D-MOV",
                            "demo-login": "D-IN",
                            "demo-choose": "D-CH",
                            "demo-list": "D-LIST",
                        }[package["key"]]
                        + f"-{index}",
                        started_at=now,
                        finished_at=now,
                        ended_on=str(state.get("result") or "").replace("_", " "),
                    )
                )
                session.add(
                    RunScore(
                        tenant_id="demo",
                        run_id=run_id,
                        chat=scored["chat"],
                        outcome=scored["outcome"],
                        checks=scored["checks"],
                        flagged=scored["flagged"],
                        reasons=scored["reasons"],
                    )
                )
    print("demo workflows installed")


if __name__ == "__main__":
    main()
