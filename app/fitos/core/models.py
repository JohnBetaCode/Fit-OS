"""Esquema completo (Fases 0–1). Ids enteros salvo `exercises.id` (id del dataset) y tablas de una fila.
Fechas-hora en UTC (`UTCDateTime`); fechas de calendario (`Date`) en la zona `TZ` del usuario."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as DateT
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .timeutil import utcnow

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator):
    """Guarda UTC sin zona (SQLite no tiene zonas) y devuelve datetimes con tzinfo=UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ── Núcleo ──────────────────────────────────────────────────────────────────


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(16))  # user | llm | system
    tool: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[Any | None] = mapped_column(JSON)
    after: Mapped[Any | None] = mapped_column(JSON)


class Timer(Base):
    __tablename__ = "timers"
    id: Mapped[int] = mapped_column(primary_key=True)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    kind: Mapped[str] = mapped_column(String(48))
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | done | cancelled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    job: Mapped[str] = mapped_column(String(48), primary_key=True)
    period_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str | None] = mapped_column(Text)


# ── WhatsApp ────────────────────────────────────────────────────────────────


class WaMessage(Base):
    """Solo mensajes del grupo de Fit-OS: entrada (webhook) y salida (outbox)."""

    __tablename__ = "wa_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    wa_id: Mapped[str | None] = mapped_column(String(128), unique=True)  # bare_id
    direction: Mapped[str] = mapped_column(String(4))  # in | out
    chat_id: Mapped[str] = mapped_column(String(64))
    from_me: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | video | image
    body: Mapped[str | None] = mapped_column(Text)
    media: Mapped[Any | None] = mapped_column(JSON)  # {path, mimetype, caption}
    quoted_wa_id: Mapped[str | None] = mapped_column(String(128))
    # out: the plan item this message is about (exercise card, set confirmation), so a WhatsApp reply to it
    # (swipe → reply) logs to that exercise
    planned_item_id: Mapped[int | None] = mapped_column(ForeignKey("planned_items.id", ondelete="SET NULL"))
    # in: received | processed | stale | error · out: queued | sent | failed
    status: Mapped[str] = mapped_column(String(16), index=True)
    handler: Mapped[str | None] = mapped_column(String(48))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ── Perfil y equipo ─────────────────────────────────────────────────────────


class Profile(Timestamped, Base):
    __tablename__ = "profile"
    id: Mapped[int] = mapped_column(primary_key=True)  # siempre 1
    display_name: Mapped[str | None] = mapped_column(String(64))
    sex: Mapped[str] = mapped_column(String(8), default="male")
    birth_date: Mapped[DateT | None] = mapped_column(Date)
    height_cm: Mapped[float | None] = mapped_column(Float)
    experience: Mapped[str] = mapped_column(String(16), default="intermediate")
    primary_goal: Mapped[str] = mapped_column(String(16), default="hypertrophy")
    goal_notes: Mapped[str | None] = mapped_column(Text)
    days_per_week: Mapped[int] = mapped_column(Integer, default=4)
    session_minutes: Mapped[int] = mapped_column(Integer, default=60)
    # {"mon": {"equipment_profile_id": 2, "time": "06:30"}, ...}; días ausentes = descanso
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    injuries: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{area, severity, since, notes, active}]
    disliked_exercise_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    coach_notes: Mapped[str | None] = mapped_column(Text)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)


class EquipmentProfile(Timestamped, Base):
    __tablename__ = "equipment_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(48), unique=True)
    kind: Mapped[str] = mapped_column(String(8), default="gym")  # home | gym
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # {"dumbbell_kg": [...], "barbell_step_kg": 2.5, "machine_step_kg": 5, "cable_step_kg": 2.5}
    loads: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ── Catálogo ────────────────────────────────────────────────────────────────


