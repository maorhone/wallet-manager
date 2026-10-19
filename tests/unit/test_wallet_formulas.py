"""SPEC §12.3 formula table, one test per row."""

from decimal import Decimal as D

from app.services import wallet


def test_department_row():
    w = wallet.department_wallet(D(1000), D(700), D(180), D(50), D(300))
    assert (w["total"], w["allocated"], w["unallocated"]) == (1000, 930, 70)
    assert (w["used"], w["remaining"], w["percent"]) == (300, 700, D("0.3"))


def test_pmo_pool_row():
    # formula alone; data arrives in Phase 2
    w = wallet.pmo_wallet(D(180), D(100), D(20), D(35))
    assert (w["total"], w["allocated"], w["used"]) == (180, 100, 35)
    assert w["unallocated"] == w["remaining"] == 60


def test_group_row():
    w = wallet.group_wallet(D(250), D(50), D(200), D(30), D(120))
    assert (w["total"], w["allocated"], w["unallocated"]) == (300, 230, 70)
    assert (w["used"], w["remaining"]) == (120, 180)
    assert w["percent"] == D("0.4")


def test_employee_row():
    w = wallet.employee_wallet(D(15000), D(5000), D(10000))
    assert (w["total"], w["used"], w["remaining"]) == (20000, 10000, 10000)
    assert w["percent"] == D("0.5")


def test_employee_effective_row():
    assert wallet.effective_remaining(D(100), D(40), D(70)) == 40
    assert wallet.effective_remaining(D(-5), D(40), D(70)) == -5


def test_binding_level():
    assert wallet.binding_level(D(10), D(40), D(70)) == "self"
    assert wallet.binding_level(D(100), D(40), D(70)) == "group"
    assert wallet.binding_level(D(100), D(400), D(70)) == "department"
    assert wallet.binding_level(D(40), D(40), D(70)) == "self"  # ties go to the narrower level


def test_zero_total_percent():
    assert wallet.employee_wallet(D(0), D(0), D(0))["percent"] == 0
    assert wallet.employee_wallet(D(0), D(0), D(5))["percent"] == 1
