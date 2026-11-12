from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


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
    inverter_manufacturer: str | None = None
    inverter_model: str | None = None
    monitoring_platform: str | None = None
    sems_station_id: str | None = None
    notes: str | None = None


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
    inverter_manufacturer: str | None = None
    inverter_model: str | None = None
    monitoring_platform: str | None = None
    sems_station_id: str | None = None
    notes: str | None = None


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
    inverter_manufacturer: str | None
    inverter_model: str | None
    monitoring_platform: str | None
    sems_station_id: str | None
    is_demo: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime
    inverters: list[InverterOut] = []
