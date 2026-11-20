"""FastAPI surface. The API signals Temporal and reads Postgres. It does not step the graph."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
from temporalio.service import RPCError

from kai.config import TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, langsmith_enabled
from kai.db import session_scope
from kai.engine.flow import explain_run
from kai.intelligence import rules_payload, runs_payload
from kai.models import Chatbot, Conversation, Customer, CustomerRule, Message, Run, RunEvent, RunScore, StepType, Workflow, WorkflowVersion
from kai.engine.catalog import bundles, stub_graph
from kai.engine.registry import KINDS, STEP_TYPES
from kai.engine.scoring import rollup_checks
from kai.engine.slots import sync_try_prompts
from kai.engine.topup import PREVIEW_LEDGER, eval_config
from kai.service import preview_run, require_weights, rescore, story_failures, trace_story, try_utterance
from kai.workflows import ConversationWorkflow, RunWorkflow

ROOT = Path(__file__).resolve().parent
DEMO_CONVERSATION = "demo"


class MessageIn(BaseModel):
    text: str
    workflow_id: Optional[str] = None
    persona: Optional[str] = "guest"
    customer_id: Optional[str] = None


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


@app.get("/ops", response_class=HTMLResponse)
async def ops_page():
    return _OPS_HTML


@app.get("/v1/ops")
async def ops_json(bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    health_checks = await health()
    workflows = session.query(Workflow).filter(Workflow.tenant_id == bot.tenant_id).order_by(Workflow.name).all()
    run_count = session.query(Run).filter(Run.tenant_id == bot.tenant_id).count()
    event_count = session.query(RunEvent).filter(RunEvent.tenant_id == bot.tenant_id).count()
    message_count = session.query(Message).filter(Message.tenant_id == bot.tenant_id).count()
    return {
        "health": health_checks,
        "langsmith": {"enabled": langsmith_enabled(), "project": "kai-bot"},
        "temporal": {
            "namespace": TEMPORAL_NAMESPACE,
            "task_queue": TASK_QUEUE,
            "address": TEMPORAL_ADDRESS,
            "workflow_types": ["ConversationWorkflow", "RunWorkflow"],
            "ui": "http://localhost:8081",
            "note": "Catalog money workflows live in Postgres. Temporal only runs ConversationWorkflow (chat router) and RunWorkflow (one graph walk).",
        },
        "databases": {
            "kai": {
                "gui": "http://localhost:8082",
                "system": "PostgreSQL",
                "server": "postgresql",
                "user": "kai",
                "password": "kai",
                "database": "kai",
                "host_port": 5433,
            },
            "temporal": {
                "gui": "http://localhost:8082",
                "user": "temporal",
                "password": "temporal",
                "note": "Temporal auto-setup DBs on the same Postgres (temporal / temporal_visibility).",
            },
        },
        "urls": {
            "chat": "http://localhost:3000/chat",
            "admin": "http://localhost:3000/admin/",
            "docs": "http://localhost:3000/docs",
            "health": "http://localhost:3000/health",
            "ops": "http://localhost:3000/ops",
            "temporal_ui": "http://localhost:8081",
            "adminer": "http://localhost:8082",
            "redis": "localhost:6380",
        },
        "endpoints": _endpoint_catalog(),
        "catalog_workflows": [{"id": row.id, "key": row.key, "name": row.name, "channels": row.channels, "published": bool(row.published_version_id)} for row in workflows],
        "counts": {"workflows": len(workflows), "runs": run_count, "run_events": event_count, "messages": message_count},
    }


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
    return FileResponse(
        ROOT / "static" / "chat.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
        channels = body.channels or ["chat"]
        graph = stub_graph(["chat"], done_line=f"{name} is ready.")
        contract = {
            "description": name,
            "examples": [],
            "exclusions": [],
            "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
            "slots": [],
            "lines": {"done": f"{name} is ready."},
            "never": [],
            "try_prompts": [],
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
    run_ids = [run.id for run in session.query(Run.id).filter(Run.tenant_id == bot.tenant_id, Run.workflow_id == row.id).all()]
    if run_ids:
        session.query(RunScore).filter(RunScore.tenant_id == bot.tenant_id, RunScore.run_id.in_(run_ids)).delete(synchronize_session=False)
        session.query(RunEvent).filter(RunEvent.tenant_id == bot.tenant_id, RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
        session.query(Run).filter(Run.tenant_id == bot.tenant_id, Run.workflow_id == row.id).delete(synchronize_session=False)
    session.delete(row)
    return {"deleted": row.id}


@app.post("/v1/workflows/{workflow_id}/run")
async def run_workflow_now(workflow_id: str, body: RunIn, bot: Chatbot = Depends(tenant_bot), session: Session = Depends(get_session)):
    row = _workflow_for(session, bot.tenant_id, workflow_id)
    origin = body.origin if body.origin in {"chat", "rule"} else "chat"
    draft = row.draft or {}
    preview = preview_run(draft, body.text, origin)
    live = None
    if app.state.temporal is not None and (draft.get("graph") or {}).get("entries"):
        run_id = f"run:{bot.tenant_id}:{uuid4()}"
        contract = draft.get("chat_contract") or {}
        payload = {
            "tenant_id": bot.tenant_id,
            "run_id": run_id,
            "workflow_id": row.id,
            "version_id": row.published_version_id or "",
            "graph": draft.get("graph") or {},
            "contract": contract,
            "eval_config": draft.get("eval_config") or {},
            "origin": origin,
            "slots": {"origin": origin},
            "ledger": contract.get("preview_ledger") or {"accounts": {}},
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
        _apply_contract(draft)
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
    return try_utterance(row.draft or {}, body.text, workflow_key=row.key or row.id)


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
    known = {item["type"] for item in types}
    workflows = session.query(Workflow).filter(Workflow.tenant_id == bot.tenant_id).all()
    used = {item["type"]: [] for item in types}
    untyped = []
    custom_kind = []
    step_count = 0
    typed_count = 0
    for workflow in workflows:
        for step in ((workflow.draft or {}).get("graph") or {}).get("steps") or []:
            step_count += 1
            record = {
                "workflow": workflow.name,
                "workflow_id": workflow.id,
                "id": step.get("id"),
                "purpose": step.get("purpose") or step.get("title") or "",
                "kind": step.get("kind") or "",
                "type": step.get("type") or "",
            }
            step_type = step.get("type") or ""
            if step_type in known:
                typed_count += 1
                used[step_type].append(record)
            elif step_type:
                custom_kind.append(record)
            elif step.get("kind"):
                # Onboarding canvases use kind-only steps (question, review, …) by design.
                custom_kind.append(record)
            else:
                untyped.append(record)
    for item in types:
        item["used_as"] = used.get(item["type"], [])
        item["usage_count"] = len(item["used_as"])
    unused = [item["type"] for item in types if not item["used_as"]]
    return {
        "kinds": [{"id": kind, "label": kind.replace("_", " ")} for kind in KINDS],
        "types": types,
        "untyped": untyped,
        "custom_kind": custom_kind,
        "steps": step_count,
        "workflows": len(workflows),
        "sync": {
            "registry_types": len(types),
            "typed_steps": typed_count,
            "custom_kind_steps": len(custom_kind),
            "missing_type_steps": len(untyped),
            "unused_types": unused,
            "in_sync": len(untyped) == 0,
            "note": "Add step / new canvases use the registry types (incl. Ask with options). Onboarding/demo-list also use kind-only question steps. Workflow templates (+ Add workflow) copy a whole draft graph — they are not the same as step types.",
        },
    }


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
        persona = (body.persona or "guest").strip().lower() or "guest"
        if persona not in {"guest", "logged_in"}:
            persona = "guest"
        started = {
            "tenant_id": bot.tenant_id,
            "conversation_id": conversation_id,
            "chatbot_id": bot.id,
            "workflow_id": body.workflow_id or "",
            "persona": persona,
            "customer_id": (body.customer_id or "").strip() if persona == "logged_in" else "",
        }
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
        "temporal_id": f"conv:{bot.tenant_id}:{conversation_id}",
        "messages": [
            {
                "role": row.role,
                "text": row.text,
                "payload": row.payload or {},
                "ai_steps": list((row.payload or {}).get("ai_steps") or []),
                "confidence": (row.payload or {}).get("confidence"),
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            }
            for row in messages
        ],
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


def _run_confidence(events: list, slots: dict) -> float | None:
    for event in events:
        if event.get("type") != "start":
            continue
        payload = event.get("payload") or {}
        if payload.get("confidence") is not None and payload.get("confidence") != "":
            try:
                return float(payload["confidence"])
            except (TypeError, ValueError):
                pass
    meta = (slots or {}).get("_intent") if isinstance(slots, dict) else None
    if isinstance(meta, dict) and meta.get("confidence") is not None:
        try:
            return float(meta["confidence"])
        except (TypeError, ValueError):
            return None
    return None


def _run_intent_source(events: list) -> str:
    for event in events:
        if event.get("type") == "start":
            return str((event.get("payload") or {}).get("intent_source") or "")
    return ""


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
                "confidence": _run_confidence(events.get(row.id) or [], row.slots or {}),
                "intent_source": _run_intent_source(events.get(row.id) or []),
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
    from kai.intelligence import turns_for_run

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
                "source": payload.get("source") or "",
                "confidence": payload.get("confidence"),
                "needs": payload.get("needs") or "none",
                "port": payload.get("port") or "",
                "slots": payload.get("slots") or {},
                "ai_steps": list(payload.get("ai_steps") or []),
                "created_at": message.created_at,
            }
        )
    for record in records:
        record["turns"] = turns_for_run(record, by_conversation.get(record["conversation_id"], []))
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
    events = (
        session.query(RunEvent)
        .filter(RunEvent.tenant_id == bot.tenant_id, RunEvent.run_id == run_id)
        .order_by(RunEvent.created_at.asc())
        .all()
    )
    payload = _run_json(row, score)
    payload["temporal_id"] = row.temporal_id
    payload["conversation_id"] = row.conversation_id or ""
    payload["events"] = [
        {
            "step_id": event.step_id,
            "type": event.type,
            "payload": event.payload or {},
            "retries": event.retries,
            "created_at": event.created_at.isoformat() + "Z" if event.created_at else None,
        }
        for event in events
    ]
    graph = {}
    if row.version_id:
        version = session.get(WorkflowVersion, row.version_id)
        if version is not None:
            graph = version.graph or {}
    if not graph:
        workflow = session.get(Workflow, row.workflow_id)
        graph = ((workflow.draft or {}).get("graph") if workflow else {}) or {}
    payload["flow"] = explain_run(
        graph,
        list(row.trace or []),
        row.slots or {},
        list(row.messages or []),
        list(row.user_replies or []),
        [{"step_id": event.step_id, "type": event.type, "payload": event.payload or {}} for event in events],
    )
    if row.conversation_id:
        from kai.intelligence import turns_for_run

        turns = []
        for message in (
            session.query(Message)
            .filter(Message.tenant_id == bot.tenant_id, Message.conversation_id == row.conversation_id, Message.role == "user")
            .order_by(Message.created_at.asc())
            .all()
        ):
            body = message.payload or {}
            if "ai_steps" not in body and "model_input" not in body:
                continue
            turns.append(
                {
                    "text": message.text,
                    "confidence": body.get("confidence"),
                    "action": body.get("action"),
                    "workflow_key": body.get("workflow_key"),
                    "source": body.get("source"),
                    "tool": body.get("tool") or "",
                    "route": body.get("route") or "",
                    "ai_steps": list(body.get("ai_steps") or []),
                    "created_at": message.created_at,
                }
            )
        payload["turns"] = turns_for_run(
            {
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "workflow_id": row.workflow_id,
            },
            turns,
        )
    return payload


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


def _apply_contract(draft: dict) -> None:
    from kai.engine.never import with_forbidden

    contract = draft.get("chat_contract")
    if isinstance(contract, dict):
        rules = contract.get("never") or []
        contract["never"] = [with_forbidden(rule) if isinstance(rule, dict) else rule for rule in rules]
        draft["chat_contract"] = sync_try_prompts(contract)
    _apply_lines(draft)


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
    draft = copy.deepcopy(row.draft or {})
    contract = draft.get("chat_contract")
    if isinstance(contract, dict):
        draft["chat_contract"] = sync_try_prompts(contract)
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


def _endpoint_catalog() -> list[dict]:
    return [
        {"method": "GET", "path": "/health", "auth": False, "use": "Service health"},
        {"method": "GET", "path": "/ops", "auth": False, "use": "Ops GUI — links, Temporal vs DB, how to verify"},
        {"method": "GET", "path": "/v1/ops", "auth": True, "use": "JSON ops catalog + counts"},
        {"method": "GET", "path": "/docs", "auth": False, "use": "OpenAPI / Swagger"},
        {"method": "GET", "path": "/chat", "auth": False, "use": "Customer chat widget"},
        {"method": "GET", "path": "/admin/", "auth": False, "use": "Backoffice"},
        {"method": "GET", "path": "/v1/workflows", "auth": True, "use": "List catalog workflows (Postgres)"},
        {"method": "GET", "path": "/v1/workflows/{id}", "auth": True, "use": "Workflow draft detail"},
        {"method": "POST", "path": "/v1/workflows", "auth": True, "use": "Create workflow from template"},
        {"method": "PUT", "path": "/v1/workflows/{id}/draft", "auth": True, "use": "Save draft (live chat picks this up)"},
        {"method": "POST", "path": "/v1/workflows/{id}/publish", "auth": True, "use": "Snapshot for rules / eval"},
        {"method": "POST", "path": "/v1/workflows/{id}/try", "auth": True, "use": "Try-it classifier + preview"},
        {"method": "POST", "path": "/v1/workflows/{id}/run", "auth": True, "use": "Start RunWorkflow in Temporal"},
        {"method": "DELETE", "path": "/v1/workflows/{id}", "auth": True, "use": "Delete template-built workflow"},
        {"method": "POST", "path": "/v1/conversations/{id}/messages", "auth": True, "use": "Chat turn → ConversationWorkflow"},
        {"method": "GET", "path": "/v1/conversations/{id}", "auth": True, "use": "Messages + ai_steps + live Temporal state"},
        {"method": "WS", "path": "/v1/conversations/{id}/ws", "auth": False, "use": "Live reply stream (Redis)"},
        {"method": "POST", "path": "/v1/rules/{id}/fire", "auth": True, "use": "Rule fire → RunWorkflow"},
        {"method": "GET", "path": "/v1/runs", "auth": True, "use": "Persisted runs (Postgres)"},
        {"method": "GET", "path": "/v1/runs/{id}", "auth": True, "use": "Run + events + flow + chat turns/ai_steps + scores"},
        {"method": "POST", "path": "/v1/runs/{id}/label", "auth": True, "use": "Reviewer label + rescore"},
        {"method": "GET", "path": "/v1/intelligence/runs", "auth": True, "use": "Intelligence runs drawer data"},
        {"method": "GET", "path": "/v1/intelligence/rules", "auth": True, "use": "Live rules board"},
        {"method": "GET", "path": "/v1/step-types", "auth": True, "use": "Step type registry"},
    ]


_OPS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kai ops</title>
  <style>
    :root { color-scheme: light; font-family: ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #f8fafc; color: #0f172a; }
    main { max-width: 920px; margin: 0 auto; padding: 32px 20px 64px; }
    h1 { font-size: 28px; margin: 0 0 8px; }
    h2 { font-size: 18px; margin: 28px 0 10px; }
    p, li { line-height: 1.5; color: #334155; }
    a { color: #1d4ed8; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
    .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px; }
    .card strong { display: block; margin-bottom: 4px; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    pre { background: #0f172a; color: #e2e8f0; padding: 14px; border-radius: 8px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e8f0; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e2e8f0; font-size: 13px; vertical-align: top; }
    th { background: #f1f5f9; }
    .ok { color: #047857; font-weight: 600; }
    .bad { color: #be123c; font-weight: 600; }
    .hint { font-size: 13px; color: #64748b; }
  </style>
</head>
<body>
<main>
  <h1>Kai ops</h1>
  <p class="hint">Verify DB, Temporal, API, and AI audit in one place. API key for JSON: <code>demo-key</code>.</p>

  <h2>Open these</h2>
  <div class="grid">
    <div class="card"><strong>Chat</strong><a href="/chat">/chat</a></div>
    <div class="card"><strong>Backoffice</strong><a href="/admin/">/admin/</a></div>
    <div class="card"><strong>API docs</strong><a href="/docs">/docs</a></div>
    <div class="card"><strong>Health</strong><a href="/health">/health</a></div>
    <div class="card"><strong>Temporal UI</strong><a href="http://localhost:8081" target="_blank" rel="noreferrer">:8081</a><span class="hint"> namespace <code>kai</code></span></div>
    <div class="card"><strong>DB GUI (Adminer)</strong><a href="http://localhost:8082" target="_blank" rel="noreferrer">:8082</a><span class="hint">System PostgreSQL · Server <code>postgresql</code></span></div>
  </div>

  <h2>Live health</h2>
  <pre id="health">Loading…</pre>

  <h2>Database login (Adminer)</h2>
  <table>
    <tr><th>Purpose</th><th>Server</th><th>Username</th><th>Password</th><th>Database</th></tr>
    <tr><td>Kai app data (workflows, runs, messages, scores)</td><td><code>postgresql</code></td><td><code>kai</code></td><td><code>kai</code></td><td><code>kai</code></td></tr>
    <tr><td>Temporal cluster</td><td><code>postgresql</code></td><td><code>temporal</code></td><td><code>temporal</code></td><td>pick Temporal DBs</td></tr>
  </table>
  <p class="hint">From the host, Postgres is also on <code>localhost:5433</code>.</p>

  <h2>Temporal vs catalog workflows</h2>
  <p><b>Postgres</b> stores the product workflows (<code>low-balance-top-up</code>, alerts, sweeps, demos…). <b>Temporal</b> only runs two workflow types:</p>
  <ul>
    <li><code>ConversationWorkflow</code> — id <code>conv:demo:…</code> — chat router, slots, starts children</li>
    <li><code>RunWorkflow</code> — id <code>run:demo:…</code> — walks one graph; persists <code>runs</code> + <code>run_events</code></li>
  </ul>
  <p>So: runs <em>execute</em> in Temporal; the durable record (trace, messages, scores, AI steps) lands in the <code>kai</code> database. Open Temporal UI for live execution; Adminer / Intelligence for the audit trail.</p>

  <h2>Where to see AI steps</h2>
  <ol>
    <li>Backoffice → Intelligence → open a run → <b>Chat audit · each turn</b></li>
    <li><code>GET /v1/conversations/{id}</code> → each user message <code>ai_steps</code></li>
    <li><code>GET /v1/runs/{id}</code> → <code>turns</code>, <code>events</code>, <code>flow</code>, <code>score.checks</code></li>
    <li>Optional LangSmith: set <code>LANGSMITH_API_KEY</code> + <code>LANGCHAIN_TRACING_V2=true</code> → LangGraph + Gemini traces at smith.langchain.com</li>
  </ol>

  <h2>Quick curl</h2>
  <pre>curl -s http://localhost:3000/health | jq
curl -s -H 'X-Api-Key: demo-key' http://localhost:3000/v1/ops | jq '.counts, .temporal, .urls'
curl -s -H 'X-Api-Key: demo-key' http://localhost:3000/v1/workflows | jq '.[].key'</pre>

  <p class="hint">JSON twin of this page: <code>GET /v1/ops</code> with header <code>X-Api-Key: demo-key</code>. Full write-up: <code>OPS.md</code> in the repo.</p>
</main>
<script>
fetch('/health').then(r => r.json()).then(data => {
  const lines = Object.entries(data.checks || data).map(([k,v]) => {
    const ok = v === 'ok';
    return `${ok ? 'OK' : '!!'}  ${k}: ${v}`;
  });
  document.getElementById('health').textContent = lines.join('\\n');
}).catch(err => {
  document.getElementById('health').textContent = String(err);
});
</script>
</body>
</html>
"""


admin_dir = ROOT / "static" / "admin"
if admin_dir.exists():
    app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin")
