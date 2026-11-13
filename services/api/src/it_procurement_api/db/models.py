"""First migration batch (P04): identity/scope, master data, quotes, requests, plans, events.

Follows docs/领域模型与数据字典.md. Reservation, review, order, receipt, evidence,
solver, task_run and memory tables belong to later batches (P07, P09-P13).

Conventions:
- every table has tenant_id and UNIQUE (tenant_id, id); foreign keys are composite
  (tenant_id, x_id) so a row cannot reference another tenant's row;
- money is integer minor units (分) in BIGINT, never float; unknown is NULL, not 0;
- timestamps are timestamptz;
- enumerations are TEXT + CHECK so new values arrive through a migration.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _uuid(nullable: bool = False) -> Mapped:
    return mapped_column(UUID(as_uuid=True), nullable=nullable)


def _ts(nullable: bool = False) -> Mapped:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def _money(nullable: bool = False) -> Mapped:
    return mapped_column(BigInteger, nullable=nullable)


def _json() -> Mapped:
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


def fk(column: str, target: str, **kw) -> ForeignKeyConstraint:
    """Tenant-scoped foreign key: (tenant_id, column) -> target(tenant_id, id)."""
    return ForeignKeyConstraint(["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], **kw)


def tenant_key() -> UniqueConstraint:
    return UniqueConstraint("tenant_id", "id")


def one_of(column: str, *values: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"{column}_valid")


def non_negative(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 0", name=f"{column}_non_negative")


class Row:
    """Common columns. created_by is a principal id; it is not a foreign key so the
    first principal of a tenant can be created by the bootstrap service identity."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class Mutable:
    """Optimistic concurrency for objects that change after creation."""

    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- identity and scope -------------------------------------------------------


