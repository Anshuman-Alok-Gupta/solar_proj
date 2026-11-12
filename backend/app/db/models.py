"""SQLAlchemy ORM models for the Solar Intelligence Platform.

Design notes
------------
* Every plant-specific record carries ``plant_id`` so the schema scales to many plants.
* Data provenance is explicit: ``source`` columns distinguish live API readings,
  imported history, weather observations vs forecasts and synthetic demo data.
* Timestamps are stored timezone-aware (UTC). ``DailyEnergy.date`` is the civil
  date in the plant's local timezone.
* PostgreSQL ``NULLS NOT DISTINCT`` unique constraints are used so nullable
  dimension columns (inverter_id, issued_at) still de-duplicate correctly.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DataSource(str, enum.Enum):
    SEMS_API = "sems_api"
    CSV_IMPORT = "csv_import"
    SYNTHETIC = "synthetic"


class WeatherKind(str, enum.Enum):
    OBSERVED = "observed"
    FORECAST = "forecast"


class SyncStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ImportStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    FAILED = "failed"


class AnomalySeverity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnomalyStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ReportKind(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# --------------------------------------------------------------------------- users


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    plants: Mapped[list[Plant]] = relationship(back_populates="owner")


# --------------------------------------------------------------------------- plants


class Plant(Base):
    __tablename__ = "plants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    capacity_kw: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    panel_tilt_deg: Mapped[float | None] = mapped_column(Float)
    panel_azimuth_deg: Mapped[float | None] = mapped_column(Float)  # Open-Meteo convention: 0=S, -90=E, 90=W
    installation_date: Mapped[date | None] = mapped_column(Date)
    installation_cost: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    tariff_import_per_kwh: Mapped[float | None] = mapped_column(Float)
    tariff_export_per_kwh: Mapped[float | None] = mapped_column(Float)
    inverter_manufacturer: Mapped[str | None] = mapped_column(String(128))
    inverter_model: Mapped[str | None] = mapped_column(String(128))
    monitoring_platform: Mapped[str | None] = mapped_column(String(128))
    sems_station_id: Mapped[str | None] = mapped_column(String(128))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="plants")
    inverters: Mapped[list[Inverter]] = relationship(back_populates="plant", cascade="all, delete-orphan")


class Inverter(Base):
    __tablename__ = "inverters"
    __table_args__ = (UniqueConstraint("plant_id", "serial_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    rated_power_kw: Mapped[float | None] = mapped_column(Float)
    source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="data_source"), nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    plant: Mapped[Plant] = relationship(back_populates="inverters")


# --------------------------------------------------------------------------- energy data


class EnergyReading(Base):
    """Interval reading (sub-daily). ``energy_kwh`` is energy produced in the interval ending at ``ts``."""

    __tablename__ = "energy_readings"
    __table_args__ = (
        UniqueConstraint("plant_id", "inverter_id", "ts", postgresql_nulls_not_distinct=True),
        Index("ix_energy_readings_plant_ts", "plant_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), nullable=False)
    inverter_id: Mapped[int | None] = mapped_column(ForeignKey("inverters.id", ondelete="SET NULL"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    power_w: Mapped[float | None] = mapped_column(Float)
    energy_kwh: Mapped[float | None] = mapped_column(Float)
    cumulative_energy_kwh: Mapped[float | None] = mapped_column(Float)
    pv_voltage_v: Mapped[float | None] = mapped_column(Float)
    pv_current_a: Mapped[float | None] = mapped_column(Float)
    ac_voltage_v: Mapped[float | None] = mapped_column(Float)
    ac_current_a: Mapped[float | None] = mapped_column(Float)
    frequency_hz: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="data_source"), nullable=False)
    quality_flags: Mapped[list[str] | None] = mapped_column(JSON)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id", ondelete="SET NULL"))
    sync_log_id: Mapped[int | None] = mapped_column(ForeignKey("sync_logs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DailyEnergy(Base):
    """One canonical row per plant per local civil date.

    When several sources cover the same date, precedence is API > import > synthetic
    (enforced in the upsert service, not the DB).
    """

    __tablename__ = "daily_energy"
    __table_args__ = (UniqueConstraint("plant_id", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    generation_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    peak_power_w: Mapped[float | None] = mapped_column(Float)
    consumption_kwh: Mapped[float | None] = mapped_column(Float)
    self_consumed_kwh: Mapped[float | None] = mapped_column(Float)
    exported_kwh: Mapped[float | None] = mapped_column(Float)
    imported_kwh: Mapped[float | None] = mapped_column(Float)
    reading_count: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="data_source"), nullable=False)
    derived_from: Mapped[str] = mapped_column(String(32), default="daily_value", nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quality_flags: Mapped[list[str] | None] = mapped_column(JSON)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id", ondelete="SET NULL"))
    sync_log_id: Mapped[int | None] = mapped_column(ForeignKey("sync_logs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- weather


class WeatherHourly(Base):
    __tablename__ = "weather_hourly"
    __table_args__ = (
        UniqueConstraint("plant_id", "ts", "kind", "issued_at", postgresql_nulls_not_distinct=True),
        Index("ix_weather_hourly_plant_kind_ts", "plant_id", "kind", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[WeatherKind] = mapped_column(Enum(WeatherKind, name="weather_kind"), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    shortwave_radiation: Mapped[float | None] = mapped_column(Float)  # W/m2 (GHI, preceding hour mean)
    direct_radiation: Mapped[float | None] = mapped_column(Float)
    diffuse_radiation: Mapped[float | None] = mapped_column(Float)
    direct_normal_irradiance: Mapped[float | None] = mapped_column(Float)
    global_tilted_irradiance: Mapped[float | None] = mapped_column(Float)
    cloud_cover: Mapped[float | None] = mapped_column(Float)  # %
    temperature_2m: Mapped[float | None] = mapped_column(Float)  # C
    relative_humidity_2m: Mapped[float | None] = mapped_column(Float)  # %
    wind_speed_10m: Mapped[float | None] = mapped_column(Float)  # km/h
    precipitation: Mapped[float | None] = mapped_column(Float)  # mm
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WeatherDaily(Base):
    __tablename__ = "weather_daily"
    __table_args__ = (
        UniqueConstraint("plant_id", "date", "kind", "issued_at", postgresql_nulls_not_distinct=True),
        Index("ix_weather_daily_plant_kind_date", "plant_id", "kind", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[WeatherKind] = mapped_column(Enum(WeatherKind, name="weather_kind"), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    shortwave_radiation_sum: Mapped[float | None] = mapped_column(Float)  # MJ/m2
    gti_sum_wh_m2: Mapped[float | None] = mapped_column(Float)  # Wh/m2 on the tilted plane (from hourly)
    sunshine_duration_s: Mapped[float | None] = mapped_column(Float)
    temperature_max: Mapped[float | None] = mapped_column(Float)
    temperature_min: Mapped[float | None] = mapped_column(Float)
    temperature_mean: Mapped[float | None] = mapped_column(Float)
    cloud_cover_mean: Mapped[float | None] = mapped_column(Float)
    humidity_mean: Mapped[float | None] = mapped_column(Float)
    wind_speed_max: Mapped[float | None] = mapped_column(Float)
    precipitation_sum: Mapped[float | None] = mapped_column(Float)
    weather_code: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --------------------------------------------------------------------------- operations


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int | None] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # sems | weather_observed | weather_forecast
    mode: Mapped[str] = mapped_column(String(32), default="incremental", nullable=False)
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, name="sync_status"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_format: Mapped[str] = mapped_column(String(16), nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus, name="import_status"), nullable=False)
    inspection: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    timezone: Mapped[str | None] = mapped_column(String(64))
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_valid: Mapped[int | None] = mapped_column(Integer)
    rows_rejected: Mapped[int | None] = mapped_column(Integer)
    rows_inserted: Mapped[int | None] = mapped_column(Integer)
    rows_updated: Mapped[int | None] = mapped_column(Integer)
    rows_duplicate: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- ML


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task: Mapped[str] = mapped_column(String(64), default="next_day_kwh", nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    version_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    training_start: Mapped[date | None] = mapped_column(Date)
    training_end: Mapped[date | None] = mapped_column(Date)
    validation_start: Mapped[date | None] = mapped_column(Date)
    test_start: Mapped[date | None] = mapped_column(Date)
    test_end: Mapped[date | None] = mapped_column(Date)
    feature_names: Mapped[list[str] | None] = mapped_column(JSON)
    hyperparameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    feature_importance: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(1024))
    dataset_fingerprint: Mapped[str | None] = mapped_column(String(64))
    row_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    data_sources: Mapped[list[str] | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    metric_rows: Mapped[list[ModelMetric]] = relationship(
        back_populates="model_version", cascade="all, delete-orphan"
    )


class ModelArtifact(Base):
    """Serialized model (joblib bytes). Durable copy for hosts with ephemeral disks."""

    __tablename__ = "model_artifacts"

    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ModelMetric(Base):
    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    split: Mapped[str] = mapped_column(String(16), nullable=False)  # train | validation | test
    metric_name: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)

    model_version: Mapped[ModelVersion] = relationship(back_populates="metric_rows")


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint(
            "plant_id", "target_date", "issued_at", "model_version_id", postgresql_nulls_not_distinct=True
        ),
        Index("ix_forecasts_plant_target", "plant_id", "target_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), nullable=False)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"))
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    predicted_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    lower_kwh: Mapped[float | None] = mapped_column(Float)
    upper_kwh: Mapped[float | None] = mapped_column(Float)
    weather_input_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # forecast | observed | none
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actual_kwh: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (
        UniqueConstraint("plant_id", "date", "category", "detector"),
        Index("ix_anomalies_plant_date", "plant_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), nullable=False)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[AnomalySeverity] = mapped_column(
        Enum(AnomalySeverity, name="anomaly_severity"), nullable=False
    )
    status: Mapped[AnomalyStatus] = mapped_column(
        Enum(AnomalyStatus, name="anomaly_status"), default=AnomalyStatus.OPEN, nullable=False
    )
    actual_kwh: Mapped[float | None] = mapped_column(Float)
    expected_kwh: Mapped[float | None] = mapped_column(Float)
    deviation_kwh: Mapped[float | None] = mapped_column(Float)
    deviation_pct: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recommended_checks: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- reports & AI


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[ReportKind] = mapped_column(Enum(ReportKind, name="report_kind"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# --------------------------------------------------------------------------- sharing


class PlantRole(str, enum.Enum):
    """Access granted to a non-owner. Owners and superusers implicitly have full access."""

    VIEWER = "viewer"  # read-only (may still chat with the AI analyst)
    EDITOR = "editor"  # everything except sharing and deleting the plant


class PlantMember(Base):
    __tablename__ = "plant_members"

    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=PlantRole.VIEWER.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship()


class Invitation(Base):
    """One-time invite link. Only the SHA-256 of the token is stored."""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=PlantRole.VIEWER.value)
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    plant: Mapped[Plant] = relationship()
    invited_by: Mapped[User | None] = relationship()
