"""Vendor display-name trust level (accuracy plan AC-4, decision-log D36).

``vendor.name_status`` records where the display name came from: ``manual``
(a person), ``verified`` (a digital/signed source or a checked value),
``provisional`` (an unverified OCR reading) or ``pending`` (no usable name yet;
``canonical_name`` is empty and the UI shows "(name pending)"). Vendor
resolution only upgrades a name to a better status and never replaces a
``manual`` name. Existing rows start as ``provisional`` (``manual`` when a
person created them); ``scripts/vendor_cleanup.py`` fixes the junk ones.
Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_NAME_STATUS = r"""CREATE TYPE vendor_name_status AS ENUM ('manual', 'verified', 'provisional', 'pending');
ALTER TABLE vendor ADD COLUMN name_status vendor_name_status NOT NULL DEFAULT 'provisional';
UPDATE vendor SET name_status = 'manual' WHERE created_by_rule = 'manual';"""


def upgrade() -> None:
    execute_sql_block(_NAME_STATUS)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
