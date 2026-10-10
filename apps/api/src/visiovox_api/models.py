"""SQLAlchemy 2.0 models (docs/10-data-model.md).

IDs are prefixed ULIDs stored as TEXT: sortable by creation time, safe to
expose, and free of the enumeration risk sequential integers carry. The prefix
makes a stray id in a log or a bug report self-describing.

Timestamps are TIMESTAMPTZ in UTC everywhere. Media timing is integer
milliseconds (invariant 2) and never appears as float seconds.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from ulid import ULID

PROJECT_STATUSES = (
    "pending",
    "validating",
    "queued",
    "processing",
    "ready",
    "failed",
    "cancelled",
    "expired",
)
JOB_STATUSES = ("queued", "running", "succeeded", "partial", "failed", "cancelled")
STAGE_STATUSES = ("pending", "running", "succeeded", "skipped", "degraded", "failed")


def new_id(prefix: str) -> str:
    """Prefixed ULID, e.g. prj_01HX8ZQ3M7N4P5R6S7T8V9W0XY."""
    return f"{prefix}_{ULID()}"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """Render a SQL IN check constraint from a Python tuple.

    Keeps the allowed values in one place so the CHECK constraint and the
    application cannot drift apart.
    """
    quote = chr(39)
    joined = ", ".join(f"{quote}{v}{quote}" for v in values)
    return f"{column} IN ({joined})"


class Base(DeclarativeBase):
    pass


def _pk(prefix: str) -> Mapped[str]:
    return mapped_column(Text, primary_key=True, default=lambda: new_id(prefix))


def _now_col() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = _pk("usr")
    # Firebase owns identity (ADR-0016); this row is the anchor eight other
    # tables hang their foreign keys off, and a mirror of the token's claims.
    # Nullable so rows created before the migration can be adopted on first
    # sign-in rather than orphaning their projects.
    firebase_uid: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # "password", "google.com", "github.com" - recorded so an operator can see
    # how someone signs in without that becoming an investigation.
    auth_provider: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(Text, nullable=False, server_default="free")
    data_region: Mapped[str] = mapped_column(Text, nullable=False, server_default="eu")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")

    # NFR-PRIV-02 / NFR-PRIV-05: the privacy-preserving setting is the default
    # and requires no action from the user (Charter principle 5).
    allow_training_use: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    persist_voiceprints: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[dt.datetime] = _now_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    projects: Mapped[list[Project]] = relationship(back_populates="user")

    __table_args__ = (
        CheckConstraint(_in_list("plan", ("free", "pro", "team")), name="users_plan_check"),
        CheckConstraint(_in_list("data_region", ("eu", "us", "in")), name="users_region_check"),
        CheckConstraint("retention_days BETWEEN 1 AND 365", name="users_retention_check"),
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = _pk("prj")
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")

    source_key: Mapped[str | None] = mapped_column(Text)
    source_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_sha256: Mapped[str | None] = mapped_column(String(64))

    duration_ms: Mapped[int | None] = mapped_column(Integer)  # invariant 2
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    has_video: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Spoken language for captions (visiovox_api.languages); None = detect.
    language: Mapped[str | None] = mapped_column(Text)
    # Names a person gave the speakers, keyed by ordinal ("1" -> "Priya").
    # Applied to the manifest as it is served (visiovox_api.speakers), so the
    # player, share page and transcript all read one source.
    speaker_names: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # The part of the source to process, when only part was chosen (imports).
    trim_start_ms: Mapped[int | None] = mapped_column(Integer)
    trim_end_ms: Mapped[int | None] = mapped_column(Integer)

    speaker_count: Mapped[int | None] = mapped_column(Integer)
    overlap_ratio: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="projects")
    job: Mapped[Job | None] = relationship(back_populates="project", uselist=False)

    __table_args__ = (
        CheckConstraint(_in_list("status", PROJECT_STATUSES), name="projects_status_check"),
        CheckConstraint(
            _in_list("difficulty", ("easy", "moderate", "hard")),
            name="projects_difficulty_check",
        ),
        CheckConstraint("speaker_count BETWEEN 0 AND 8", name="projects_speaker_count_check"),
        Index("ix_projects_user_created", "user_id", "created_at"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = _pk("job")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    pipeline_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="mock")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()

    project: Mapped[Project] = relationship(back_populates="job")
    stages: Mapped[list[JobStage]] = relationship(back_populates="job", order_by="JobStage.ordinal")

    __table_args__ = (
        CheckConstraint(_in_list("status", JOB_STATUSES), name="jobs_status_check"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="jobs_progress_check"),
    )


class JobStage(Base):
    """One pipeline stage attempt.

    Unique on (job_id, stage, version) so retries are idempotent (invariant 7):
    a resumed job skips stages already recorded as succeeded at that version.
    """

    __tablename__ = "job_stages"

    id: Mapped[str] = _pk("jst")
    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)  # S0_ingest ... S9_package
    version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0.0")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="stages")

    __table_args__ = (
        Index("uq_job_stage_version", "job_id", "stage", "version", unique=True),
        CheckConstraint(_in_list("status", STAGE_STATUSES), name="job_stages_status_check"),
    )


class UsageCounter(Base):
    """Metered consumption, per user, per metric, per period (docs/15 §9).

    In Postgres rather than Redis because quota enforcement must be atomic
    across API replicas and must survive eviction: a counter that resets when
    the cache is under pressure is not a quota. Redis holds rate-limit windows,
    which are allowed to be approximate; this is not.

    `period_key` is the calendar bucket the metric is counted in - "2026-09-16"
    for daily, "2026-09" for monthly. Storing the bucket rather than a rolling
    window keeps increments to a single upsert and makes the row directly
    readable when a user asks why they were refused.
    """

    __tablename__ = "usage_counters"

    id: Mapped[str] = _pk("usc")
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    period_key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # The upsert target. Without this, concurrent increments create
        # duplicate rows and the quota silently doubles.
        Index("uq_usage_user_metric_period", "user_id", "metric", "period_key", unique=True),
        CheckConstraint("value >= 0", name="usage_counters_value_check"),
    )


class AuditEvent(Base):
    """Append-only security record (docs/15 §10).

    Retained a year. Account deletion nulls `user_id` but preserves the row -
    erasure of identity, retention of the security record.

    IPs are stored hashed, never raw, and the table deliberately has no column
    that could hold media, transcript text or an embedding.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = _pk("aud")
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False, server_default="success")
    ip_hash: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (
        Index("ix_audit_user_created", "user_id", "created_at"),
        Index("ix_audit_action_created", "action", "created_at"),
        CheckConstraint(
            _in_list("outcome", ("success", "failure", "denied")),
            name="audit_events_outcome_check",
        ),
    )