class Exercise(Base):
    __tablename__ = "exercises"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(160))
    name_es: Mapped[str | None] = mapped_column(String(160))
    primary_muscle: Mapped[str | None] = mapped_column(String(24), index=True)
    secondary_muscles: Mapped[list[Any]] = mapped_column(JSON, default=list)
    target_raw: Mapped[str | None] = mapped_column(String(48))
    body_part: Mapped[str | None] = mapped_column(String(32))
    equipment_raw: Mapped[str | None] = mapped_column(String(48))
    equipment_tag: Mapped[str | None] = mapped_column(String(24), index=True)
    requires: Mapped[list[Any]] = mapped_column(JSON, default=list)
    station: Mapped[str | None] = mapped_column(String(32))
    movement_pattern: Mapped[str | None] = mapped_column(String(24), index=True)
    mechanic: Mapped[str | None] = mapped_column(String(12))
    laterality: Mapped[str | None] = mapped_column(String(12))
    load_kind: Mapped[str | None] = mapped_column(String(16))
    stress_joints: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False)
    difficulty: Mapped[int | None] = mapped_column(Integer)
    instructions_es: Mapped[list[Any]] = mapped_column(JSON, default=list)
    instructions_en: Mapped[list[Any] | None] = mapped_column(JSON)
    media_mp4: Mapped[str | None] = mapped_column(String(128))
    media_thumb: Mapped[str | None] = mapped_column(String(128))
    media_gif: Mapped[str | None] = mapped_column(String(160))
    attribution: Mapped[str | None] = mapped_column(String(128))
    youtube_url: Mapped[str | None] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    custom: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def display_name(self) -> str:
        """Spanish name, or the dataset's English one title-cased ("Dumbbell Bench Press") in English."""
        from ..catalog.names import title_en  # noqa: PLC0415
        from ..i18n import lang  # noqa: PLC0415

        if lang() == "en":
            return title_en(self.name_en) if self.name_en else (self.name_es or "")
        return self.name_es or self.name_en

    @property
    def instructions(self) -> list[str]:
        """Steps in the current language, falling back to the other one."""
        from ..i18n import lang  # noqa: PLC0415

        es, en = list(self.instructions_es or []), list(self.instructions_en or [])
        return (en or es) if lang() == "en" else (es or en)


# ── Programa y plan ─────────────────────────────────────────────────────────


class Program(Timestamped, Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    template_key: Mapped[str | None] = mapped_column(String(48))
    split_kind: Mapped[str] = mapped_column(String(24))
    days_per_week: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | archived | proposed
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(12), default="template")
    started_on: Mapped[DateT | None] = mapped_column(Date)
    rotation_idx: Mapped[int] = mapped_column(Integer, default=0)  # próximo día de la rotación
    days: Mapped[list[ProgramDay]] = relationship(
        back_populates="program", order_by="ProgramDay.order_idx", cascade="all, delete-orphan"
    )


class ProgramDay(Base):
    __tablename__ = "program_days"
    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    order_idx: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(48))
    focus: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # [{slot, pattern, muscle, sets, rep_min, rep_max, target_rir, preferred_exercise_ids}]
    slots: Mapped[list[Any]] = mapped_column(JSON, default=list)
    program: Mapped[Program] = relationship(back_populates="days")


class PlannedSession(Timestamped, Base):
    __tablename__ = "planned_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[DateT] = mapped_column(Date, index=True)
    program_day_id: Mapped[int | None] = mapped_column(ForeignKey("program_days.id", ondelete="SET NULL"))
    focus_label: Mapped[str] = mapped_column(String(80))
    equipment_profile_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_profiles.id"))
    # proposed | accepted | active | completed | skipped | replaced
    status: Mapped[str] = mapped_column(String(12), default="proposed")
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(12), default="planner")
    items: Mapped[list[PlannedItem]] = relationship(
        back_populates="session", order_by="PlannedItem.order_idx", cascade="all, delete-orphan"
    )


