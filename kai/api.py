"""FastAPI surface. The API signals Temporal and reads Postgres. It does not step the graph."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
from temporalio.service import RPCError

from kai.config import TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
from kai.db import session_scope
from kai.engine.flow import explain_run
from kai.intelligence import rules_payload, runs_payload
from kai.models import Chatbot, Conversation, Customer, CustomerRule, Message, Run, RunEvent, RunScore, StepType, Workflow, WorkflowVersion
from kai.engine.catalog import bundles
from kai.engine.registry import KINDS, STEP_TYPES
from kai.engine.scoring import rollup_checks
from kai.engine.topup import PREVIEW_LEDGER, eval_config
from kai.service import preview_run, require_weights, rescore, story_failures, trace_story, try_utterance
from kai.workflows import ConversationWorkflow, RunWorkflow

ROOT = Path(__file__).resolve().parent
DEMO_CONVERSATION = "demo"


class MessageIn(BaseModel):
    text: str
    workflow_id: Optional[str] = None


class DraftIn(BaseModel):
    graph: Optional[dict] = None
    chat_contract: Optional[dict] = None
    stories: Optional[list] = None
    eval_config: Optional[dict] = None
    name: Optional[str] = None
    summary: Optional[str] = None


class LabelIn(BaseModel):
    label: str


class TryIn(BaseModel):
    text: str


class WorkflowIn(BaseModel):
    name: str
    channels: Optional[list] = None
    template_id: Optional[str] = None


class RunIn(BaseModel):
    text: str = ""
    origin: str = "chat"


def get_session():
    with session_scope() as session:
        yield session


def tenant_bot(x_api_key: str = Header(...), session: Session = Depends(get_session)) -> Chatbot:
    bot = session.query(Chatbot).filter(Chatbot.public_key == x_api_key).one_or_none()
    if bot is None:
        raise HTTPException(status_code=401, detail="unknown api key")
    return bot


def _workflow_for(session: Session, tenant_id: str, workflow_id: str) -> Workflow:
    row = session.get(Workflow, workflow_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


def _search(tenant_id: str, workflow_key: str, origin: str, conversation_id: str) -> Optional[TypedSearchAttributes]:
    try:
        return TypedSearchAttributes(
            [
                SearchAttributePair(SearchAttributeKey.for_keyword("TenantId"), tenant_id),
                SearchAttributePair(SearchAttributeKey.for_keyword("WorkflowKey"), workflow_key),
                SearchAttributePair(SearchAttributeKey.for_keyword("Origin"), origin),
                SearchAttributePair(SearchAttributeKey.for_keyword("ConversationId"), conversation_id),
            ]
        )
    except Exception:
        return None


async def _connect() -> Client:
    last = None
    for _ in range(60):
        try:
            return await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
        except Exception as exc:
            last = exc
            await asyncio.sleep(2)
    raise RuntimeError(str(last))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.temporal = None
    app.state.temporal_error = None
    try:
        app.state.temporal = await _connect()
    except Exception as exc:
        app.state.temporal_error = str(exc)
    yield


app = FastAPI(title="Kai", lifespan=lifespan)


def _client(app_state) -> Client:
    if app_state.temporal is None:
        raise HTTPException(status_code=503, detail=app_state.temporal_error or "temporal is not ready")
    return app_state.temporal


@app.get("/health")
async def health():
    checks = {"api": "ok", "postgres": "down", "redis": "down", "temporal": "down", "worker": "down"}
    try:
        with session_scope() as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = str(exc)
    try:
        import redis

        from kai.config import REDIS_URL

        client = redis.Redis.from_url(REDIS_URL)
        client.ping()
        checks["redis"] = "ok"
        checks["worker"] = "ok" if client.get("worker:heartbeat") else "down"
    except Exception as exc:
        checks["redis"] = str(exc)
    if app.state.temporal is not None:
        checks["temporal"] = "ok"
    else:
        checks["temporal"] = app.state.temporal_error or "down"
    ok = all(value == "ok" for value in checks.values())
    return {"ok": ok, "checks": checks}


@app.get("/chat")
def chat_page():
    return FileResponse(ROOT / "static" / "chat.html")


@app.get("/v1/workflows")
def list_workflows(bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    rows = session.query(Workflow).filter(Workflow.tenant_id == bot.tenant_id).order_by(Workflow.name).all()
    return [_workflow_json(row) for row in rows]


@app.get("/v1/workflows/{workflow_id}")
def get_workflow(workflow_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    return _detail(session, _workflow_for(session, bot.tenant_id, workflow_id))


@app.post("/v1/workflows")
def create_workflow(body: WorkflowIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    taken = {row.id for row in session.query(Workflow.id).filter(Workflow.tenant_id == bot.tenant_id).all()}
    workflow_id = _workflow_key(name, taken)
    template = _workflow_for(session, bot.tenant_id, body.template_id) if body.template_id else None
    if template is not None:
        source = template.draft or {}
        graph = copy.deepcopy(source.get("graph") or {})
        contract = copy.deepcopy(source.get("chat_contract") or {})
        suite = copy.deepcopy(source.get("stories") or [])
        config = copy.deepcopy(source.get("eval_config") or eval_config())
        channels = list(template.channels or [])
        summary = template.summary or ""
        group_name = template.group_name or ""
        built_from = template.id
    else:
        channels = body.channels or ["chat", "rule"]
        graph = {"budget": 40, "entries": {}, "steps": [], "edges": []}
        contract = {
            "description": name,
            "examples": [name],
            "exclusions": [],
            "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
            "slots": [],
            "lines": {},
            "never": [],
            "try_prompts": [name],
            "preview_ledger": PREVIEW_LEDGER,
        }
        suite = []
        config = eval_config()
        summary = "Built from the four-lane template."
        group_name = ""
        built_from = "lanes"
    draft = {"graph": graph, "chat_contract": contract, "stories": suite, "eval_config": config, "built_from": built_from}
    row = Workflow(
        id=workflow_id,
        tenant_id=bot.tenant_id,
        chatbot_id=bot.id,
        key=workflow_id,
        name=name,
        summary=summary,
        group_name=group_name,
        channels=list(channels),
        draft=draft,
    )
    session.add(row)
    session.flush()
    version = WorkflowVersion(
        tenant_id=row.tenant_id,
        workflow_id=row.id,
        version=1,
        graph=graph,
        chat_contract=contract,
        stories=suite,
        eval_config=config,
    )
    session.add(version)
    session.flush()
    row.published_version_id = version.id
    return _detail(session, row)


@app.delete("/v1/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    if not (row.draft or {}).get("built_from"):
        raise HTTPException(status_code=409, detail="only a workflow built from a template can be deleted")
    session.delete(row)
    return {"deleted": row.id}


@app.post("/v1/workflows/{workflow_id}/run")
async def run_workflow_now(workflow_id: str, body: RunIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    origin = body.origin if body.origin in {"chat", "rule"} else "chat"
    preview = preview_run(row.draft or {}, body.text, origin)
    live = None
    version = session.get(WorkflowVersion, row.published_version_id) if row.published_version_id else None
    if version is not None and app.state.temporal is not None:
        run_id = f"run:{bot.tenant_id}:{uuid4()}"
        payload = {
            "tenant_id": bot.tenant_id,
            "run_id": run_id,
            "workflow_id": row.id,
            "version_id": version.id,
            "graph": version.graph,
            "contract": version.chat_contract,
            "eval_config": version.eval_config,
            "origin": origin,
            "slots": {"origin": origin},
            "ledger": (version.chat_contract or {}).get("preview_ledger") or {"accounts": {}},
            "slots_from_message": {},
            "missing_slot_count": 0,
        }
        client = _client(app.state)
        attributes = _search(bot.tenant_id, row.key, origin, "")
        kwargs = {"search_attributes": attributes} if attributes is not None else {}
        try:
            await client.start_workflow(RunWorkflow.run, payload, id=run_id, task_queue=TASK_QUEUE, **kwargs)
        except Exception:
            await client.start_workflow(RunWorkflow.run, payload, id=run_id, task_queue=TASK_QUEUE)
        live = run_id
    return {**preview, "run_id": live}


@app.put("/v1/workflows/{workflow_id}/draft")
def save_draft(workflow_id: str, body: DraftIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    draft = dict(row.draft or {})
    if body.graph is not None:
        draft["graph"] = body.graph
    if body.chat_contract is not None:
        draft["chat_contract"] = body.chat_contract
        _apply_lines(draft)
    if body.stories is not None:
        draft["stories"] = body.stories
    if body.eval_config is not None:
        try:
            require_weights(body.eval_config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        draft["eval_config"] = body.eval_config
    row.draft = draft
    if body.name:
        row.name = body.name
    if body.summary is not None:
        row.summary = body.summary
    session.add(row)
    return _detail(session, row)


@app.post("/v1/workflows/{workflow_id}/publish")
def publish(workflow_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    draft = row.draft or {}
    failures = story_failures(draft.get("graph") or {}, draft.get("stories") or [])
    if failures:
        raise HTTPException(status_code=409, detail={"stories": failures})
    current = session.query(WorkflowVersion).filter(WorkflowVersion.workflow_id == row.id).count()
    version = WorkflowVersion(
        tenant_id=row.tenant_id,
        workflow_id=row.id,
        version=current + 1,
        graph=draft.get("graph") or {},
        chat_contract=draft.get("chat_contract") or {},
        stories=draft.get("stories") or [],
        eval_config=draft.get("eval_config") or {},
    )
    session.add(version)
    session.flush()
    row.published_version_id = version.id
    return {"version_id": version.id, "version": version.version}


@app.post("/v1/workflows/{workflow_id}/try")
def try_it(workflow_id: str, body: TryIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    return try_utterance(row.draft or {}, body.text)


@app.post("/v1/workflows/{workflow_id}/stories/{story_id}/trace")
def story_trace(workflow_id: str, story_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    try:
        return trace_story(row.draft or {}, story_id)
    except StopIteration:
        raise HTTPException(status_code=404, detail="story not found") from None


@app.get("/v1/workflows/{workflow_id}/eval")
def eval_summary(workflow_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    runs = session.query(Run).filter(Run.tenant_id == bot.tenant_id, Run.workflow_id == row.id).all()
    scores = session.query(RunScore).filter(RunScore.tenant_id == bot.tenant_id, RunScore.run_id.in_([run.id for run in runs] or [""])).all()
    return _eval_payload(row, runs, scores)


@app.put("/v1/workflows/{workflow_id}/eval-config")
def save_eval(workflow_id: str, body: dict, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    try:
        require_weights(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    draft = dict(row.draft or {})
    draft["eval_config"] = body
    row.draft = draft
    return body


@app.get("/v1/step-types")
def step_types(bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    catalog = {item["type"]: item for item in STEP_TYPES}
    rows = session.query(StepType).filter(StepType.tenant_id == bot.tenant_id).all()
    seen = set()
    types = []
    for row in rows:
        extra = catalog.get(row.type, {})
        seen.add(row.type)
        types.append(_step_type_json(row.type, row.kind, row.title, row.ports, extra))
    for item in STEP_TYPES:
        if item["type"] not in seen:
            types.append(_step_type_json(item["type"], item["kind"], item["title"], item["ports"], item))
    workflows = session.query(Workflow).filter(Workflow.tenant_id == bot.tenant_id).all()
    used = {item["type"]: [] for item in types}
    untyped = []
    step_count = 0
    for workflow in workflows:
        for step in ((workflow.draft or {}).get("graph") or {}).get("steps") or []:
            step_count += 1
            record = {"workflow": workflow.name, "workflow_id": workflow.id, "id": step.get("id"), "purpose": step.get("purpose") or step.get("title") or ""}
            if not step.get("type"):
                untyped.append(record)
            elif step["type"] in used:
                used[step["type"]].append(record)
    for item in types:
        item["used_as"] = used.get(item["type"], [])
    return {"kinds": [{"id": kind, "label": kind.replace("_", " ")} for kind in KINDS], "types": types, "untyped": untyped, "steps": step_count, "workflows": len(workflows)}


@app.post("/v1/conversations/{conversation_id}/messages")
async def post_message(conversation_id: str, body: MessageIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    temporal_id = f"conv:{bot.tenant_id}:{conversation_id}"
    existing = session.get(Conversation, conversation_id)
    if existing is None:
        session.add(Conversation(id=conversation_id, tenant_id=bot.tenant_id, chatbot_id=bot.id, temporal_id=temporal_id))
    session.add(Message(tenant_id=bot.tenant_id, conversation_id=conversation_id, role="user", text=body.text, payload={}))
    session.flush()
    client = _client(app.state)
    handle = client.get_workflow_handle(temporal_id)
    try:
        await handle.signal(ConversationWorkflow.user_message, body.text)
    except RPCError:
        attributes = _search(bot.tenant_id, "", "chat", conversation_id)
        kwargs = {}
        if attributes is not None:
            kwargs["search_attributes"] = attributes
        started = {"tenant_id": bot.tenant_id, "conversation_id": conversation_id, "chatbot_id": bot.id, "workflow_id": body.workflow_id or ""}
        try:
            await client.start_workflow(
                ConversationWorkflow.run,
                started,
                id=temporal_id,
                task_queue=TASK_QUEUE,
                start_signal="user_message",
                start_signal_args=[body.text],
                **kwargs,
            )
        except Exception:
            await client.start_workflow(
                ConversationWorkflow.run,
                started,
                id=temporal_id,
                task_queue=TASK_QUEUE,
                start_signal="user_message",
                start_signal_args=[body.text],
            )
    return {"conversation_id": conversation_id, "temporal_id": temporal_id}


@app.get("/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    messages = (
        session.query(Message)
        .filter(Message.tenant_id == bot.tenant_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    live = {}
    if app.state.temporal is not None:
        handle = app.state.temporal.get_workflow_handle(f"conv:{bot.tenant_id}:{conversation_id}")
        try:
            live = await handle.query(ConversationWorkflow.state)
        except Exception:
            live = {}
    child = {}
    child_id = live.get("child_id") if isinstance(live, dict) else None
    if child_id and app.state.temporal is not None:
        try:
            child = await app.state.temporal.get_workflow_handle(child_id).query(RunWorkflow.state)
        except Exception:
            child = {}
    return {
        "conversation_id": conversation_id,
        "messages": [{"role": row.role, "text": row.text} for row in messages],
        "live": live,
        "run": child,
    }


@app.post("/v1/rules/{rule_id}/fire")
async def fire_rule(rule_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    rule = session.get(CustomerRule, rule_id)
    if rule is None or rule.tenant_id != bot.tenant_id:
        rule = (
            session.query(CustomerRule)
            .filter(CustomerRule.tenant_id == bot.tenant_id, CustomerRule.code == rule_id)
            .one_or_none()
        )
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    workflow = _workflow_for(session, bot.tenant_id, rule.workflow_id)
    version = session.get(WorkflowVersion, workflow.published_version_id) if workflow.published_version_id else None
    if version is None:
        raise HTTPException(status_code=409, detail="workflow is not published")
    run_id = f"run:{bot.tenant_id}:{uuid4()}"
    client = _client(app.state)
    payload = {
        "tenant_id": bot.tenant_id,
        "run_id": run_id,
        "workflow_id": workflow.id,
        "version_id": version.id,
        "graph": version.graph,
        "contract": version.chat_contract,
        "eval_config": version.eval_config,
        "origin": "rule",
        "slots": rule.params,
        "ledger": (version.chat_contract or {}).get("preview_ledger") or {"accounts": {}},
        "slots_from_message": {},
        "missing_slot_count": 0,
        "customer_id": rule.customer_id,
        "rule_id": rule.id,
    }
    attributes = _search(bot.tenant_id, workflow.key, "rule", "")
    kwargs = {"search_attributes": attributes} if attributes is not None else {}
    try:
        await client.start_workflow(RunWorkflow.run, payload, id=run_id, task_queue=TASK_QUEUE, **kwargs)
    except Exception:
        await client.start_workflow(RunWorkflow.run, payload, id=run_id, task_queue=TASK_QUEUE)
    return {"run_id": run_id}


def _intelligence_records(session: Session, tenant_id: str) -> tuple:
    workflows = [{"id": row.id, "name": row.name} for row in session.query(Workflow).filter(Workflow.tenant_id == tenant_id).all()]
    customers = {row.id: row for row in session.query(Customer).filter(Customer.tenant_id == tenant_id).all()}
    rules = {row.id: row for row in session.query(CustomerRule).filter(CustomerRule.tenant_id == tenant_id).all()}
    scores = {row.run_id: row for row in session.query(RunScore).filter(RunScore.tenant_id == tenant_id).all()}
    versions = {row.id: row.graph for row in session.query(WorkflowVersion).filter(WorkflowVersion.tenant_id == tenant_id).all()}
    drafts = {row.id: (row.draft or {}).get("graph") or {} for row in session.query(Workflow).filter(Workflow.tenant_id == tenant_id).all()}
    events: dict[str, list] = {}
    for event in session.query(RunEvent).filter(RunEvent.tenant_id == tenant_id).order_by(RunEvent.created_at.asc()).all():
        events.setdefault(event.run_id, []).append({"step_id": event.step_id, "type": event.type, "payload": event.payload or {}})
    records = []
    for row in session.query(Run).filter(Run.tenant_id == tenant_id).all():
        customer = customers.get(row.customer_id)
        rule = rules.get(row.rule_id)
        score = scores.get(row.id)
        records.append(
            {
                "id": row.id,
                "code": row.code,
                "workflow_id": row.workflow_id,
                "customer_name": customer.name if customer else "",
                "customer_handle": customer.handle if customer else "",
                "rule_id": row.rule_id or "",
                "rule_code": rule.code if rule else "",
                "origin": row.origin,
                "status": row.status,
                "result": row.result,
                "ended_on": row.ended_on,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "chat": None if score is None else score.chat,
                "outcome": None if score is None else score.outcome,
                "flagged": bool(score and score.flagged),
                "reasons": [] if score is None else list(score.reasons or []),
                "checks": {} if score is None else (score.checks or {}),
                "trace": list(row.trace or []),
                "messages": list(row.messages or []),
                "conversation_id": row.conversation_id or "",
                "step_id": row.step_id or "",
                "turns": [],
                "flow": explain_run(
                    versions.get(row.version_id) or drafts.get(row.workflow_id) or {},
                    list(row.trace or []),
                    row.slots or {},
                    list(row.messages or []),
                    list(row.user_replies or []),
                    events.get(row.id) or [],
                ),
            }
        )
    by_conversation: dict[str, list] = {}
    for message in session.query(Message).filter(Message.tenant_id == tenant_id, Message.role == "user").order_by(Message.created_at.asc()).all():
        payload = message.payload or {}
        if "model_input" not in payload:
            continue
        by_conversation.setdefault(message.conversation_id, []).append(
            {
                "text": message.text,
                "tool": payload.get("tool") or "",
                "action": payload.get("action") or "",
                "workflow_key": payload.get("workflow_key") or "",
                "model_input": payload.get("model_input") or "",
                "pii": list(payload.get("pii") or []),
                "step_id": payload.get("step_id") or "",
                "route": payload.get("route") or "",
                "strike": payload.get("strike") or 0,
                "ai_steps": list(payload.get("ai_steps") or []),
            }
        )
    for record in records:
        record["turns"] = by_conversation.get(record["conversation_id"], [])
    return records, workflows


@app.get("/v1/intelligence/runs")
def intelligence_runs(
    workflow_id: str = "",
    rule: str = "",
    origin: str = "",
    flagged: str = "",
    bot: Chatbot = Depends(tenant_bot),
    session: Session = Depends(get_session),
):
    records, workflows = _intelligence_records(session, bot.tenant_id)
    rule_code = ""
    if rule:
        found = session.get(CustomerRule, rule)
        if found is None or found.tenant_id != bot.tenant_id:
            found = (
                session.query(CustomerRule)
                .filter(CustomerRule.tenant_id == bot.tenant_id, CustomerRule.code == rule)
                .one_or_none()
            )
        rule_code = found.code if found is not None else rule
    return runs_payload(
        records,
        workflows,
        workflow_id=workflow_id,
        rule_code=rule_code,
        origin=origin,
        flagged_only=flagged in {"1", "true", "yes"},
    )


@app.get("/v1/intelligence/rules")
def intelligence_rules(
    workflow_id: str = "",
    bot: Chatbot = Depends(tenant_bot),
    session: Session = Depends(get_session),
):
    records, workflows = _intelligence_records(session, bot.tenant_id)
    customers = {row.id: row for row in session.query(Customer).filter(Customer.tenant_id == bot.tenant_id).all()}
    rules = []
    for row in session.query(CustomerRule).filter(CustomerRule.tenant_id == bot.tenant_id).all():
        customer = customers.get(row.customer_id)
        rules.append(
            {
                "id": row.id,
                "code": row.code,
                "workflow_id": row.workflow_id,
                "customer_name": customer.name if customer else "",
                "customer_handle": customer.handle if customer else "",
                "made_in": row.made_in,
                "since": row.since,
                "values": row.values or [],
                "enabled": row.enabled,
            }
        )
    return rules_payload(rules, records, workflows, workflow_id=workflow_id)


@app.get("/v1/runs")
def list_runs(bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    rows = session.query(Run).filter(Run.tenant_id == bot.tenant_id).all()
    scores = {row.run_id: row for row in session.query(RunScore).filter(RunScore.tenant_id == bot.tenant_id).all()}
    return [_run_json(row, scores.get(row.id)) for row in rows]


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = session.get(Run, run_id)
    if row is None or row.tenant_id != bot.tenant_id:
        raise HTTPException(status_code=404, detail="run not found")
    score = session.query(RunScore).filter(RunScore.run_id == run_id).one_or_none()
    return _run_json(row, score)


@app.post("/v1/runs/{run_id}/label")
def label_run(run_id: str, body: LabelIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    if body.label not in {"right", "wrong"}:
        raise HTTPException(status_code=422, detail="label must be right or wrong")
    row = session.get(Run, run_id)
    if row is None or row.tenant_id != bot.tenant_id:
        raise HTTPException(status_code=404, detail="run not found")
    score = rescore(session, row, body.label)
    return {"flagged": score.flagged, "checks": score.checks, "reviewer_label": score.reviewer_label}


@app.websocket("/v1/conversations/{conversation_id}/ws")
async def conversation_ws(conversation_id: str, websocket: WebSocket):
    await websocket.accept()
    import redis.asyncio as aioredis

    from kai.config import REDIS_URL

    client = aioredis.from_url(REDIS_URL)
    pubsub = client.pubsub()
    await pubsub.subscribe(f"conv:{conversation_id}")
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("data"):
                data = message["data"]
                await websocket.send_text(data.decode() if isinstance(data, bytes) else data)
            await asyncio.sleep(0.1)
    except Exception:
        return
    finally:
        await pubsub.unsubscribe(f"conv:{conversation_id}")
        await client.aclose()


def _apply_lines(draft: dict) -> None:
    lines = (draft.get("chat_contract") or {}).get("lines") or {}
    graph = draft.get("graph") or {}
    for step in graph.get("steps") or []:
        if step["id"] in lines:
            step["line"] = lines[step["id"]]
    draft["graph"] = graph


def _workflow_key(name: str, taken: set) -> str:
    cleaned = []
    dash = False
    for char in name.lower():
        if char.isalnum():
            cleaned.append(char)
            dash = False
        elif not dash:
            cleaned.append("-")
            dash = True
    base = "".join(cleaned).strip("-")[:32] or "workflow"
    key = base
    suffix = 2
    while key in taken:
        extra = f"-{suffix}"
        key = f"{base[: 32 - len(extra)]}{extra}"
        suffix += 1
    return key


def _step_type_json(step_type: str, kind: str, title: str, ports: list, extra: dict) -> dict:
    return {
        "type": step_type,
        "kind": kind,
        "title": extra.get("title") or title,
        "ports": extra.get("ports") or ports,
        "purpose": extra.get("purpose") or "",
        "summary": extra.get("summary") or "",
        "slug": extra.get("slug") or step_type,
        "input": extra.get("input") or {},
        "output": extra.get("output") or {},
    }


def _graph_body(graph: dict | None) -> dict:
    graph = graph or {}
    steps = []
    for step in graph.get("steps") or []:
        if isinstance(step, dict):
            steps.append({key: value for key, value in step.items() if key != "position"})
        else:
            steps.append(step)
    return {"entries": graph.get("entries") or {}, "steps": steps, "edges": graph.get("edges") or []}


def _detail(session: Session, row: Workflow) -> dict:
    payload = _workflow_json(row, detail=True)
    version = session.get(WorkflowVersion, row.published_version_id) if row.published_version_id else None
    draft = row.draft or {}
    payload["draft_ahead"] = bool(version and (_graph_body(version.graph) != _graph_body(draft.get("graph") or {}) or version.chat_contract != (draft.get("chat_contract") or {})))
    return payload


def _workflow_json(row: Workflow, detail: bool = False) -> dict:
    draft = row.draft or {}
    graph = draft.get("graph") or {}
    payload = {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "summary": row.summary,
        "group": row.group_name,
        "channels": row.channels,
        "steps": len(graph.get("steps") or []),
        "stories": len(draft.get("stories") or []),
        "published_version_id": row.published_version_id,
        "built_from": draft.get("built_from") or "",
    }
    if detail:
        payload["draft"] = draft
    return payload


def _run_json(row: Run, score: Optional[RunScore]) -> dict:
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "origin": row.origin,
        "status": row.status,
        "step_id": row.step_id,
        "trace": row.trace,
        "result": row.result,
        "slots": row.slots,
        "messages": row.messages,
        "score": None
        if score is None
        else {
            "chat": score.chat,
            "outcome": score.outcome,
            "checks": score.checks,
            "flagged": score.flagged,
            "reasons": score.reasons,
            "reviewer_label": score.reviewer_label,
        },
    }


def _eval_payload(workflow: Workflow, runs: list[Run], scores: list[RunScore]) -> dict:
    by_run = {run.id: run for run in runs}
    chat_scores = [score for score in scores if by_run.get(score.run_id) and by_run[score.run_id].origin == "chat"]
    def avg(values: list) -> Optional[float]:
        clean = [value for value in values if value is not None]
        if not clean:
            return None
        return round(sum(clean) / len(clean), 2)

    config = (workflow.draft or {}).get("eval_config") or {}
    measured = [
        {"origin": by_run[score.run_id].origin, "code": by_run[score.run_id].code or score.run_id, "checks": score.checks or {}}
        for score in scores
        if score.run_id in by_run
    ]
    groups = rollup_checks(measured, config)
    return {
        "workflow_id": workflow.id,
        "runs": len(scores),
        "chat": avg([score.chat for score in chat_scores]),
        "outcome": avg([score.outcome for score in scores]),
        "flagged": sum(1 for score in scores if score.flagged),
        "config": config,
        "chat_checks": groups["chat"],
        "outcome_checks": groups["outcome"],
        "rows": [
            {
                "run_id": score.run_id,
                "origin": by_run[score.run_id].origin if score.run_id in by_run else "",
                "chat": score.chat,
                "outcome": score.outcome,
                "flagged": score.flagged,
                "checks": score.checks,
                "reasons": score.reasons,
            }
            for score in scores
            if score.run_id in by_run
        ],
    }


admin_dir = ROOT / "static" / "admin"
if admin_dir.exists():
    app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin")
