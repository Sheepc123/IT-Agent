"""Business A analysis: reuse, gap, quote classification and ranking.

Pure functions. No database access and no model calls, so every amount and
boundary instant can be tested directly. All money is integer minor units (分).

Rules (docs/业务需求规格说明书.md REQ-A01, REQ-A02, REQ-R01):
- an asset counts only if it is an idle device whose memory and storage are not
  below the requirement;
- a quote is valid in [valid_from, valid_until);
- unknown price, shipping, stock or delivery date is "pending", never treated as 0;
- candidates are ordered by delivered total, delivery date, then quote key.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime

RULE_VERSION = "RULE-DEMO-1"
MAX_ITEMS = 3


@dataclass(frozen=True)
class Need:
    quantity: int
    min_memory_gib: int
    min_storage_bytes: int
    budget_minor: int
    required_by: datetime
    currency: str = "CNY"


@dataclass(frozen=True)
class AssetInfo:
    asset_tag: str
    kind: str
    lifecycle_status: str
    memory_gib: int | None
    storage_bytes: int | None


@dataclass(frozen=True)
class QuoteInfo:
    quote_key: str
    supplier_code: str
    channel_name: str
    sku_code: str
    memory_gib: int | None
    storage_bytes: int | None
    status: str
    currency: str
    tax_included: bool
    unit_price_minor: int | None
    shipping_minor: int | None
    available_quantity: int | None
    valid_from: datetime
    valid_until: datetime
    delivery_at: datetime | None


@dataclass(frozen=True)
class Candidate:
    quote_key: str
    supplier_code: str
    channel_name: str
    sku_code: str
    quantity: int
    unit_price_minor: int
    shipping_minor: int
    total_minor: int
    delivery_at: datetime
    available_quantity: int


@dataclass
class Analysis:
    result_status: str  # READY | NEEDS_QUOTE | NEEDS_INFO | NO_MATCH
    reused_assets: list[str] = field(default_factory=list)
    excluded_assets: list[dict] = field(default_factory=list)
    gap: int = 0
    items: list[Candidate] = field(default_factory=list)
    excluded_items: list[dict] = field(default_factory=list)
    pending_items: list[dict] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)


def meets_spec(memory_gib: int | None, storage_bytes: int | None, need: Need) -> bool:
    """Unknown specs do not satisfy a requirement."""
    return (
        memory_gib is not None
        and storage_bytes is not None
        and memory_gib >= need.min_memory_gib
        and storage_bytes >= need.min_storage_bytes
    )


def match_assets(need: Need, assets: list[AssetInfo]) -> tuple[list[str], list[dict]]:
    """Returns (asset tags to reuse, excluded assets with a reason)."""
    usable: list[str] = []
    excluded: list[dict] = []
    for asset in sorted(assets, key=lambda a: a.asset_tag):
        if asset.kind != "DEVICE":
            reason = "NOT_A_DEVICE"
        elif asset.lifecycle_status == "IN_USE":
            reason = "IN_USE"
        elif asset.lifecycle_status != "AVAILABLE":
            reason = "NOT_AVAILABLE"
        elif not meets_spec(asset.memory_gib, asset.storage_bytes, need):
            reason = "SPEC_BELOW_REQUIREMENT"
        else:
            usable.append(asset.asset_tag)
            continue
        excluded.append({"asset_tag": asset.asset_tag, "reason": reason})
    return usable[: need.quantity], excluded


def classify_quote(quote: QuoteInfo, need: Need, gap: int, now: datetime) -> tuple[str, str | Candidate]:
    """Returns ("item", Candidate), ("excluded", reason) or ("pending", reason)."""
    if quote.status != "ACTIVE":
        return "excluded", "WITHDRAWN"
    if now < quote.valid_from:
        return "excluded", "NOT_YET_VALID"
    if now >= quote.valid_until:
        return "excluded", "EXPIRED"
    if quote.currency != need.currency or not quote.tax_included:
        return "excluded", "NOT_COMPARABLE"
    if quote.unit_price_minor is None or quote.shipping_minor is None:
        return "pending", "PRICE_UNKNOWN"
    if quote.available_quantity is None:
        return "pending", "STOCK_UNKNOWN"
    if quote.available_quantity == 0:
        return "excluded", "OUT_OF_STOCK"
    if quote.available_quantity < gap:
        return "excluded", "INSUFFICIENT_STOCK"
    if quote.delivery_at is None:
        return "pending", "DELIVERY_UNKNOWN"
    if quote.delivery_at > need.required_by:
        return "excluded", "LATE_DELIVERY"
    total = quote.unit_price_minor * gap + quote.shipping_minor
    if total > need.budget_minor:
        return "excluded", "OVER_BUDGET"
    return "item", Candidate(
        quote_key=quote.quote_key, supplier_code=quote.supplier_code, channel_name=quote.channel_name,
        sku_code=quote.sku_code, quantity=gap, unit_price_minor=quote.unit_price_minor,
        shipping_minor=quote.shipping_minor, total_minor=total, delivery_at=quote.delivery_at,
        available_quantity=quote.available_quantity,
    )


def analyze(need: Need, assets: list[AssetInfo], quotes: list[QuoteInfo], now: datetime) -> Analysis:
    reused, excluded_assets = match_assets(need, assets)
    gap = need.quantity - len(reused)
    result = Analysis(result_status="READY", reused_assets=reused, excluded_assets=excluded_assets, gap=gap)
    if gap == 0:
        return result  # fully covered by existing assets: transfer only, nothing to buy

    relevant = [q for q in quotes if meets_spec(q.memory_gib, q.storage_bytes, need)]
    if not relevant:
        result.result_status = "NO_MATCH"
        result.next_actions = ["ADJUST_REQUEST"]
        return result

    for quote in sorted(relevant, key=lambda q: q.quote_key):
        kind, outcome = classify_quote(quote, need, gap, now)
        if kind == "item":
            result.items.append(outcome)
        elif kind == "excluded":
            result.excluded_items.append({"quote_key": quote.quote_key, "reason": outcome})
        else:
            result.pending_items.append({"quote_key": quote.quote_key, "reason": outcome})

    result.items.sort(key=lambda c: (c.total_minor, c.delivery_at, c.quote_key))
    result.items = result.items[:MAX_ITEMS]
    if not result.items:
        over_budget = any(e["reason"] == "OVER_BUDGET" for e in result.excluded_items)
        result.result_status = "NEEDS_INFO" if over_budget else "NEEDS_QUOTE"
        result.next_actions = ["ADJUST_REQUEST"] if over_budget else ["REQUEST_QUOTES"]
    return result


def to_snapshot(result: Analysis, need: Need, now: datetime) -> dict:
    """JSON-safe snapshot stored on the run. Money is a string of minor units."""

    def item(candidate: Candidate) -> dict:
        data = asdict(candidate)
        for key in ("unit_price_minor", "shipping_minor", "total_minor"):
            data[key] = str(data[key])
        data["delivery_at"] = candidate.delivery_at.isoformat()
        data["candidate_id"] = candidate.quote_key
        return data

    return {
        "result_status": result.result_status,
        "rule_version": RULE_VERSION,
        "data_as_of": now.isoformat(),
        "source_labels": {"market_data": "SYNTHETIC", "channel": "SIMULATED_STORE"},
        "need": {
            "quantity": need.quantity, "min_memory_gib": need.min_memory_gib,
            "min_storage_bytes": need.min_storage_bytes, "budget_minor": str(need.budget_minor),
            "required_by": need.required_by.isoformat(), "currency": need.currency,
        },
        "reused_assets": result.reused_assets,
        "excluded_assets": result.excluded_assets,
        "gap": result.gap,
        "items": [item(c) for c in result.items],
        "excluded_items": result.excluded_items,
        "pending_items": result.pending_items,
        "next_actions": result.next_actions,
    }
