"""add prediction_context

Revision ID: 2c09afbbdcdf
Revises: 8df4e2013c5a
Create Date: 2026-05-14

"""
from alembic import op
import sqlalchemy as sa


revision = '2c09afbbdcdf'
down_revision = '8df4e2013c5a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("context", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "context")
