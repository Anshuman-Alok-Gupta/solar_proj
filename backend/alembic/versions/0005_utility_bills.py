"""utility_bills: electricity bills (numbers only) for measured savings

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    f = sa.Float()
    op.create_table(
        "utility_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plant_id", sa.Integer(), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bill_month", sa.Date(), nullable=False),
        sa.Column("bill_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("reading_from", sa.Date(), nullable=False),
        sa.Column("reading_to", sa.Date(), nullable=False),
        sa.Column("bill_period_months", f),
        sa.Column("tariff_category", sa.String(64)),
        sa.Column("sanctioned_load_kw", f),
        sa.Column("net_metered", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("import_kwh", f, nullable=False),
        sa.Column("export_kwh", f, nullable=False, server_default="0"),
        sa.Column("generation_kwh", f),
        sa.Column("billed_kwh", f),
        sa.Column("export_offset_kwh", f),
        sa.Column("banked_kwh", f),
        sa.Column("prev_banked_kwh", f),
        sa.Column("fixed_charge", f),
        sa.Column("energy_charge", f),
        sa.Column("wheeling_charge", f),
        sa.Column("fac_charge", f),
        sa.Column("electricity_duty", f),
        sa.Column("tax_on_sale", f),
        sa.Column("other_charges", f),
        sa.Column("current_bill", f),
        sa.Column("amount_payable", f),
        sa.Column("tariff", sa.JSON()),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plant_id", "bill_month", name="uq_utility_bill_month"),
    )
    op.create_index("ix_utility_bills_plant_id", "utility_bills", ["plant_id"])


def downgrade() -> None:
    op.drop_index("ix_utility_bills_plant_id", table_name="utility_bills")
    op.drop_table("utility_bills")