class UploadSession(Base):
    """A multipart upload in flight (docs/28 §W2).

    Server-side part state is what makes a large upload resumable across a
    browser refresh: without it the part list lives only in the tab that started
    the upload, and a reload means starting a multi-GB transfer again.

    `reserved_bytes` is the disk headroom claimed at init and released on
    complete, abort or expiry. Rate-limiting /upload/init is not enough on its
    own - each init creates a real multipart upload that occupies storage even
    if never completed, so the space has to be booked, not merely counted.
    """

    __tablename__ = "upload_sessions"

    id: Mapped[str] = _pk("upl")
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    upload_id: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    part_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # [{"part_number": 1, "etag": "..."}] - completed parts, so a resumed upload
    # knows what to skip.
    completed_parts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_upload_sessions_user_status", "user_id", "status"),
        Index("uq_upload_sessions_upload_id", "upload_id", unique=True),
        CheckConstraint(
            _in_list("status", ("active", "completed", "aborted", "expired")),
            name="upload_sessions_status_check",
        ),
    )


class Export(Base):
    """A rendered file a user asked for (docs/28 §W6).

    A row rather than a file on disk because an export has a lifecycle that
    outlives the request that made it: it is produced asynchronously, it can
    fail, it is downloaded later, and it expires. Storage grows without bound
    otherwise — an export is typically larger than the source it came from.

    Unique on (project, speaker, format, quality) so asking twice returns the
    same artifact instead of re-encoding it. Re-encoding the same video to the
    same rung twice is pure waste on a single-GPU machine.
    """

    __tablename__ = "exports"

    id: Mapped[str] = _pk("exp")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # None means "all speakers", for the audio-stems variant.
    speaker_ordinal: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="video")
    quality: Mapped[str | None] = mapped_column(Text)
    # A clip of the recording (ADR-0022); both None for the whole of it.
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    # Captions burned into the picture: None, "original", or a language code
    # of a ready translation.
    captions: Mapped[str | None] = mapped_column(Text)
    # A listener's own mix (ADR-0023): "ordinal:percent" pairs, sorted, e.g.
    # "0:0,1:100,2:30" (0 = the original mix). None for a single speaker.
    mix: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    storage_key: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_detail: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_export_project_variant",
            "project_id",
            "speaker_ordinal",
            "kind",
            "quality",
            "start_ms",
            "end_ms",
            "captions",
            "mix",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_exports_user_created", "user_id", "created_at"),
        CheckConstraint(
            _in_list("kind", ("video", "audio", "captions")), name="exports_kind_check"
        ),
        CheckConstraint(
            _in_list("status", ("queued", "running", "ready", "failed", "expired")),
            name="exports_status_check",
        ),
    )


