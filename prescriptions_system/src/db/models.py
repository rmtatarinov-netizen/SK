"""ORM-модели (SQLAlchemy 2.x, typed ORM). Полная схема (Шаг 2).

Идемпотентность обеспечивают уникальные ключи:
- prescriptions: (doc_number, doc_date, object_id, prescription_type);
- processed_files: sha256;
- notifications: (prescription_id, event_type, recipient_type, recipient_id,
  event_version).

Каскадное удаление разрешено только для содержимого предписания (пункты,
нормативные ссылки). История и уведомления не удаляются каскадом.
Контакты получателей хранятся в recipient_channels, а не полями в users.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum as PyEnum

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base
from src.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)


def _enum_values(enum_cls: type[PyEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def enum_col(enum_cls: type[PyEnum], name: str) -> Enum:
    """SQLAlchemy Enum с хранением value Python-класса."""
    return Enum(enum_cls, name=name, values_callable=_enum_values)


class PrescriptionCategory(Base):
    __tablename__ = "prescription_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)

class PrescriptionKind(Base):
    __tablename__ = "prescription_kinds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)

class ConstructionManagement(Base):
    __tablename__ = "construction_management"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)

class PrescriptionInspector(Base):
    __tablename__ = "prescription_inspectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)


class WorkType(Base):
    """Справочник видов работ (гибридный, M3). risk_weight — 0–10 для индексов."""

    __tablename__ = "work_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    risk_weight: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Section(Base):
    """Участок / корпус / захватка (дерево, M1). parent_id — вышестоящий узел."""

    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("object_id", "name", "parent_id", name="uq_section_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    object_id: Mapped[int] = mapped_column(
        ForeignKey("objects.id", name="fk_sections_object"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("sections.id", name="fk_sections_parent"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    object: Mapped[Object] = relationship()


class OverdueReasonType(Base):
    """Справочник причин просрочки (M4)."""

    __tablename__ = "overdue_reason_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)

class PrescriptionType(str, PyEnum):
    CUSTOMER = "customer"
    INTERNAL = "internal"


class PrescriptionStatus(str, PyEnum):
    DRAFT_FOR_SIGNATURE = "draft_for_signature"
    NEW = "new"
    UNDER_REVIEW = "under_review"
    IN_WORK = "in_work"
    FIXED_PENDING_REVIEW = "fixed_pending_review"
    CLOSED = "closed"
    REJECTED = "rejected"


class UserRole(str, PyEnum):
    ADMIN = "admin"
    INSPECTOR = "inspector"
    CONTRACTOR_RESPONSIBLE = "contractor_responsible"
    MANAGER = "manager"


class NormRefSource(str, PyEnum):
    EXPLICIT = "explicit"
    LLM = "llm"
    RAG = "rag"
    MANUAL = "manual"


class NormRefStatus(str, PyEnum):
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class AttachmentKind(str, PyEnum):
    SCAN = "scan"
    PHOTO = "photo"
    ACT = "act"


class ProcessedFileStatus(str, PyEnum):
    PROCESSED = "processed"
    ERROR = "error"


class LlmRagCallStatus(str, PyEnum):
    OK = "ok"
    ERROR = "error"


class Enterprise(Base):
    __tablename__ = "enterprises"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Object(Base):
    __tablename__ = "objects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Contractor(Base):
    __tablename__ = "contractors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    inn: Mapped[str | None] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(enum_col(UserRole, "user_role"))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    preferred_notification_channel: Mapped[NotificationChannel | None] = (
        mapped_column(enum_col(NotificationChannel, "notification_channel"))
    )
    contractor_id: Mapped[int | None] = mapped_column(
        ForeignKey("contractors.id", name="fk_users_contractor"), index=True
    )
    object_id: Mapped[int | None] = mapped_column(
        ForeignKey("objects.id", name="fk_users_object"), index=True
    )
    password_hash: Mapped[str] = mapped_column(String(128))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    contractor: Mapped[Contractor | None] = relationship()
    object: Mapped[Object | None] = relationship()


class UserRoleChangeLog(Base):
    __tablename__ = "user_role_change_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_role_log_user"), index=True
    )
    old_role: Mapped[str] = mapped_column(String(32))
    new_role: Mapped[str] = mapped_column(String(32))
    changed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_role_log_changed_by"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RecipientChannel(Base):
    __tablename__ = "recipient_channels"
    __table_args__ = (
        UniqueConstraint(
            "recipient_type",
            "recipient_id",
            "channel",
            "address",
            name="uq_recipient_channel",
        ),
        Index("ix_recipient_channels_recipient", "recipient_type", "recipient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_type: Mapped[str] = mapped_column(String(16))
    recipient_id: Mapped[int] = mapped_column(Integer)
    channel: Mapped[NotificationChannel] = mapped_column(
        enum_col(NotificationChannel, "notification_channel")
    )
    address: Mapped[str] = mapped_column(String(128))
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Prescription(Base):
    __tablename__ = "prescriptions"
    __table_args__ = (
        UniqueConstraint(
            "doc_number",
            "doc_date",
            "object_id",
            "prescription_type",
            name="uq_prescription_doc",
        ),
        Index("ix_prescriptions_status_deadline", "status", "deadline"),
        Index("ix_prescriptions_object_deadline", "object_id", "deadline"),
        Index("ix_prescriptions_contractor_status", "contractor_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_type: Mapped[PrescriptionType] = mapped_column(
        enum_col(PrescriptionType, "prescription_type")
    )
    doc_number: Mapped[str] = mapped_column(String(64), index=True)
    doc_date: Mapped[date] = mapped_column(Date)
    issue_date: Mapped[date | None] = mapped_column(Date)
    actual_fix_date: Mapped[date | None] = mapped_column(Date)
    enterprise_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprises.id", name="fk_prescriptions_enterprise"), index=True
    )
    object_id: Mapped[int] = mapped_column(
        ForeignKey("objects.id", name="fk_prescriptions_object"), index=True
    )
    contractor_id: Mapped[int] = mapped_column(
        ForeignKey("contractors.id", name="fk_prescriptions_contractor"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    justification: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[date | None] = mapped_column(Date)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescription_categories.id", name="fk_prescriptions_category"), index=True
    )
    kind_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescription_kinds.id", name="fk_prescriptions_kind"), index=True
    )
    mgmt_id: Mapped[int | None] = mapped_column(
        ForeignKey("construction_management.id", name="fk_prescriptions_mgmt"), index=True
    )
    issuer_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescription_inspectors.id", name="fk_prescriptions_issuer"), index=True
    )
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("sections.id", name="fk_prescriptions_section"), index=True
    )
    work_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_types.id", name="fk_prescriptions_work_type"), index=True
    )
    recurrence_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_prescriptions_recurrence_of"), index=True
    )
    overdue_reason_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("overdue_reason_types.id", name="fk_prescriptions_overdue_reason_type"),
        index=True,
    )
    overdue_reason_text: Mapped[str | None] = mapped_column(Text)
    responsible_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_prescriptions_responsible_user"), index=True
    )
    inspector_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_prescriptions_inspector_user"), index=True
    )
    status: Mapped[PrescriptionStatus] = mapped_column(
        enum_col(PrescriptionStatus, "prescription_status"),
        default=PrescriptionStatus.NEW,
    )
    source_file_path: Mapped[str | None] = mapped_column(String(500))
    source_file_hash: Mapped[str | None] = mapped_column(String(64))
    signed_scan_attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "prescription_attachments.id",
            name="fk_prescriptions_signed_scan",
            use_alter=True,
        ),
        index=True,
    )
    signed_scan_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_prescriptions_confirmed_by"), index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_prescriptions_closed_by"), index=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_prescriptions_created_by"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    enterprise: Mapped[Enterprise | None] = relationship()
    object: Mapped[Object] = relationship()
    contractor: Mapped[Contractor] = relationship()
    section: Mapped[Section | None] = relationship()
    work_type: Mapped[WorkType | None] = relationship()
    recurrence_of: Mapped[Prescription | None] = relationship(
        remote_side="Prescription.id", foreign_keys="Prescription.recurrence_of_id"
    )
    overdue_reason_type: Mapped[OverdueReasonType | None] = relationship()
    responsible_user: Mapped[User | None] = relationship(
        foreign_keys="Prescription.responsible_user_id"
    )
    inspector_user: Mapped[User | None] = relationship(
        foreign_keys="Prescription.inspector_user_id"
    )
    confirmed_by: Mapped[User | None] = relationship(
        foreign_keys="Prescription.confirmed_by_user_id"
    )
    closed_by: Mapped[User | None] = relationship(
        foreign_keys="Prescription.closed_by_user_id"
    )
    created_by: Mapped[User | None] = relationship(
        foreign_keys="Prescription.created_by_user_id"
    )
    items: Mapped[list[PrescriptionItem]] = relationship(
        back_populates="prescription",
        cascade="all, delete-orphan",
        order_by="PrescriptionItem.item_number",
    )
    norm_refs: Mapped[list[PrescriptionNormRef]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan"
    )
    attachments: Mapped[list[PrescriptionAttachment]] = relationship(
        foreign_keys="PrescriptionAttachment.prescription_id"
    )
    history: Mapped[list[PrescriptionHistory]] = relationship()
    notifications: Mapped[list[Notification]] = relationship()
    print_versions: Mapped[list[PrescriptionPrintVersion]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan"
    )

    @property
    def suggested_norm_refs(self) -> list[PrescriptionNormRef]:
        return [
            ref for ref in self.norm_refs if ref.status == NormRefStatus.SUGGESTED
        ]

    @property
    def confirmed_norm_refs(self) -> list[PrescriptionNormRef]:
        return [
            ref for ref in self.norm_refs if ref.status == NormRefStatus.CONFIRMED
        ]


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_items_prescription"), index=True
    )
    item_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    deadline: Mapped[date | None] = mapped_column(Date)
    explicit_norm_refs: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    prescription: Mapped[Prescription] = relationship(back_populates="items")
    norm_refs: Mapped[list[PrescriptionNormRef]] = relationship(
        back_populates="item"
    )


class NormDoc(Base):
    __tablename__ = "norm_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    edition: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40))
    source_file: Mapped[str] = mapped_column(String(500))
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NormChunk(Base):
    __tablename__ = "norm_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_norm_chunk_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("norm_docs.id", name="fk_chunks_document"), index=True
    )
    section: Mapped[str | None] = mapped_column(String(255))
    item: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_char: Mapped[int | None] = mapped_column(Integer)
    end_char: Mapped[int | None] = mapped_column(Integer)

    document: Mapped[NormDoc] = relationship()


class NormChunkEmbedding(Base):
    """Метаданные эмбеддингов: векторы хранит ChromaDB, здесь — связь."""

    __tablename__ = "norm_chunk_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("norm_chunks.id", name="fk_embeddings_chunk"),
        unique=True,
        index=True,
    )
    chroma_id: Mapped[str] = mapped_column(String(128), unique=True)
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    chunk: Mapped[NormChunk] = relationship()


class PrescriptionNormRef(Base):
    __tablename__ = "prescription_norm_refs"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_normrefs_prescription"), index=True
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescription_items.id", name="fk_normrefs_item"), index=True
    )
    doc_code: Mapped[str] = mapped_column(String(120), index=True)
    doc_item: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(500))
    fragment: Mapped[str | None] = mapped_column(Text)
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("norm_chunks.id", name="fk_normrefs_chunk"), index=True
    )
    source: Mapped[NormRefSource] = mapped_column(
        enum_col(NormRefSource, "norm_ref_source")
    )
    status: Mapped[NormRefStatus] = mapped_column(
        enum_col(NormRefStatus, "norm_ref_status"),
        default=NormRefStatus.SUGGESTED,
    )
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_normrefs_confirmed_by"), index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    prescription: Mapped[Prescription] = relationship(back_populates="norm_refs")
    item: Mapped[PrescriptionItem | None] = relationship(back_populates="norm_refs")
    confirmed_by: Mapped[User | None] = relationship()


class PrescriptionAttachment(Base):
    __tablename__ = "prescription_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_attachments_prescription"),
        index=True,
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescription_items.id", name="fk_attachments_item"), index=True
    )
    kind: Mapped[AttachmentKind] = mapped_column(
        enum_col(AttachmentKind, "attachment_kind")
    )
    file_path: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    print_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "prescription_print_versions.id",
            name="fk_attachments_print_version",
        ),
        index=True,
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    caption: Mapped[str | None] = mapped_column(String(500))
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_attachments_uploaded_by"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PrescriptionPrintVersion(Base):
    """Неизменяемая печатная версия внутреннего предписания.

    Новая редакция создаёт новую версию; существующие файлы не перезаписываются.
    """

    __tablename__ = "prescription_print_versions"
    __table_args__ = (
        UniqueConstraint(
            "prescription_id", "version_number", name="uq_print_version_number"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_print_versions_prescription"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    file_path: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_print_versions_created_by"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    prescription: Mapped[Prescription] = relationship(
        back_populates="print_versions"
    )


class PrescriptionHistory(Base):
    __tablename__ = "prescription_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_history_prescription"), index=True
    )
    changed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_history_changed_by"), index=True
    )
    change_type: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "prescription_id",
            "event_type",
            "recipient_type",
            "recipient_id",
            "event_version",
            name="uq_notification_event_key",
        ),
        Index("ix_notifications_status_next_attempt", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_notifications_prescription"),
        index=True,
    )
    event_type: Mapped[NotificationEvent] = mapped_column(
        enum_col(NotificationEvent, "notification_event")
    )
    event_version: Mapped[int] = mapped_column(Integer, default=1)
    recipient_type: Mapped[str] = mapped_column(String(16))
    recipient_id: Mapped[int] = mapped_column(Integer)
    channel: Mapped[NotificationChannel | None] = mapped_column(
        enum_col(NotificationChannel, "notification_channel")
    )
    message_text: Mapped[str] = mapped_column(Text)
    status: Mapped[NotificationStatus] = mapped_column(
        enum_col(NotificationStatus, "notification_status"),
        default=NotificationStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    external_message_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)

    prescription: Mapped[Prescription] = relationship(back_populates="notifications")
    attempts: Mapped[list[NotificationAttempt]] = relationship(
        back_populates="notification", cascade="all, delete-orphan"
    )


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", name="fk_attempts_notification"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    channel: Mapped[NotificationChannel | None] = mapped_column(
        enum_col(NotificationChannel, "notification_channel")
    )
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    external_message_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    notification: Mapped[Notification] = relationship(back_populates="attempts")


class LlmRagLog(Base):
    __tablename__ = "llm_rag_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    prescription_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_llm_logs_prescription"), index=True
    )
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LlmRagCallStatus] = mapped_column(
        enum_col(LlmRagCallStatus, "llm_rag_call_status"),
        default=LlmRagCallStatus.OK,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProcessedFile(Base):
    __tablename__ = "processed_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[ProcessedFileStatus] = mapped_column(
        enum_col(ProcessedFileStatus, "processed_file_status"),
        default=ProcessedFileStatus.PROCESSED,
    )
    prescription_id: Mapped[int | None] = mapped_column(
        ForeignKey("prescriptions.id", name="fk_processed_files_prescription"),
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SystemConfig(Base):
    """Служебные переключатели (например, «отправлять OCR без проверки: off»)."""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[dict | str | int | bool | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
