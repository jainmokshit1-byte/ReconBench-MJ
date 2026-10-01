"""Exact SQLAlchemy numeric domain types from Backend Schema §3."""

from __future__ import annotations

from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import DOMAIN

D_AMOUNT = DOMAIN("d_amount", Numeric(18, 2), create_type=False)
D_QTY = DOMAIN("d_qty", Numeric(18, 4), create_type=False)
D_SCORE = DOMAIN("d_score", Numeric(5, 4), create_type=False)
D_TAXRATE = DOMAIN("d_taxrate", Numeric(5, 2), create_type=False)

__all__ = ["D_AMOUNT", "D_QTY", "D_SCORE", "D_TAXRATE"]
