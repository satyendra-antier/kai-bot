"""Load the demo tenant, the catalog, and the six live-demo workflows."""

import time

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from kai.db import session_scope
from kai.engine.catalog import bundles
from kai.engine.demos import bundles as demo_bundles
from kai.engine.registry import STEP_TYPES
from kai.intelligence import CUSTOMERS, live_rules, run_history
from kai.models import Chatbot, Connector, Customer, CustomerRule, Membership, Run, RunScore, StepType, Tenant, User, Workflow, WorkflowVersion


def _upgrade() -> None:
    last = None
    for _ in range(30):
        try:
            command.upgrade(Config("alembic.ini"), "head")
            return
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"database is not ready: {last}")


def _draft(graph: dict, contract: dict, suite: list, config: dict, built_from: str = "") -> dict:
    from kai.engine.never import with_forbidden
    from kai.engine.slots import sync_try_prompts

    synced = sync_try_prompts(dict(contract or {}))
    synced["never"] = [with_forbidden(rule) if isinstance(rule, dict) else rule for rule in (synced.get("never") or [])]
    payload = {
        "graph": graph,
        "chat_contract": synced,
        "stories": suite,
        "eval_config": config,
    }
    if built_from:
        payload["built_from"] = built_from
    return payload


def _add_workflow(session, package: dict, built_from: str = "") -> None:
    draft = _draft(package["graph"], package["chat_contract"], package["stories"], package["eval_config"], built_from=built_from)
    contract = draft["chat_contract"]
    row = Workflow(
        id=package["key"],
        tenant_id="demo",
        chatbot_id="bot",
        key=package["key"],
        name=package["name"],
        summary=package["summary"],
        group_name=package["group"],
        channels=package["channels"],
        draft=draft,
    )
    session.add(row)
    session.flush()
    published = WorkflowVersion(
        tenant_id="demo",
        workflow_id=row.id,
        version=1,
        graph=package["graph"],
        chat_contract=contract,
        stories=package["stories"],
        eval_config=package["eval_config"],
    )
    session.add(published)
    session.flush()
    row.published_version_id = published.id


def _rule(item: dict) -> CustomerRule:
    return CustomerRule(
        id=item["id"],
        tenant_id="demo",
        workflow_id=item["workflow_id"],
        customer_id=item["customer_id"],
        code=item["code"],
        source_step=item["source_step"],
        params=item["params"],
        values=item["values"],
        made_in=item["made_in"],
        since=item["since"],
        enabled=item["enabled"],
    )


def main() -> None:
    _upgrade()
    packaged = bundles()
    with session_scope() as session:
        session.execute(text("DELETE FROM tenants WHERE id = 'demo'"))
        session.add(Tenant(id="demo", name="Demo"))
        session.flush()
        session.add(User(id="user-demo", tenant_id="demo", email="demo@kai.local"))
        session.flush()
        for item in CUSTOMERS:
            session.add(Customer(id=item["id"], tenant_id="demo", name=item["name"], handle=item["handle"]))
        session.add(Customer(id="cust-demo-passkey", tenant_id="demo", name="Demo account", handle="demo-passkey"))
        session.flush()
        session.add(Membership(id="mem-demo", tenant_id="demo", user_id="user-demo", role="owner"))
        session.add(Chatbot(id="bot", tenant_id="demo", name="Kai", public_key="demo-key"))
        session.flush()
        for item in STEP_TYPES:
            session.add(StepType(tenant_id="demo", type=item["type"], kind=item["kind"], title=item["title"], ports=item["ports"]))
        for item in ("get_balance", "get_accounts", "get_rate", "get_quote", "move_money"):
            session.add(Connector(tenant_id="demo", type=item, base_url="", host_allowlist=[]))

        for package in packaged:
            _add_workflow(session, package)
        for package in demo_bundles():
            _add_workflow(session, package, built_from="demo")
        for item in live_rules():
            session.add(_rule(item))
        session.flush()
        for item in run_history():
            session.add(
                Run(
                    id=item["id"],
                    tenant_id="demo",
                    workflow_id=item["workflow_id"],
                    temporal_id=item["id"],
                    origin=item["origin"],
                    status=item["status"],
                    step_id=item["result"],
                    slots={},
                    trace=[],
                    result=item["result"],
                    ledger={},
                    messages=[],
                    timings_ms={},
                    user_replies=[],
                    slots_from_message={},
                    code=item["code"],
                    customer_id=item["customer_id"],
                    rule_id=item["rule_id"] or None,
                    started_at=item["started_at"],
                    finished_at=item["finished_at"],
                    ended_on=item["ended_on"],
                )
            )
            session.add(
                RunScore(
                    tenant_id="demo",
                    run_id=item["id"],
                    chat=item["chat"],
                    outcome=item["outcome"],
                    checks=item.get("checks") or {},
                    flagged=item["flagged"],
                    reasons=item["reasons"],
                )
            )
    print("seeded tenant demo, catalog, six demos, rule LR-3120, api key demo-key")


if __name__ == "__main__":
    main()
