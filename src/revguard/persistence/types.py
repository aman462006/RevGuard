"""Custom SQLAlchemy column types that preserve domain invariants on SQLite.

SQLite has no native decimal or timezone-aware datetime storage. These ``TypeDecorator``
implementations serialise values as canonical text so that:

* monetary :class:`~decimal.Decimal` values round-trip **exactly** (no float coercion), and
* datetimes round-trip **timezone-aware** (normalised to UTC ISO-8601).

Keeping this here means the rest of the codebase stores plain domain values and never sees
the serialisation detail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class DecimalText(TypeDecorator):
    """Stores a ``Decimal`` as its canonical string to preserve exact precision/scale."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return str(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return Decimal(value)


class AwareDateTime(TypeDecorator):
    """Stores a timezone-aware datetime as UTC ISO-8601 text; rejects naive datetimes."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be persisted; use a tz-aware value")
        return value.astimezone(UTC).isoformat()

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:  # defensive; stored values always carry an offset
            parsed = parsed.replace(tzinfo=UTC)
        return parsed


__all__ = ["DecimalText", "AwareDateTime"]
