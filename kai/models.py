from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _id() -> str:
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(200))


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(40))


class Chatbot(Base):
    __tablename__ = "chatbots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    public_key: Mapped[str] = mapped_column(String(200), unique=True)


class StepType(Base):
    __tablename__ = "step_types"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    ports: Mapped[list] = mapped_column(JSONB)


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    chatbot_id: Mapped[str] = mapped_column(ForeignKey("chatbots.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    group_name: Mapped[str] = mapped_column(String(80), default="")
    channels: Mapped[list] = mapped_column(JSONB)
    draft: Mapped[dict] = mapped_column(JSONB)
    published_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column()
    graph: Mapped[dict] = mapped_column(JSONB)
    chat_contract: Mapped[dict] = mapped_column(JSONB)
    stories: Mapped[list] = mapped_column(JSONB)
    eval_config: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    handle: Mapped[str] = mapped_column(String(80))


class CustomerRule(Base):
    __tablename__ = "customer_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    customer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    source_step: Mapped[str] = mapped_column(String(80))
    params: Mapped[dict] = mapped_column(JSONB)
    values: Mapped[list] = mapped_column(JSONB, default=list)
    made_in: Mapped[str] = mapped_column(String(80), default="")
    since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    chatbot_id: Mapped[str] = mapped_column(ForeignKey("chatbots.id", ondelete="CASCADE"))
    temporal_id: Mapped[str] = mapped_column(String(200))


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(32), index=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    temporal_id: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40))
    step_id: Mapped[str] = mapped_column(String(80), default="")
    slots: Mapped[dict] = mapped_column(JSONB)
    trace: Mapped[list] = mapped_column(JSONB)
    result: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ledger: Mapped[dict] = mapped_column(JSONB)
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    timings_ms: Mapped[dict] = mapped_column(JSONB, default=dict)
    user_replies: Mapped[list] = mapped_column(JSONB, default=list)
    slots_from_message: Mapped[dict] = mapped_column(JSONB, default=dict)
    missing_slot_count: Mapped[int] = mapped_column(default=0)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    customer_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    rule_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_on: Mapped[str] = mapped_column(String(200), default="")


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    step_id: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    retries: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RunScore(Base):
    __tablename__ = "run_scores"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(80), unique=True)
    chat: Mapped[Optional[float]] = mapped_column(nullable=True)
    outcome: Mapped[Optional[float]] = mapped_column(nullable=True)
    checks: Mapped[dict] = mapped_column(JSONB)
    flagged: Mapped[bool] = mapped_column(default=False)
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    reviewer_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(String(400), default="")
    host_allowlist: Mapped[list] = mapped_column(JSONB, default=list)