class Principal(Row, Mutable, Base):
    __tablename__ = "principal"
    __table_args__ = (tenant_key(), one_of("kind", "USER", "SERVICE"))

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    department_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class AccessScope(Row, Mutable, Base):
    __tablename__ = "access_scope"
    __table_args__ = (
        tenant_key(),
        fk("owner_id", "principal"),
        one_of("kind", "PERSONAL", "TASK", "DEPARTMENT", "ORG"),
        CheckConstraint("kind <> 'PERSONAL' OR owner_id IS NOT NULL", name="personal_has_owner"),
        CheckConstraint(
            "kind <> 'DEPARTMENT' OR department_id IS NOT NULL", name="department_has_department"
        ),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    department_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class RoleGrant(Row, Mutable, Base):
    __tablename__ = "role_grant"
    __table_args__ = (
        tenant_key(),
        fk("principal_id", "principal"),
        fk("scope_id", "access_scope"),
        one_of(
            "role",
            "REQUESTER",
            "IT_ASSET_ADMIN",
            "BUYER",
            "TECH_REVIEWER",
            "PROCUREMENT_APPROVER",
            "AUDITOR",
            "SYS_ADMIN",
        ),
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="validity_ordered"),
        # the same grant cannot be stacked while one is still unrevoked
        Index(
            "uq_role_grant_unrevoked",
            "tenant_id",
            "principal_id",
            "scope_id",
            "role",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    principal_id: Mapped[uuid.UUID] = _uuid()
    scope_id: Mapped[uuid.UUID] = _uuid()
    role: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = _ts()
    valid_until: Mapped[datetime | None] = _ts(nullable=True)
    revoked_at: Mapped[datetime | None] = _ts(nullable=True)


# --- sources and master data --------------------------------------------------


class SourceBatch(Row, Mutable, Base):
    __tablename__ = "source_batch"
    __table_args__ = (
        tenant_key(),
        one_of("source_type", "SYNTHETIC", "IMPORTED_REAL"),
        one_of("quality_status", "PENDING", "PASSED", "QUARANTINED"),
    )

    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = _ts()
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    quality_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProductModel(Row, Base):
    __tablename__ = "product_model"
    __table_args__ = (
        tenant_key(),
        fk("source_batch_id", "source_batch"),
        UniqueConstraint("tenant_id", "manufacturer", "model_code"),
    )

    manufacturer: Mapped[str] = mapped_column(Text, nullable=False)
    model_code: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    source_batch_id: Mapped[uuid.UUID] = _uuid()


class Sku(Row, Mutable, Base):
    __tablename__ = "sku"
    __table_args__ = (
        tenant_key(),
        fk("model_id", "product_model"),
        fk("source_batch_id", "source_batch"),
        UniqueConstraint("tenant_id", "sku_code"),
        one_of("catalog_kind", "SYNTHETIC", "REAL_DOCUMENTED"),
        one_of("review_status", "PENDING", "VERIFIED", "EXCLUDED"),
    )

    model_id: Mapped[uuid.UUID] = _uuid()
    sku_code: Mapped[str] = mapped_column(Text, nullable=False)
    hardware_revision: Mapped[str] = mapped_column(Text, nullable=False)
    specs: Mapped[dict] = _json()
    source_batch_id: Mapped[uuid.UUID] = _uuid()
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    catalog_kind: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    restriction_notes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class Asset(Row, Mutable, Base):
    __tablename__ = "asset"
    __table_args__ = (
        tenant_key(),
        fk("sku_id", "sku"),
        fk("scope_id", "access_scope"),
        fk("assigned_to", "principal"),
        fk("installed_in_asset_id", "asset"),
        UniqueConstraint("tenant_id", "asset_tag"),
        Index(
            "uq_asset_serial_no",
            "tenant_id",
            "serial_no",
            unique=True,
            postgresql_where=text("serial_no IS NOT NULL"),
        ),
        one_of("kind", "DEVICE", "COMPONENT"),
        one_of("lifecycle_status", "AVAILABLE", "IN_USE", "MAINTENANCE", "RETIRED", "INSTALLED"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint(
            "lifecycle_status <> 'INSTALLED' OR (kind = 'COMPONENT' AND installed_in_asset_id IS NOT NULL)",
            name="installed_is_component_in_host",
        ),
        CheckConstraint("installed_in_asset_id IS NULL OR installed_in_asset_id <> id", name="not_installed_in_self"),
    )

    sku_id: Mapped[uuid.UUID] = _uuid()
    scope_id: Mapped[uuid.UUID] = _uuid()
    asset_tag: Mapped[str] = mapped_column(Text, nullable=False)
    serial_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot: Mapped[dict] = _json()
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    location: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    installed_in_asset_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    # receipt_item arrives with the P07 batch; the foreign key is added there
    source_receipt_item_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)


class Supplier(Row, Mutable, Base):
    __tablename__ = "supplier"
    __table_args__ = (tenant_key(), UniqueConstraint("tenant_id", "supplier_code"))

    supplier_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


# --- quotes -------------------------------------------------------------------


class QuoteVersion(Row, Mutable, Base):
    __tablename__ = "quote_version"
    __table_args__ = (
        tenant_key(),
        fk("supplier_id", "supplier"),
        fk("source_batch_id", "source_batch"),
        fk("scope_id", "access_scope"),
        UniqueConstraint("tenant_id", "supplier_id", "quote_key", "revision"),
        one_of("status", "ACTIVE", "WITHDRAWN"),
        one_of("channel_kind", "SIMULATED_STORE"),
        one_of("market_data_kind", "SYNTHETIC"),
        non_negative("shipping_minor"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        # validity is [valid_from, valid_until)
        CheckConstraint("valid_from < valid_until", name="validity_ordered"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_code"),
    )

    supplier_id: Mapped[uuid.UUID] = _uuid()
    scope_id: Mapped[uuid.UUID] = _uuid()
    quote_key: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    price_basis: Mapped[str] = mapped_column(Text, nullable=False)
    shipping_minor: Mapped[int | None] = _money(nullable=True)
    valid_from: Mapped[datetime] = _ts()
    valid_until: Mapped[datetime] = _ts()
    delivery_at: Mapped[datetime | None] = _ts(nullable=True)
    warranty_terms: Mapped[dict] = _json()
    source_batch_id: Mapped[uuid.UUID] = _uuid()
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    channel_kind: Mapped[str] = mapped_column(Text, nullable=False)
    channel_name: Mapped[str] = mapped_column(Text, nullable=False)
    market_data_kind: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = _ts()


class QuoteLine(Row, Base):
    __tablename__ = "quote_line"
    __table_args__ = (
        tenant_key(),
        fk("quote_version_id", "quote_version"),
        fk("sku_id", "sku"),
        non_negative("unit_price_minor"),
        non_negative("available_quantity"),
        CheckConstraint("quantity_min >= 1", name="quantity_min_positive"),
        CheckConstraint("quantity_max IS NULL OR quantity_max >= quantity_min", name="quantity_bounds_ordered"),
    )

    quote_version_id: Mapped[uuid.UUID] = _uuid()
    sku_id: Mapped[uuid.UUID] = _uuid()
    quantity_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    quantity_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price_minor: Mapped[int | None] = _money(nullable=True)  # NULL = 待询价
    tax_included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    delivery_at: Mapped[datetime | None] = _ts(nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL unknown, 0 none
    stock_as_of: Mapped[datetime | None] = _ts(nullable=True)
    listing_key: Mapped[str] = mapped_column(Text, nullable=False)


# --- requests -----------------------------------------------------------------


class PurchaseRequest(Row, Mutable, Base):
    __tablename__ = "purchase_request"
    __table_args__ = (
        tenant_key(),
        fk("requester_id", "principal"),
        fk("scope_id", "access_scope"),
        fk("current_revision_id", "request_revision", use_alter=True),
        UniqueConstraint("tenant_id", "request_no"),
        one_of("business_type", "A", "B"),
        one_of("status", "DRAFT", "SUBMITTED", "FULFILLING", "COMPLETED", "CANCELLED"),
        non_negative("model_call_budget"),
        non_negative("token_budget"),
    )

    request_no: Mapped[str] = mapped_column(Text, nullable=False)
    requester_id: Mapped[uuid.UUID] = _uuid()
    department_id: Mapped[uuid.UUID] = _uuid()
    scope_id: Mapped[uuid.UUID] = _uuid()
    business_type: Mapped[str] = mapped_column(Text, nullable=False)
    current_revision_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    # 0 means the model path is closed for this request
    model_call_budget: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    token_budget: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class RequestRevision(Row, Mutable, Base):
    __tablename__ = "request_revision"
    __table_args__ = (
        tenant_key(),
        fk("request_id", "purchase_request"),
        UniqueConstraint("tenant_id", "request_id", "revision"),
        non_negative("budget_minor"),
        non_negative("unit_budget_minor"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    request_id: Mapped[uuid.UUID] = _uuid()
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_minor: Mapped[int | None] = _money(nullable=True)
    unit_budget_minor: Mapped[int | None] = _money(nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CNY'"))
    required_by: Mapped[datetime | None] = _ts(nullable=True)
    destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    workload: Mapped[dict] = _json()
    missing_fields: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)  # set when frozen on submit


class RequestLine(Row, Base):
    __tablename__ = "request_line"
    __table_args__ = (
        tenant_key(),
        fk("request_revision_id", "request_revision"),
        fk("target_asset_id", "asset"),
        CheckConstraint("quantity IS NULL OR quantity >= 1", name="quantity_positive"),
    )

    request_revision_id: Mapped[uuid.UUID] = _uuid()
    category: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL while a draft lacks it
    template_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    minimum_specs: Mapped[dict] = _json()
    target_asset_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    applicability: Mapped[dict] = _json()


# --- plans --------------------------------------------------------------------


class PlanVersion(Row, Mutable, Base):
    __tablename__ = "plan_version"
    __table_args__ = (
        tenant_key(),
        fk("request_revision_id", "request_revision"),
        fk("author_id", "principal"),
        UniqueConstraint("tenant_id", "plan_key", "revision"),
        one_of(
            "status",
            "DRAFT",
            "NEEDS_INFO",
            "TECH_REVIEW",
            "PROCUREMENT_REVIEW",
            "APPROVED",
            "EXECUTING",
            "COMPLETED",
            "REJECTED",
            "SUPERSEDED",
            "INVALIDATED",
            "CANCELLED",
        ),
        non_negative("internal_cost_minor"),
        non_negative("total_minor"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    request_revision_id: Mapped[uuid.UUID] = _uuid()
    plan_key: Mapped[uuid.UUID] = _uuid()
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[uuid.UUID] = _uuid()
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    internal_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    total_minor: Mapped[int | None] = _money(nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CNY'"))
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict] = _json()
    assumptions: Mapped[dict] = _json()
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = _ts(nullable=True)


class PlanLine(Row, Base):
    __tablename__ = "plan_line"
    __table_args__ = (
        tenant_key(),
        fk("plan_version_id", "plan_version"),
        fk("request_line_id", "request_line"),
        fk("sku_id", "sku"),
        fk("target_asset_id", "asset"),
        fk("quote_line_id", "quote_line"),
        fk("upgrade_for_line_id", "plan_line"),
        one_of("action", "REUSE", "UPGRADE", "BUY"),
        non_negative("unit_price_minor"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("line_total_minor = unit_price_minor * quantity", name="line_total_recomputable"),
        CheckConstraint(
            "action <> 'REUSE' OR (quantity = 1 AND target_asset_id IS NOT NULL)",
            name="reuse_is_one_asset",
        ),
        CheckConstraint("action <> 'UPGRADE' OR target_asset_id IS NOT NULL", name="upgrade_has_target"),
        CheckConstraint("action <> 'BUY' OR quote_line_id IS NOT NULL", name="buy_has_quote"),
        CheckConstraint("upgrade_for_line_id IS NULL OR action = 'BUY'", name="only_buy_links_upgrade"),
    )

    plan_version_id: Mapped[uuid.UUID] = _uuid()
    request_line_id: Mapped[uuid.UUID] = _uuid()
    action: Mapped[str] = mapped_column(Text, nullable=False)
    sku_id: Mapped[uuid.UUID] = _uuid()
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    target_asset_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    quote_line_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    upgrade_for_line_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    unit_price_minor: Mapped[int] = _money()
    line_total_minor: Mapped[int] = _money()


# --- audit --------------------------------------------------------------------


class BusinessEvent(Row, Base):
    """Append-only. The migration installs a trigger that rejects UPDATE and DELETE."""

    __tablename__ = "business_event"
    __table_args__ = (
        tenant_key(),
        fk("actor_id", "principal"),
        UniqueConstraint("tenant_id", "aggregate_type", "aggregate_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = _uuid()
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID] = _uuid()
    occurred_at: Mapped[datetime] = _ts()
    payload: Mapped[dict] = _json()
    correlation_id: Mapped[uuid.UUID] = _uuid()
