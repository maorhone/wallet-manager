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
    assert "not** `domain_reverted`" in _flat(TRANSFERS)
    assert "are excluded: their credit already left" in _flat(COMMITMENTS)


def test_target_publication_and_absent_source_rows():  # redesign U0-fix1 rows
    assert "| 2b " in TRANSFERS
    assert "activated(E)" in TRANSFERS


def test_backfill_covers_source_only_repairs():  # redesign U0-fix1 backfill
    assert "source-only repair" in TRANSFERS
    assert "legacy_restore_failed" in TRANSFERS


def _flat(text):
    return " ".join(text.split())


def test_unreverted_compensation_receipts_stay_pending():  # redesign U0-fix2 pending-unreverted
    t, c = _flat(TRANSFERS), _flat(COMMITMENTS)
    assert "`T.state ≠ SETTLED` and **not** `domain_reverted`" in t
    assert "COMPENSATING or STUCK transfers before `RevertDomain`" in c
    assert "COMPENSATING, STUCK}`" in t and "unreverted receipts of E in" in t
    assert "unreadable compensation" in t or "12b" in t


def test_capacity_subtracts_held_restore():  # redesign U0-fix2 capacity
    c = _flat(COMMITMENTS)
    assert c.count("base_budget − pending_incoming − held_restore") >= 3


def test_upstream_application_fence():  # redesign U0-fix2 fence
    t = _flat(TRANSFERS)
    assert "single flight" in t and "no other leg of E is IN_FLIGHT or UNCERTAIN" in t
    assert "delayed_old_request" in t
    assert "A `target` leg is claimable only while its transfer is `PUBLISHING`" in t


def test_resolve_requires_domain_reconciliation():  # redesign U0-fix2 resolve
    t = _flat(TRANSFERS)
    assert "AND the domain is reverted" in t and "`RevertDomain` in the same transaction" in t
    assert "`domain_reverted=false` (domain still holds" in t
    assert "a transfer is COMPENSATED only with `domain_reverted`" in t


def test_restore_publication_vs_availability():  # redesign U0-fix2 restore-split
    t = _flat(TRANSFERS)
    assert "`unreleased_restore(S)` (publication hold" in t
    assert "Σ unreleased_restore(E)" in t


def test_conservation_predicate_scope_and_examples():  # redesign U0-fix2 conservation
    t = _flat(TRANSFERS)
    assert "Σ_E w(E) ≤ Σ_E domain(E)" in t
    assert "_safe, must pass_" in t and "_Counterexamples, must fail the predicate_" in t


def test_backfill_remaining_shapes():  # redesign U0-fix2 backfill-shapes
    t = _flat(TRANSFERS)
    assert "a target leg REJECTED (failed, attempts 0)" in t
    assert "every target leg is QUEUED with `attempts = 0`" in t
    assert "confirmed destination repair with an unfinished source restore" in t


def test_oracle_closed_shares_and_cut_floors():  # redesign U0-fix2 oracle
    c = _flat(COMMITMENTS)
    assert "a **closed** share contributes `min(left, stored used)`" in c
    assert "AND no cut floor is violated" in c
    assert "BELOW_CONSUMED` (floor)" in c
