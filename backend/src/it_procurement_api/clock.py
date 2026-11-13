"""Server clock. Business rules read time only from here so tests can fix it."""

from contextlib import contextmanager
from datetime import datetime, timezone

_fixed: datetime | None = None


def now() -> datetime:
    return _fixed if _fixed is not None else datetime.now(timezone.utc)


@contextmanager
def frozen(moment: datetime):
    """Fix the clock inside a `with` block (tests and evaluation runs)."""
    global _fixed
    if moment.tzinfo is None:
        raise ValueError("the fixed clock needs a time zone")
    previous, _fixed = _fixed, moment
    try:
        yield
    finally:
        _fixed = previous
