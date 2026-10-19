from decimal import Decimal

from app.services.validation import check_limit, check_model_sums, check_sum


def test_check_sum_within_parent_is_ok():
    assert check_sum(Decimal(100), Decimal(100)) is None


def test_check_sum_over_parent_reports_numbers():
    err = check_sum(Decimal(620000), Decimal(600000))
    assert (err.code, err.status) == ("PARENT_OVERALLOCATED", 422)
    assert (err.requested, err.available, err.parent_total) == (620000, 600000, 600000)


def test_check_sum_factor_scales_available():
    assert check_sum(Decimal(110), Decimal(100), Decimal("1.1")) is None
    err = check_sum(Decimal(111), Decimal(100), Decimal("1.1"))
    assert err.available == Decimal("110.0") and err.parent_total == 100


def test_model_sums_missing_parent_is_zero():
    err = check_model_sums({1: {"gpt": Decimal(5)}}, {})
    assert err.code == "PARENT_OVERALLOCATED"
    assert err.details["model"] == "gpt" and err.available == 0 and err.requested == 5


def test_model_sums_sum_across_children_per_model():
    parent = {"a": Decimal(10), "b": Decimal(5)}
    assert (
        check_model_sums({1: {"a": Decimal(6)}, 2: {"a": Decimal(4), "b": Decimal(5)}}, parent)
        is None
    )
    err = check_model_sums({1: {"a": Decimal(6)}, 2: {"a": Decimal(5)}}, parent)
    assert err.details["model"] == "a" and err.requested == 11 and err.parent_total == 10


def test_check_limit():
    policy = {"min": 1, "max": 8}
    assert check_limit("max_parallel", 4, policy, False) is None
    err = check_limit("max_parallel", 9, policy, False)
    assert err.code == "LIMIT_OUT_OF_POLICY" and err.details == {
        "field": "max_parallel", "min": 1, "max": 8,
    }  # fmt: skip
    assert check_limit("max_parallel", 9, policy, True) is None
