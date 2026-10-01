"""JSON conversion helpers that preserve Decimal values as decimal strings."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def decimal_to_string(value: Decimal) -> str:
    """Format a Decimal without converting it to a binary float."""

    return format(value, "f")


def to_jsonable(value: Any) -> Any:
    """Convert nested API data while encoding every Decimal as a JSON string."""

    return jsonable_encoder(value, custom_encoder={Decimal: decimal_to_string})


class DecimalJSONResponse(JSONResponse):
    """JSON response for direct content that preserves Decimal values as strings."""

    def render(self, content: Any) -> bytes:
        return super().render(to_jsonable(content))


__all__ = ["DecimalJSONResponse", "decimal_to_string", "to_jsonable"]
