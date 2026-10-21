"""Vision provenance and cache key v2 (LLM extraction plan WI-BE5, decision-log D63).

* ``ai_request.reader_params``: the reader and image parameters of the request
  (reader, render and mask versions, image size, resolution, thinking, sampling,
  prompt variant, pass), as canonical JSON. Text hooks keep ``{}``.
* ``ai_cache.provider``, ``ai_cache.schema_version``, ``ai_cache.reader_params``:
  what produced a cached answer. ``NULL`` on entries written before this revision;
  the vision cache key now includes provider and reader parameters, so those old
  vision entries are simply never hit again and expire under the normal TTL.

Runs as reconbench_migrator (revision 0015), so default privileges apply. No
data is rewritten.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_READER_PARAMS = r"""ALTER TABLE ai_request
  ADD COLUMN reader_params JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD CONSTRAINT ai_request_reader_params_ck CHECK (jsonb_typeof(reader_params) = 'object');

ALTER TABLE ai_cache
  ADD COLUMN provider ai_provider,
  ADD COLUMN schema_version TEXT,
  ADD COLUMN reader_params JSONB,
  ADD CONSTRAINT ai_cache_schema_version_ck
    CHECK (schema_version IS NULL OR length(schema_version) BETWEEN 1 AND 64),
  ADD CONSTRAINT ai_cache_reader_params_ck
    CHECK (reader_params IS NULL OR jsonb_typeof(reader_params) = 'object');"""


def upgrade() -> None:
    execute_sql_block(_READER_PARAMS)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
