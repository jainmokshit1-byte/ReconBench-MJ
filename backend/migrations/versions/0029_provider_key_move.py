"""Gemini key moves to its provider card; the card gains the key tier (accuracy plan WI-PV2).

* ``ai_provider_config.key_tier`` (``free`` or ``paid``, default ``free``): declared by the
  admin at connect because the provider API cannot say which tier a key belongs to
  (decision-log D83/D84). A vLLM card is always ``free`` (the value is not used). Every
  existing card, including the live Gemini card, migrates as ``free`` (owner, D84).
* ``ai_provider_config.pending_key_secret_id``: a candidate key during a rotation. The old
  key stays active until the candidate passes a provider test; then the worker swaps them.
* The Gemini key reference moves from ``ai_setting.key_secret_id`` to the Gemini card. The
  secret row itself is **not copied or re-encrypted**: only the pointer moves, so the
  ciphertext, nonce and key id are byte for byte what they were. The old pointer is cleared
  **only after** the migration has verified, for every moved key, that the card points at a
  secret row of the same workspace that still holds ciphertext and nonce; any mismatch
  raises and the whole migration rolls back. The migration never reads or prints key
  material. A card that already holds a different key than the legacy pointer is a
  conflict and also aborts the migration.
* The card also takes over the connection state the legacy ``ai_setting`` test result
  implies: a Gemini card whose last test passed is ``connected_at`` that test time.

``downgrade()`` is real here (the owner asked for a tested path): it moves the pointer
back with the same verify-then-clear order, deletes any pending rotation candidate and
drops the two columns. Cards created after 0029 and their state stay as they are.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_COLUMNS = r"""ALTER TABLE ai_provider_config
  ADD COLUMN key_tier TEXT NOT NULL DEFAULT 'free',
  ADD COLUMN pending_key_secret_id UUID REFERENCES secret(id),
  ADD CONSTRAINT ai_provider_config_key_tier_ck CHECK (key_tier IN ('free', 'paid')),
  ADD CONSTRAINT ai_provider_config_key_tier_provider_ck CHECK (provider = 'gemini' OR key_tier = 'free'),
  ADD CONSTRAINT ai_provider_config_pending_key_ck CHECK (pending_key_secret_id IS NULL OR pending_key_secret_id IS DISTINCT FROM key_secret_id);"""

_MOVE = r"""DO $move$
DECLARE
  expected integer;
  verified integer;
BEGIN
  -- A Gemini workspace with a key but no card yet (defensive: 0026 created one for every Gemini row).
  INSERT INTO ai_provider_config (id, workspace_id, provider, enabled, model, updated_by, updated_at, monthly_cost_cap_inr, doc_cost_flag_inr)
  SELECT gen_random_uuid(), s.workspace_id, 'gemini', s.mode <> 'rules_only', s.model, s.updated_by, s.updated_at, 2500.00, 20.00
    FROM ai_setting s
   WHERE s.provider = 'gemini' AND s.key_secret_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM ai_provider_config c WHERE c.workspace_id = s.workspace_id AND c.provider = 'gemini');

  SELECT count(*) INTO expected FROM ai_setting WHERE provider = 'gemini' AND key_secret_id IS NOT NULL;

  UPDATE ai_provider_config c
     SET key_secret_id = s.key_secret_id,
         connected_at = CASE WHEN s.last_test_ok IS TRUE THEN s.last_test_at END,
         version = c.version + 1,
         updated_at = now()
    FROM ai_setting s
   WHERE c.workspace_id = s.workspace_id AND c.provider = 'gemini'
     AND s.provider = 'gemini' AND s.key_secret_id IS NOT NULL AND c.key_secret_id IS NULL;

  SELECT count(*) INTO verified
    FROM ai_setting s
    JOIN ai_provider_config c ON c.workspace_id = s.workspace_id AND c.provider = 'gemini' AND c.key_secret_id = s.key_secret_id
    JOIN secret sec ON sec.id = c.key_secret_id AND sec.workspace_id = c.workspace_id
                   AND length(sec.ciphertext) > 0 AND length(sec.nonce) > 0
   WHERE s.provider = 'gemini' AND s.key_secret_id IS NOT NULL;

  IF verified <> expected THEN
    RAISE EXCEPTION 'gemini key move verification failed: % of % keys verified on the card', verified, expected;
  END IF;

  UPDATE ai_setting SET key_secret_id = NULL WHERE provider = 'gemini' AND key_secret_id IS NOT NULL;
END
$move$;"""

_BACK = r"""DO $back$
DECLARE
  expected integer;
  verified integer;
  pending uuid[];
BEGIN
  IF EXISTS (
    SELECT 1 FROM ai_provider_config c JOIN ai_setting s ON s.workspace_id = c.workspace_id
     WHERE c.provider = 'gemini' AND c.key_secret_id IS NOT NULL AND s.key_secret_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'downgrade refused: a workspace holds a key on both ai_setting and its Gemini card';
  END IF;

  SELECT count(*) INTO expected
    FROM ai_provider_config c JOIN ai_setting s ON s.workspace_id = c.workspace_id
   WHERE c.provider = 'gemini' AND c.key_secret_id IS NOT NULL;

  UPDATE ai_setting s
     SET key_secret_id = c.key_secret_id
    FROM ai_provider_config c
   WHERE c.workspace_id = s.workspace_id AND c.provider = 'gemini'
     AND c.key_secret_id IS NOT NULL AND s.key_secret_id IS NULL;

  SELECT count(*) INTO verified
    FROM ai_setting s
    JOIN ai_provider_config c ON c.workspace_id = s.workspace_id AND c.provider = 'gemini' AND c.key_secret_id = s.key_secret_id
    JOIN secret sec ON sec.id = s.key_secret_id AND sec.workspace_id = s.workspace_id
                   AND length(sec.ciphertext) > 0 AND length(sec.nonce) > 0;

  IF verified <> expected THEN
    RAISE EXCEPTION 'gemini key move back failed verification: % of % keys verified on ai_setting', verified, expected;
  END IF;

  UPDATE ai_provider_config c
     SET key_secret_id = NULL, version = c.version + 1, updated_at = now()
    FROM ai_setting s
   WHERE c.workspace_id = s.workspace_id AND c.provider = 'gemini' AND c.key_secret_id = s.key_secret_id;

  SELECT array_agg(pending_key_secret_id) INTO pending FROM ai_provider_config WHERE pending_key_secret_id IS NOT NULL;
  UPDATE ai_provider_config SET pending_key_secret_id = NULL WHERE pending_key_secret_id IS NOT NULL;
  IF pending IS NOT NULL THEN
    DELETE FROM secret WHERE id = ANY(pending);
  END IF;
END
$back$;"""

_DROP = r"""ALTER TABLE ai_provider_config
  DROP CONSTRAINT ai_provider_config_pending_key_ck,
  DROP CONSTRAINT ai_provider_config_key_tier_provider_ck,
  DROP CONSTRAINT ai_provider_config_key_tier_ck,
  DROP COLUMN pending_key_secret_id,
  DROP COLUMN key_tier;"""


def upgrade() -> None:
    execute_sql_block(_COLUMNS)
    execute_sql_block(_MOVE)


def downgrade() -> None:
    execute_sql_block(_BACK)
    execute_sql_block(_DROP)
