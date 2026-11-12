from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class DateRange(BaseModel):
    start: date
    end: date


class MessageResponse(BaseModel):
    message: str
    details: dict[str, Any] | None = None


class SyncLogOut(ORMModel):
    id: int
    plant_id: int | None
    source: str
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    records_fetched: int
    records_inserted: int
    records_updated: int
    range_start: datetime | None
    range_end: datetime | None
    message: str | None
    error: str | None
    details: dict[str, Any] | None
