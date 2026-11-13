"""Unit tests for the pure analysis functions. No database."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from it_procurement_api.analysis import AssetInfo, Need, QuoteInfo, analyze, to_snapshot

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)
TB = 1_000_000_000_000

NEED = Need(
    quantity=10, min_memory_gib=32, min_storage_bytes=TB, budget_minor=6_000_000,
    required_by=datetime(2026, 12, 1, 18, 0, tzinfo=TZ),
)


def asset(tag: str, status: str = "AVAILABLE", memory: int | None = 32, kind: str = "DEVICE") -> AssetInfo:
    return AssetInfo(asset_tag=tag, kind=kind, lifecycle_status=status, memory_gib=memory, storage_bytes=TB)


def quote(key: str, unit: int | None = 750_000, shipping: int | None = 100_000, **changes) -> QuoteInfo:
    base = QuoteInfo(
        quote_key=key, supplier_code=f"SUP-{key}", channel_name=f"模拟店铺 {key}", sku_code="SIM-NB-DEV-32-1T",
        memory_gib=32, storage_bytes=TB, status="ACTIVE", currency="CNY", tax_included=True,
        unit_price_minor=unit, shipping_minor=shipping, available_quantity=20,
        valid_from=datetime(2026, 11, 1, tzinfo=TZ), valid_until=datetime(2026, 11, 20, tzinfo=TZ),
        delivery_at=datetime(2026, 11, 25, 18, 0, tzinfo=TZ),
    )
    return replace(base, **changes)


ASSETS = [asset("AST-0001"), asset("AST-0002"), asset("AST-0003"), asset("AST-0004", "IN_USE"), asset("AST-0005", "IN_USE")]


def reasons(entries: list[dict], key: str) -> dict[str, str]:
    return {entry[key]: entry["reason"] for entry in entries}


def test_baseline_reuses_three_and_buys_seven():
    result = analyze(NEED, ASSETS, [quote("A"), quote("C", 780_000, 50_000)], T0)
    assert result.reused_assets == ["AST-0001", "AST-0002", "AST-0003"]
    assert reasons(result.excluded_assets, "asset_tag") == {"AST-0004": "IN_USE", "AST-0005": "IN_USE"}
    assert result.gap == 7
    assert [(c.quote_key, c.total_minor) for c in result.items] == [("A", 5_350_000), ("C", 5_510_000)]
    assert result.result_status == "READY"


def test_assets_must_be_idle_devices_that_meet_the_spec():
    assets = [
        asset("A1", memory=16), asset("A2", memory=None), asset("A3", "MAINTENANCE"),
        asset("A4", kind="COMPONENT"), asset("A5", memory=64),
    ]
    result = analyze(NEED, assets, [quote("A")], T0)
    assert result.reused_assets == ["A5"]
    assert reasons(result.excluded_assets, "asset_tag") == {
        "A1": "SPEC_BELOW_REQUIREMENT", "A2": "SPEC_BELOW_REQUIREMENT", "A3": "NOT_AVAILABLE", "A4": "NOT_A_DEVICE",
    }


def test_full_coverage_needs_no_purchase_and_never_reuses_more_than_needed():
    result = analyze(replace(NEED, quantity=2), ASSETS, [quote("A")], T0)
    assert result.reused_assets == ["AST-0001", "AST-0002"]
    assert result.gap == 0 and result.items == [] and result.result_status == "READY"


def test_validity_includes_the_start_instant_and_excludes_the_end_instant():
    q = quote("A")
    assert analyze(NEED, ASSETS, [q], q.valid_from).items
    assert analyze(NEED, ASSETS, [q], q.valid_until - timedelta(seconds=1)).items
    at_end = analyze(NEED, ASSETS, [q], q.valid_until)
    assert at_end.items == [] and reasons(at_end.excluded_items, "quote_key") == {"A": "EXPIRED"}
    assert at_end.result_status == "NEEDS_QUOTE"
    before = analyze(NEED, ASSETS, [q], q.valid_from - timedelta(seconds=1))
    assert reasons(before.excluded_items, "quote_key") == {"A": "NOT_YET_VALID"}


def test_each_exclusion_and_pending_reason():
    quotes = [
        quote("WITHDRAWN", status="WITHDRAWN"),
        quote("USD", currency="USD"),
        quote("NOTAX", tax_included=False),
        quote("NOPRICE", unit=None),
        quote("NOSHIP", shipping=None),
        quote("NOSTOCKINFO", available_quantity=None),
        quote("ZERO", available_quantity=0),
        quote("FEW", available_quantity=6),
        quote("NODATE", delivery_at=None),
        quote("LATE", delivery_at=NEED.required_by + timedelta(seconds=1)),
        quote("DEAR", unit=900_000),
    ]
    result = analyze(NEED, ASSETS, quotes, T0)
    assert result.items == []
    assert reasons(result.excluded_items, "quote_key") == {
        "WITHDRAWN": "WITHDRAWN", "USD": "NOT_COMPARABLE", "NOTAX": "NOT_COMPARABLE", "ZERO": "OUT_OF_STOCK",
        "FEW": "INSUFFICIENT_STOCK", "LATE": "LATE_DELIVERY", "DEAR": "OVER_BUDGET",
    }
    assert reasons(result.pending_items, "quote_key") == {
        "NOPRICE": "PRICE_UNKNOWN", "NOSHIP": "PRICE_UNKNOWN", "NOSTOCKINFO": "STOCK_UNKNOWN",
        "NODATE": "DELIVERY_UNKNOWN",
    }
    assert result.result_status == "NEEDS_INFO" and result.next_actions == ["ADJUST_REQUEST"]


def test_total_equal_to_budget_is_allowed_and_one_fen_over_is_not():
    q = quote("A")  # 7 x 750000 + 100000 = 5350000
    assert analyze(replace(NEED, budget_minor=5_350_000), ASSETS, [q], T0).items
    over = analyze(replace(NEED, budget_minor=5_349_999), ASSETS, [q], T0)
    assert reasons(over.excluded_items, "quote_key") == {"A": "OVER_BUDGET"}


def test_stock_exactly_equal_to_gap_and_delivery_exactly_on_deadline_are_accepted():
    q = quote("A", available_quantity=7, delivery_at=NEED.required_by)
    assert [c.quote_key for c in analyze(NEED, ASSETS, [q], T0).items] == ["A"]


def test_ranking_by_total_then_delivery_then_key_and_at_most_three():
    early = datetime(2026, 11, 22, tzinfo=TZ)
    quotes = [
        quote("D", 760_000), quote("C", 750_000), quote("B", 750_000, delivery_at=early),
        quote("A", 750_000), quote("E", 740_000),
    ]
    assert [c.quote_key for c in analyze(NEED, ASSETS, quotes, T0).items] == ["E", "B", "A"]


def test_no_sku_meeting_the_spec_is_no_match():
    result = analyze(replace(NEED, min_memory_gib=64), ASSETS, [quote("A")], T0)
    assert result.result_status == "NO_MATCH" and result.items == [] and result.gap == 10


def test_snapshot_money_is_integer_strings():
    result = analyze(NEED, ASSETS, [quote("A")], T0)
    snapshot = to_snapshot(result, NEED, T0)
    assert snapshot["items"][0]["total_minor"] == "5350000"
    assert snapshot["need"]["budget_minor"] == "6000000"
    assert snapshot["data_as_of"] == "2026-11-12T12:00:00+08:00"
    assert snapshot["source_labels"]["market_data"] == "SYNTHETIC"
