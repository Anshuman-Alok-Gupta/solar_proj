"""plants.tariff_slabs + monthly_consumption_kwh: slab tariffs under net metering

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plants", sa.Column("tariff_slabs", sa.JSON(), nullable=True))
    op.add_column("plants", sa.Column("monthly_consumption_kwh", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("plants", "monthly_consumption_kwh")
    op.drop_column("plants", "tariff_slabs")
