"""Electricity bill payloads (numbers only; no personal details by design)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Units = Field(default=None, ge=0, le=1_000_000)
Money = Field(default=None, ge=-10_000_000, le=10_000_000)


class BillSlab(BaseModel):
    up_to_kwh: float | None = Field(default=None, gt=0)
    energy_rate: float = Field(ge=0, le=1000)
    fac_rate: float | None = Field(default=None, ge=0, le=1000)


class BillTariff(BaseModel):
    slabs: list[BillSlab] = Field(default_factory=list, max_length=12)
    wheeling_per_kwh: float | None = Field(default=None, ge=0, le=1000)
    duty_pct: float | None = Field(default=None, ge=0, le=100)

    @field_validator("slabs")
    @classmethod
    def _ordered(cls, slabs: list[BillSlab]) -> list[BillSlab]:
        prev = 0.0
        for i, s in enumerate(slabs):
            if s.up_to_kwh is None:
                if i != len(slabs) - 1:
                    raise ValueError("only the last slab may be open-ended")
            elif s.up_to_kwh <= prev:
                raise ValueError("slab limits must increase")
            else:
                prev = s.up_to_kwh
        return slabs


class BillIn(BaseModel):
    bill_month: date | None = None  # defaults to the month of bill_date (or reading_to)
    bill_date: date | None = None
    due_date: date | None = None
    reading_from: date
    reading_to: date
    bill_period_months: float | None = Field(default=None, gt=0, le=3)
    tariff_category: str | None = Field(default=None, max_length=64)
    sanctioned_load_kw: float | None = Field(default=None, ge=0, le=10_000)
    net_metered: bool = True
    import_kwh: float = Field(ge=0, le=1_000_000)
    export_kwh: float = Field(default=0.0, ge=0, le=1_000_000)
    generation_kwh: float | None = Units
    billed_kwh: float | None = Units
    export_offset_kwh: float | None = Units
    banked_kwh: float | None = Units
    prev_banked_kwh: float | None = Units
    fixed_charge: float | None = Money
    energy_charge: float | None = Money
    wheeling_charge: float | None = Money
    fac_charge: float | None = Money
    electricity_duty: float | None = Money
    tax_on_sale: float | None = Money
    other_charges: float | None = Money
    current_bill: float | None = Money
    amount_payable: float | None = Money
    tariff: BillTariff | None = None
    source: Literal["pdf", "manual"] = "manual"

    @model_validator(mode="after")
    def _dates(self) -> BillIn:
        if self.reading_to <= self.reading_from:
            raise ValueError("the current reading date must be after the previous reading date")
        if (self.reading_to - self.reading_from).days > 100:
            raise ValueError("a bill covers at most about three months")
        anchor = self.bill_month or self.bill_date or self.reading_to
        self.bill_month = anchor.replace(day=1)
        return self
