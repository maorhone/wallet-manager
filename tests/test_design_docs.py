"""Guards the U0 design contracts against regressing on review findings."""

from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs" / "design"
TRANSFERS = (DOCS / "transfers-v2.md").read_text()
COMMITMENTS = (DOCS / "commitments.md").read_text()


def test_held_restore_excluded_from_every_payload():  # redesign U0-fix1 held-restore
    assert "held_restore" in TRANSFERS
    assert "`payload(E)`" in TRANSFERS
    assert "domain_reverted" in TRANSFERS


def test_confirmation_requires_inclusion_not_version():  # redesign U0-fix1 included
    assert "`included`" in TRANSFERS
    assert "entity_version ≤ v" not in TRANSFERS


def test_reverted_receipts_not_pending_incoming():  # redesign U0-fix1 pending-incoming
    assert "not** `domain_reverted`" in TRANSFERS
    assert "excluded: their credit already left" in COMMITMENTS.replace("\n", " ")


def test_target_publication_and_absent_source_rows():  # redesign U0-fix1 rows
    assert "| 2b " in TRANSFERS
    assert "activated(E)" in TRANSFERS


def test_backfill_covers_source_only_repairs():  # redesign U0-fix1 backfill
    assert "source-only repair" in TRANSFERS
    assert "legacy_restore_failed" in TRANSFERS
