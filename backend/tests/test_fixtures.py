"""Fixture sets from docs/种子评测规格.md section 3. Synthetic data; rolled back after each test."""

import copy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from it_procurement_api.db.models import Asset, QuoteLine, QuoteVersion, SourceBatch
from it_procurement_api.fixtures import (
    TENANT_ID,
    FixtureError,
    fixture_id,
    load_fixture_set,
    loaded_counts,
    read_part,
    read_sets,
    validate,
)

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=timezone(timedelta(hours=8)))


def quotes(session) -> dict[str, tuple[QuoteVersion, QuoteLine]]:
    rows = session.execute(
        select(QuoteVersion, QuoteLine)
        .join(QuoteLine, QuoteLine.quote_version_id == QuoteVersion.id)
        .where(QuoteVersion.tenant_id == TENANT_ID)
    ).all()
    return {quote.quote_key: (quote, line) for quote, line in rows}


def delivered_total(quote: QuoteVersion, line: QuoteLine, quantity: int) -> int:
    return line.unit_price_minor * quantity + quote.shipping_minor


def test_base_set_matches_the_demo_baseline(session):
    counts = load_fixture_set(session, "FX-A-BASE")
    assert counts == {
        "principal": 12, "access_scope": 3, "role_grant": 11, "source_batch": 3,
        "product_model": 1, "sku": 1, "asset": 5, "supplier": 3, "quote_version": 3, "quote_line": 3,
    }

    assets = session.scalars(select(Asset).where(Asset.tenant_id == TENANT_ID)).all()
    available = sorted(a.asset_tag for a in assets if a.lifecycle_status == "AVAILABLE")
    assert available == ["AST-0001", "AST-0002", "AST-0003"]
    assert all(a.config_snapshot["memory_gib"] == 32 for a in assets)

    q = quotes(session)
    assert delivered_total(*q["Q-A"], 7) == 5_350_000
    assert delivered_total(*q["Q-C"], 7) == 5_510_000
    assert delivered_total(*q["Q-B"], 7) == 5_120_000
    # validity is [valid_from, valid_until): A and C are valid at T0, B is not
    assert q["Q-A"][0].valid_from <= T0 < q["Q-A"][0].valid_until
    assert q["Q-C"][0].valid_from <= T0 < q["Q-C"][0].valid_until
    assert q["Q-B"][0].valid_until <= T0

    batches = session.scalars(select(SourceBatch).where(SourceBatch.tenant_id == TENANT_ID)).all()
    assert {(b.source_type, b.quality_status) for b in batches} == {("SYNTHETIC", "PASSED")}
    assert {quote.market_data_kind for quote, _ in q.values()} == {"SYNTHETIC"}


def test_loading_again_gives_the_same_rows(session):
    load_fixture_set(session, "FX-A-BASE")
    first = loaded_counts(session)
    load_fixture_set(session, "FX-A-BASE")
    assert loaded_counts(session) == first
    assert session.get(Asset, fixture_id("asset", "AST-0001")).asset_tag == "AST-0001"


def test_switching_sets_leaves_no_rows_from_the_previous_one(session):
    load_fixture_set(session, "FX-A-BASE")
    load_fixture_set(session, "FX-A-BUYONLY")
    counts = loaded_counts(session)
    assert counts["asset"] == 0
    assert counts["quote_version"] == 3


def test_quote_variants_keep_unknown_and_zero_apart(session):
    load_fixture_set(session, "FX-A-QUOTEVAR")
    q = quotes(session)
    assert len(q) == 9
    assert delivered_total(*q["Q-D"], 7) == 4_950_000
    assert q["Q-D"][0].valid_from > T0  # not yet in effect
    assert q["Q-E"][0].currency == "USD" and q["Q-E"][0].shipping_minor is None
    assert q["Q-K"][1].available_quantity == 5
    assert q["Q-L"][1].available_quantity == 0  # known: out of stock
    assert q["Q-N"][1].unit_price_minor is None  # unknown price
    assert q["Q-S"][1].available_quantity is None and q["Q-S"][1].stock_as_of is None  # unknown stock


def test_every_set_passes_quality_checks():
    for name, spec in read_sets()["sets"].items():
        parts = {part: read_part(part)[0] for part in spec["parts"]}
        assert validate(parts, spec.get("expect")) == [], name


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p["a_quotes"]["quotes"][0]["lines"][0].update(unit_price_minor=-1), "unit_price_minor"),
        (lambda p: p["a_quotes"]["quotes"][0]["lines"][0].update(unit_price_minor=7500.0), "unit_price_minor"),
        (lambda p: p["a_quotes"]["quotes"][0].update(valid_until="2026-11-01T00:00:00+08:00"), "valid_from"),
        (lambda p: p["a_quotes"]["quotes"][0].update(valid_until="2026-11-20T00:00:00"), "time zone"),
        (lambda p: p["a_quotes"]["quotes"][0].update(supplier="SUP-Z"), "unknown supplier"),
        (lambda p: p["a_catalog"]["skus"][0].update(sku_code="NB-DEV"), "SIM-"),
        (lambda p: p["a_assets"]["assets"][0].update(status="IN_USE"), "assigned_to"),
        (lambda p: p["a_quotes"]["quotes"][0].update(shipping_minor=0), "expected 5350000"),
    ],
)
def test_quality_checks_reject_bad_data(change, message):
    spec = read_sets()["sets"]["FX-A-BASE"]
    parts = copy.deepcopy({part: read_part(part)[0] for part in spec["parts"]})
    change(parts)
    assert any(message in problem for problem in validate(parts, spec["expect"]))


def test_unknown_set_is_an_error(session):
    with pytest.raises(FixtureError):
        load_fixture_set(session, "FX-NOPE")
