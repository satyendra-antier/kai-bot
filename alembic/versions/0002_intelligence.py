"""Customers, the values on a live rule, and the fields a run shows in user intelligence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _names(inspector) -> set:
    return set(inspector.get_table_names())


def _columns(inspector, table: str) -> set:
    if table not in _names(inspector):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "customers" not in _names(inspector):
        op.create_table(
            "customers",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("tenant_id", sa.String(length=32), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("handle", sa.String(length=80), nullable=False),
        )
        op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])

    inspector = sa.inspect(bind)
    rule_columns = _columns(inspector, "customer_rules")
    if "code" not in rule_columns:
        op.add_column("customer_rules", sa.Column("code", sa.String(length=40), nullable=False, server_default=""))
    if "customer_id" not in rule_columns:
        op.add_column(
            "customer_rules",
            sa.Column("customer_id", sa.String(length=32), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=True),
        )
    if "made_in" not in rule_columns:
        op.add_column("customer_rules", sa.Column("made_in", sa.String(length=80), nullable=False, server_default=""))
    if "since" not in rule_columns:
        op.add_column("customer_rules", sa.Column("since", sa.DateTime(), nullable=True))
    if "values" not in rule_columns:
        op.add_column(
            "customer_rules",
            sa.Column("values", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        )

    run_columns = _columns(inspector, "runs")
    if "code" not in run_columns:
        op.add_column("runs", sa.Column("code", sa.String(length=40), nullable=False, server_default=""))
    if "customer_id" not in run_columns:
        op.add_column("runs", sa.Column("customer_id", sa.String(length=32), nullable=True))
    if "rule_id" not in run_columns:
        op.add_column("runs", sa.Column("rule_id", sa.String(length=32), nullable=True))
    if "started_at" not in run_columns:
        op.add_column("runs", sa.Column("started_at", sa.DateTime(), nullable=True))
    if "finished_at" not in run_columns:
        op.add_column("runs", sa.Column("finished_at", sa.DateTime(), nullable=True))
    if "ended_on" not in run_columns:
        op.add_column("runs", sa.Column("ended_on", sa.String(length=200), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for column in ("ended_on", "finished_at", "started_at", "rule_id", "customer_id", "code"):
        if column in _columns(inspector, "runs"):
            op.drop_column("runs", column)
    for column in ("values", "since", "made_in", "customer_id", "code"):
        if column in _columns(inspector, "customer_rules"):
            op.drop_column("customer_rules", column)
    if "customers" in _names(inspector):
        op.drop_index("ix_customers_tenant_id", table_name="customers")
        op.drop_table("customers")