class PlannedItem(Base):
    __tablename__ = "planned_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    planned_session_id: Mapped[int] = mapped_column(ForeignKey("planned_sessions.id", ondelete="CASCADE"))
    order_idx: Mapped[int] = mapped_column(Integer)
    slot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # copia del slot que lo originó
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercises.id"))
    sets: Mapped[int] = mapped_column(Integer)
    rep_min: Mapped[int] = mapped_column(Integer)
    rep_max: Mapped[int] = mapped_column(Integer)
    target_rir: Mapped[int] = mapped_column(Integer, default=2)
    target_weight_kg: Mapped[float | None] = mapped_column(Float)
    target_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|active|done|skipped|swapped
    swapped_from_id: Mapped[str | None] = mapped_column(String(32))
    swap_reason: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    session: Mapped[PlannedSession] = relationship(back_populates="items")
    exercise: Mapped[Exercise] = relationship()


# ── Ejecución ───────────────────────────────────────────────────────────────


class WorkoutSession(Timestamped, Base):
    __tablename__ = "workout_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    planned_session_id: Mapped[int | None] = mapped_column(ForeignKey("planned_sessions.id"))
    date: Mapped[DateT] = mapped_column(Date, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    equipment_profile_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_profiles.id"))
    state: Mapped[str] = mapped_column(String(12), default="active")  # active | finishing | completed
    session_rpe: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="wa")
    # {current_item_id, busy_stations[], excluded_tags[], alt_cursor{item_id: n}, nudged}
    wa_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sets: Mapped[list[WorkoutSet]] = relationship(
        back_populates="session", order_by="WorkoutSet.id", cascade="all, delete-orphan"
    )
    planned: Mapped[PlannedSession | None] = relationship()


class WorkoutSet(Base):
    __tablename__ = "workout_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True)
    planned_item_id: Mapped[int | None] = mapped_column(ForeignKey("planned_items.id", ondelete="SET NULL"))
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercises.id"), index=True)
    set_idx: Mapped[int] = mapped_column(Integer)
    target_rep_min: Mapped[int | None] = mapped_column(Integer)
    target_rep_max: Mapped[int | None] = mapped_column(Integer)
    target_weight_kg: Mapped[float | None] = mapped_column(Float)
    target_rir: Mapped[int | None] = mapped_column(Integer)
    reps: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    rir: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    is_warmup: Mapped[bool] = mapped_column(Boolean, default=False)
    e1rm_kg: Mapped[float | None] = mapped_column(Float)
    pr_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    raw_text: Mapped[str | None] = mapped_column(Text)
    # cardio details: {"level": 8, "speed_kmh": 4.5, "incline_pct": 15, "steps": 1000, "distance_km": 1.2}
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    logged_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    session: Mapped[WorkoutSession] = relationship(back_populates="sets")
    exercise: Mapped[Exercise] = relationship()


class BodyMetric(Base):
    __tablename__ = "body_metrics"
    __table_args__ = (UniqueConstraint("kind", "measured_on", "source", name="uq_body_metric"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    measured_on: Mapped[DateT] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # weight_kg | body_fat_pct | waist_cm ...
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), default="manual")


class Activity(Base):
    """One activity recorded by a wearable (a WHOOP workout): when, what sport, how hard, heart-rate zones.

    Upserted by (source, ext_id) on every sync. `zones_min` is minutes per zone of max heart rate:
    [<50 %, 50–60, 60–70, 70–80, 80–90, 90–100]. `local_date` is the local day it started."""

    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("source", "ext_id", name="uq_activity_ext"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(24))
    ext_id: Mapped[str] = mapped_column(String(64))
    sport: Mapped[str] = mapped_column(String(48), default="activity")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime)
    local_date: Mapped[DateT] = mapped_column(Date, index=True)
    strain: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    kcal: Mapped[float | None] = mapped_column(Float)
    distance_m: Mapped[float | None] = mapped_column(Float)
    zones_min: Mapped[list[Any] | None] = mapped_column(JSON)


# ── Exámenes ────────────────────────────────────────────────────────────────


