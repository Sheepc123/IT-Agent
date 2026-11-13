"""Schema checks against the local PostgreSQL from infra/compose.yaml.

Requires `alembic upgrade head` to have been applied. Every test runs inside a
transaction that is rolled back, so no rows are left behind.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from it_procurement_api.db import get_engine
from it_procurement_api.db.models import (
    AccessScope,
    Asset,
    Base,
    BusinessEvent,
    Principal,
    ProductModel,
    QuoteVersion,
    RoleGrant,
    Sku,
    SourceBatch,
    Supplier,
)

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def session():
    connection = get_engine().connect()
    outer = connection.begin()
    s = Session(connection, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        outer.rollback()
        connection.close()


def rejected(session: Session, *rows, exc=IntegrityError) -> None:
    """The rows must be refused by the database; the session stays usable."""
    with pytest.raises(exc):
        with session.begin_nested():
            session.add_all(rows)
            session.flush()


class World:
    """Minimal valid rows for one tenant."""

    def __init__(self, session: Session):
        self.tenant = uuid.uuid4()
        self.actor = uuid.uuid4()
        base = dict(tenant_id=self.tenant, created_by=self.actor)
        self.base = base
        self.principal = Principal(id=self.actor, kind="USER", display_name="U-BUY-01", **base)
        self.scope = AccessScope(id=uuid.uuid4(), kind="ORG", **base)
        self.batch = SourceBatch(
            id=uuid.uuid4(), source_type="SYNTHETIC", source_name="test", captured_at=T0, content_hash="h", **base
        )
        self.model = ProductModel(
            id=uuid.uuid4(), manufacturer="SIM", model_code="SIM-NB-DEV", category="LAPTOP",
            source_batch_id=self.batch.id, **base,
        )
        self.sku = Sku(
            id=uuid.uuid4(), model_id=self.model.id, sku_code="SIM-NB-DEV-32-1T", hardware_revision="A",
            source_batch_id=self.batch.id, catalog_kind="SYNTHETIC", **base,
        )
        self.supplier = Supplier(id=uuid.uuid4(), supplier_code="SUP-A", name="Supplier A", **base)
        for row in (self.principal, self.scope, self.batch, self.model, self.sku, self.supplier):
            session.add(row)
            session.flush()

    def quote(self, **overrides) -> QuoteVersion:
        values = dict(
            supplier_id=self.supplier.id, scope_id=self.scope.id, quote_key="Q-A", revision=1,
            currency="CNY", price_basis="TAX_INCLUDED_DELIVERED", shipping_minor=100000,
            valid_from=datetime(2026, 11, 1, tzinfo=T0.tzinfo), valid_until=datetime(2026, 11, 20, tzinfo=T0.tzinfo),
            source_batch_id=self.batch.id, content_hash="h", channel_kind="SIMULATED_STORE",
            channel_name="模拟店铺 A", market_data_kind="SYNTHETIC", captured_at=T0, **self.base,
        )
        values.update(overrides)
        return QuoteVersion(**values)

    def asset(self, tag: str, **overrides) -> Asset:
        values = dict(
            sku_id=self.sku.id, scope_id=self.scope.id, asset_tag=tag, kind="DEVICE",
            lifecycle_status="AVAILABLE", location="SH-1F", **self.base,
        )
        values.update(overrides)
        return Asset(**values)


def test_database_is_at_migration_head():
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with get_engine().connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = set(inspect(connection).get_table_names())
    assert current == head
    assert set(Base.metadata.tables) <= tables
    assert len(Base.metadata.tables) == 16


def test_valid_rows_are_accepted(session):
    world = World(session)
    session.add_all([world.quote(), world.asset("AST-0001", serial_no="SN-1")])
    session.flush()


def test_quote_validity_must_be_a_non_empty_interval(session):
    world = World(session)
    moment = datetime(2026, 11, 20, tzinfo=T0.tzinfo)
    rejected(session, world.quote(valid_from=moment, valid_until=moment))


def test_money_cannot_be_negative_but_may_be_unknown(session):
    world = World(session)
    rejected(session, world.quote(shipping_minor=-1))
    session.add(world.quote(quote_key="Q-N", shipping_minor=None))
    session.flush()


def test_row_cannot_reference_another_tenant(session):
    world, other = World(session), World(session)
    rejected(
        session,
        Sku(
            model_id=other.model.id, sku_code="X", hardware_revision="A", source_batch_id=world.batch.id,
            catalog_kind="SYNTHETIC", **world.base,
        ),
    )


def test_asset_tag_and_serial_are_unique_per_tenant(session):
    world, other = World(session), World(session)
    session.add_all([
        world.asset("AST-0001", serial_no="SN-1"),
        world.asset("AST-0002"),
        world.asset("AST-0003"),  # several assets without a serial number are fine
        other.asset("AST-0001", serial_no="SN-1"),  # another tenant may reuse both
    ])
    session.flush()
    rejected(session, world.asset("AST-0001"))
    rejected(session, world.asset("AST-0009", serial_no="SN-1"))


def test_installed_status_is_only_for_components_in_a_host(session):
    world = World(session)
    rejected(session, world.asset("AST-0001", lifecycle_status="INSTALLED"))
    rejected(session, world.asset("CMP-0001", kind="COMPONENT", lifecycle_status="INSTALLED"))


def test_unknown_enum_value_is_rejected(session):
    world = World(session)
    rejected(session, world.asset("AST-0001", lifecycle_status="LOST"))
    rejected(session, world.quote(market_data_kind="REAL"))


def test_same_role_cannot_be_granted_twice_while_unrevoked(session):
    world = World(session)

    def grant(**overrides) -> RoleGrant:
        values = dict(principal_id=world.actor, scope_id=world.scope.id, role="BUYER", valid_from=T0, **world.base)
        values.update(overrides)
        return RoleGrant(**values)

    session.add(grant(revoked_at=T0 + timedelta(hours=1)))
    session.add(grant())
    session.flush()
    rejected(session, grant())


def test_business_event_is_append_only(session):
    world = World(session)
    aggregate = uuid.uuid4()

    def event(sequence: int) -> BusinessEvent:
        return BusinessEvent(
            aggregate_type="purchase_request", aggregate_id=aggregate, sequence=sequence,
            event_type="REQUEST_CREATED", actor_id=world.actor, occurred_at=T0,
            correlation_id=uuid.uuid4(), **world.base,
        )

    first = event(1)
    session.add(first)
    session.flush()
    rejected(session, event(1))  # duplicate sequence
    for statement in ("UPDATE business_event SET event_type = 'X' WHERE id = :id", "DELETE FROM business_event WHERE id = :id"):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(statement), {"id": first.id})
    assert session.get(BusinessEvent, first.id).event_type == "REQUEST_CREATED"
