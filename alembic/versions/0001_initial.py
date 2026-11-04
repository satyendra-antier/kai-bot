"""Create the kai schema from the SQLAlchemy models."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from kai.models import Base

    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    from kai.models import Base

    Base.metadata.drop_all(op.get_bind())