class DayLog(Base):
    """What a day was when it wasn't a normal training or rest day: sick, injured, travelling."""

    __tablename__ = "day_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[DateT] = mapped_column(Date, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(12))  # sick | injured | travel
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="wa")  # wa | web
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProgressPhoto(Base):
    """A progress photo: the file lives in `$DATA_DIR/users/<name>/photos/`, never in the database or in git."""

    __tablename__ = "progress_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    taken_on: Mapped[DateT] = mapped_column(Date, index=True)
    path: Mapped[str] = mapped_column(String(256))  # relative to $DATA_DIR/users/<name>/photos
    mimetype: Mapped[str] = mapped_column(String(32), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="web")  # web | wa
    wa_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Biomarker(Base):
    __tablename__ = "biomarkers"
    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    name_es: Mapped[str] = mapped_column(String(80))
    name_en: Mapped[str] = mapped_column(String(80))
    loinc: Mapped[str | None] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    canonical_unit: Mapped[str] = mapped_column(String(16))
    unit_conversions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    direction: Mapped[str] = mapped_column(String(8), default="range")  # lower | higher | range
    derived_from: Mapped[list[Any] | None] = mapped_column(JSON)
    decimals: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    description_es: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)

    @property
    def display_name(self) -> str:
        from ..i18n import pick  # noqa: PLC0415

        return pick(self.name_es, self.name_en)

    @property
    def description(self) -> str:
        from ..i18n import pick  # noqa: PLC0415

        return pick(self.description_es, self.description_en)


class BiomarkerRange(Base):
    __tablename__ = "biomarker_ranges"
    id: Mapped[int] = mapped_column(primary_key=True)
    biomarker_key: Mapped[str] = mapped_column(ForeignKey("biomarkers.key", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # lab_standard | borderline | user_target
    sex: Mapped[str] = mapped_column(String(8), default="any")
    age_min: Mapped[int | None] = mapped_column(Integer)
    age_max: Mapped[int | None] = mapped_column(Integer)
    low: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(128))


class BiomarkerNote(Base):
    __tablename__ = "biomarker_notes"
    biomarker_key: Mapped[str] = mapped_column(ForeignKey("biomarkers.key", ondelete="CASCADE"), primary_key=True)
    food_positive: Mapped[list[Any]] = mapped_column(JSON, default=list)
    food_negative: Mapped[list[Any]] = mapped_column(JSON, default=list)
    raw: Mapped[str | None] = mapped_column(Text)


class LabReport(Timestamped, Base):
    __tablename__ = "lab_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    collected_on: Mapped[DateT] = mapped_column(Date, index=True)
    date_precision: Mapped[str] = mapped_column(String(8), default="day")
    lab_name: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(12))  # seed_csv | pdf | manual
    file_path: Mapped[str | None] = mapped_column(String(256))
    file_sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    extraction: Mapped[Any | None] = mapped_column(JSON)
    results: Mapped[list[LabResult]] = relationship(back_populates="report", cascade="all, delete-orphan")


class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = (UniqueConstraint("biomarker_key", "report_id", name="uq_lab_result"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("lab_reports.id", ondelete="CASCADE"), index=True)
    biomarker_key: Mapped[str] = mapped_column(ForeignKey("biomarkers.key"), index=True)
    collected_on: Mapped[DateT] = mapped_column(Date)
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(64))
    comparator: Mapped[str | None] = mapped_column(String(4))
    unit: Mapped[str | None] = mapped_column(String(16))
    value_canonical: Mapped[float | None] = mapped_column(Float)
    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    ref_text: Mapped[str | None] = mapped_column(String(64))
    flag: Mapped[str | None] = mapped_column(String(8))
    page: Mapped[int | None] = mapped_column(Integer)
    raw_name: Mapped[str | None] = mapped_column(String(80))
    derived: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    report: Mapped[LabReport] = relationship(back_populates="results")


# ── Coach ───────────────────────────────────────────────────────────────────


class ChatThread(Base):
    __tablename__ = "chat_threads"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(4))  # wa | web
    title: Mapped[str | None] = mapped_column(String(120))
    day: Mapped[DateT | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="thread", order_by="ChatMessage.seq", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant | system
    content: Mapped[Any] = mapped_column(JSON)  # bloques exactos, se reenvían sin editar
    visible_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    thread: Mapped[ChatThread] = relationship(back_populates="messages")


class LlmCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read: Mapped[int] = mapped_column(Integer, default=0)
    cache_write: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(24))
