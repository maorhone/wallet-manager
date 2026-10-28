"""Test helper: what a group has allocated, ignoring consumption (no spend rows)."""

from app.services import commitments
from app.services.spend import Spend


async def group_allocated(db, group):
    return (await commitments.snapshot(db, group, Spend([], "2000-01"), tz="UTC")).total


async def group_committed(db, spend, group, tz="UTC"):
    snap = await commitments.snapshot(db, group, spend, tz=tz)
    return snap.total + snap.consumption_pmo
