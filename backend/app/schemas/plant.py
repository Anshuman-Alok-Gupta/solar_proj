from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class TariffSlab(BaseModel):
    """One step of a monthly slab tariff; ``up_to_kwh=None`` is the open-ended top slab."""

    up_to_kwh: float | None = Field(default=None, gt=0)
    rate: float = Field(ge=0)


def _check_slabs(v: list[TariffSlab] | None) -> list[TariffSlab] | None:
    if not v:
        return None
    tops = [s.up_to_kwh for s in v]
    if any(t is None for t in tops[:-1]):
        raise ValueError("only the last slab may be open-ended (no upper limit)")
    bounded = [t for t in tops if t is not None]
    if bounded != sorted(bounded) or len(set(bounded)) != len(bounded):
        raise ValueError("slab limits must be strictly increasing")
    return v


class PlantBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    capacity_kw: float | None = Field(default=None, gt=0, le=10000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = "UTC"
    panel_tilt_deg: float | None = Field(default=None, ge=0, le=90)
    panel_azimuth_deg: float | None = Field(default=None, ge=-180, le=180)
    installation_date: date | None = None
    installation_cost: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=8)
    tariff_import_per_kwh: float | None = Field(default=None, ge=0)
    tariff_export_per_kwh: float | None = Field(default=None, ge=0)
    tariff_slabs: list[TariffSlab] | None = None
    monthly_consumption_kwh: float | None = Field(default=None, gt=0, le=100000)
    inverter_manufacturer: str | None = None
    inverter_model: str | None = None
    monitoring_platform: str | None = None
    sems_station_id: str | None = None
    notes: str | None = None

    _slabs = field_validator("tariff_slabs")(_check_slabs)


class PlantCreate(PlantBase):
    is_demo: bool = False


class PlantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    capacity_kw: float | None = Field(default=None, gt=0, le=10000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = None
    panel_tilt_deg: float | None = Field(default=None, ge=0, le=90)
    panel_azimuth_deg: float | None = Field(default=None, ge=-180, le=180)
    installation_date: date | None = None
    installation_cost: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    tariff_import_per_kwh: float | None = Field(default=None, ge=0)
    tariff_export_per_kwh: float | None = Field(default=None, ge=0)
    tariff_slabs: list[TariffSlab] | None = None
    monthly_consumption_kwh: float | None = Field(default=None, gt=0, le=100000)
    inverter_manufacturer: str | None = None
    inverter_model: str | None = None
    monitoring_platform: str | None = None
    sems_station_id: str | None = None
    notes: str | None = None

    _slabs = field_validator("tariff_slabs")(_check_slabs)


class InverterOut(ORMModel):
    id: int
    plant_id: int
    serial_number: str
    name: str | None
    manufacturer: str | None
    model: str | None
    rated_power_kw: float | None
    source: str
    extra: dict | None
    created_at: datetime


class PlantOut(ORMModel):
    id: int
    owner_id: int
    name: str
    capacity_kw: float | None
    latitude: float | None
    longitude: float | None
    timezone: str
    panel_tilt_deg: float | None
    panel_azimuth_deg: float | None
    installation_date: date | None
    installation_cost: float | None
    currency: str
    tariff_import_per_kwh: float | None
    tariff_export_per_kwh: float | None
    tariff_slabs: list[dict] | None = None
    monthly_consumption_kwh: float | None = None
    inverter_manufacturer: str | None
    inverter_model: str | None
    monitoring_platform: str | None
    sems_station_id: str | None
    is_demo: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime
    inverters: list[InverterOut] = []
    # 'owner' | 'editor' | 'viewer' for the requesting user (set by the route)
    access_role: str = "owner"
