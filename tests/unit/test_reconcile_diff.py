from decimal import Decimal

from app.services.reconcile import diff


def test_decimal_tolerance_and_none_missing():
    assert (
        diff("team", "1", {"max_budget": Decimal("10"), "tpm_limit": None}, {"max_budget": 10.009})
        == []
    )
    assert len(diff("team", "1", {"max_budget": 10}, {"max_budget": 10.02})) == 1


def test_model_dict_per_key():
    rows = diff(
        "team", "1", {"model_max_budget": {"a": 10}}, {"model_max_budget": {"a": 12, "b": 3}}
    )
    assert {r.field for r in rows} == {"model_max_budget.a", "model_max_budget.b"}


def test_role_fields_ignored():
    assert diff("employee", "x", {"role": "admin"}, {"role": "user"}) == []