class ShareLink(Base):
    """A revocable public link to one project (docs/28 §W7).

    Durable rather than cached because revocation has to be: a "revoked" share
    that comes back after a restart is a privacy incident, not a cache miss.

    Only the SHA-256 of the token is stored, for the same reason password hashes
    are. Anyone who can read this table - an operator, a backup, a leaked dump -
    would otherwise hold working links to every shared recording.
    """

    __tablename__ = "share_links"

    id: Mapped[str] = _pk("shr")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # None shares every speaker; an ordinal shares just that one, which is what
    # someone usually wants when sending a clip of one person talking.
    speaker_ordinal: Mapped[int | None] = mapped_column(Integer)
    # Optional second factor for a link that will travel further than intended.
    password_hash: Mapped[str | None] = mapped_column(Text)

    access_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_accessed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (
        Index("ix_share_links_project", "project_id"),
        Index("ix_share_links_user", "user_id"),
    )

    @property
    def is_live(self) -> bool:
        """Whether this link should still open anything.

        Checked in one place so revocation, expiry and deletion cannot disagree
        about what "live" means.
        """
        if self.revoked_at is not None:
            return False
        return not (self.expires_at is not None and self.expires_at < dt.datetime.now(dt.UTC))


class Translation(Base):
    """A project's captions translated into one language (ADR-0020).

    Unique on (project, language): asking again returns the same translation,
    and a failed one is retried in place. The files live at
    `projects/<id>/translations/<language>/spk_<ordinal>.json`.
    """

    __tablename__ = "translations"

    id: Mapped[str] = _pk("trl")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_translation_project_language", "project_id", "language", unique=True),
        CheckConstraint(
            _in_list("status", ("queued", "running", "ready", "failed")),
            name="translations_status_check",
        ),
    )


class Summary(Base):
    """A project's summary and action items, written by a local model (ADR-0021).

    One per project; asking again rewrites it (names may have changed since).
    `content` is what ml/pipeline/s11_summarize.py returns: every item cites
    transcript line ids, and only cited items are kept.
    """

    __tablename__ = "summaries"

    id: Mapped[str] = _pk("sum")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            _in_list("status", ("queued", "running", "ready", "failed")),
            name="summaries_status_check",
        ),
    )


class Dub(Base):
    """A project dubbed into one language, every speaker in a voice of their
    own, the room kept (ADR-0024). One per (project, language)."""

    __tablename__ = "dubs"

    id: Mapped[str] = _pk("dub")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    # How the dub fits the original timing, and which voice each speaker got.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_dub_project_language", "project_id", "language", unique=True),
        CheckConstraint(
            _in_list("status", ("queued", "running", "ready", "failed")),
            name="dubs_status_check",
        ),
    )


class StudySession(Base):
    """One participant's session in the user study (docs/user-study).

    Recorded by the facilitator's account. No name, email or recording of the
    participant: an anonymous code, per-task timings and answers, the
    usability questionnaire, and a preference.
    """

    __tablename__ = "study_sessions"

    id: Mapped[str] = _pk("sty")
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    participant: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (Index("uq_study_participant", "user_id", "participant", unique=True),)


class MediaImport(Base):
    """A recording fetched from a link (YouTube, Vimeo) instead of uploaded."""

    __tablename__ = "media_imports"

    id: Mapped[str] = _pk("imp")
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Measured while downloading, for the time-left figure.
    downloaded_bytes: Mapped[int | None] = mapped_column(BigInteger)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    speed_bps: Mapped[int | None] = mapped_column(BigInteger)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    source_title: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            _in_list("status", ("queued", "downloading", "ready", "failed")),
            name="media_imports_status_check",
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="media_imports_progress_check"),
    )
