"""Tests for the diet policy compliance engine and its API."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.engine import evaluate, evaluate_policy
from app.main import app
from app.models import MealPlan, Policy, ValidationRequest
from app.policies import load_policies

SAMPLES = Path(__file__).parent.parent / "samples"
client = TestClient(app)


# --------------------------------------------------------------------------
# Small helpers to build plans and policies
# --------------------------------------------------------------------------
def food(name="food", servings=1, tags=None, **nutrients):
    return {"name": name, "servings": servings, "tags": tags or [], "nutrients": nutrients}


def meal(kind, *items):
    return {"type": kind, "items": list(items)}


def plan(*days):
    """Each argument is a list of meals for one day."""
    return MealPlan.model_validate(
        {"days": [{"day": f"Day {i + 1}", "meals": meals} for i, meals in enumerate(days)]}
    )


def rule(rule_id, rule_type, **kwargs):
    return {"id": rule_id, "type": rule_type, "message": f"{rule_id} message", **kwargs}


def policy(*rules):
    return Policy.model_validate({"id": "test", "name": "Test policy", "rules": list(rules)})


def load_sample(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def ids(report, status=None, severity=None):
    return {
        f.rule_id for f in report.findings
        if (status is None or f.status == status) and (severity is None or f.severity == severity)
    }


# --------------------------------------------------------------------------
# nutrient_limit
# --------------------------------------------------------------------------
def test_value_equal_to_limit_passes():
    p = plan([meal("lunch", food(sodium_mg=2000))])
    pol = policy(rule("R1", "nutrient_limit", scope="day", nutrient="sodium_mg", max_value=2000))
    report = evaluate_policy(p, pol)
    assert report.compliant
    assert report.findings == []
    assert report.checks_passed == 1


def test_servings_multiply_nutrients():
    p = plan([meal("lunch", food(servings=2, sodium_mg=600))])
    pol = policy(rule("R1", "nutrient_limit", scope="day", nutrient="sodium_mg", max_value=1000))
    report = evaluate_policy(p, pol)
    assert not report.compliant
    assert report.findings[0].actual == 1200


def test_missing_data_does_not_hide_a_real_failure():
    p = plan([meal("lunch", food("a", sodium_mg=2500), food("b", calories_kcal=100))])
    pol = policy(rule("R1", "nutrient_limit", scope="day", nutrient="sodium_mg", max_value=2000))
    report = evaluate_policy(p, pol)
    assert report.findings[0].status == "failed"
    assert not report.compliant


def test_missing_data_under_limit_is_incomplete_not_pass():
    p = plan([meal("lunch", food("a", sodium_mg=100), food("b", calories_kcal=100))])
    pol = policy(rule("R1", "nutrient_limit", scope="day", nutrient="sodium_mg", max_value=2000))
    report = evaluate_policy(p, pol)
    assert report.findings[0].status == "incomplete"
    assert report.findings[0].severity == "warning"
    assert report.compliant            # incomplete is a warning only
    assert report.checks_passed == 0
    assert report.warnings == 1


def test_minimum_with_missing_data_is_incomplete():
    p = plan([meal("lunch", food("a", carbs_g=20), food("b", calories_kcal=50))])
    pol = policy(rule("R1", "nutrient_limit", scope="meal", nutrient="carbs_g", min_value=45))
    assert evaluate_policy(p, pol).findings[0].status == "incomplete"


def test_minimum_fails_when_data_is_complete():
    p = plan([meal("lunch", food("a", carbs_g=20))])
    pol = policy(rule("R1", "nutrient_limit", scope="meal", nutrient="carbs_g", min_value=45))
    report = evaluate_policy(p, pol)
    assert report.findings[0].status == "failed"


def test_meal_scope_only_checks_listed_meal_types():
    p = plan([
        meal("breakfast", food(sodium_mg=900)),
        meal("snack", food(sodium_mg=300)),
    ])
    pol = policy(rule("R1", "nutrient_limit", scope="meal", meal_types=["snack"],
                      nutrient="sodium_mg", max_value=200))
    report = evaluate_policy(p, pol)
    assert report.checks_run == 1
    assert report.findings[0].meal == "snack"


def test_repeated_meal_types_get_numbered_labels():
    p = plan([
        meal("snack", food(sodium_mg=50)),
        meal("snack", food(sodium_mg=500)),
    ])
    pol = policy(rule("R1", "nutrient_limit", scope="meal", meal_types=["snack"],
                      nutrient="sodium_mg", max_value=200))
    report = evaluate_policy(p, pol)
    assert [f.meal for f in report.findings] == ["snack 2"]


def test_warning_severity_does_not_break_compliance():
    p = plan([meal("lunch", food(sodium_mg=5000))])
    pol = policy(rule("R1", "nutrient_limit", scope="day", nutrient="sodium_mg",
                      max_value=2000, severity="warning"))
    report = evaluate_policy(p, pol)
    assert report.compliant
    assert report.warnings == 1 and report.violations == 0


# --------------------------------------------------------------------------
# energy_share
# --------------------------------------------------------------------------
def test_energy_share_fails_above_limit_and_passes_below():
    pol = policy(rule("R1", "energy_share", nutrient="sat_fat_g", kcal_per_g=9, max_pct=10))
    too_high = plan([meal("lunch", food(calories_kcal=1000, sat_fat_g=15))])   # 13.5%
    ok = plan([meal("lunch", food(calories_kcal=1000, sat_fat_g=10))])         # 9.0%
    assert not evaluate_policy(too_high, pol).compliant
    assert evaluate_policy(ok, pol).compliant


def test_energy_share_without_calorie_data_is_incomplete():
    pol = policy(rule("R1", "energy_share", nutrient="sat_fat_g", kcal_per_g=9, max_pct=10))
    p = plan([meal("lunch", food(sat_fat_g=15))])
    assert evaluate_policy(p, pol).findings[0].status == "incomplete"


# --------------------------------------------------------------------------
# forbidden_tags, tag_servings, required_meals, consistency
# --------------------------------------------------------------------------
def test_forbidden_tags_report_the_item():
    p = plan([meal("lunch", food("Tuyo", tags=["Salted_Fish"]), food("Rice"))])
    pol = policy(rule("R1", "forbidden_tags", tags=["salted_fish"]))
    report = evaluate_policy(p, pol)
    assert report.checks_run == 2
    assert [f.item for f in report.findings] == ["Tuyo"]


def test_tag_servings_minimum():
    pol = policy(rule("R1", "tag_servings", tags=["fruit", "vegetable"], min_value=5))
    low = plan([meal("lunch", food(servings=2, tags=["vegetable"]), food(servings=1, tags=["fruit"]))])
    enough = plan([meal("lunch", food(servings=3, tags=["vegetable"]), food(servings=2, tags=["fruit"]))])
    assert not evaluate_policy(low, pol).compliant
    assert evaluate_policy(enough, pol).compliant


def test_required_meals():
    pol = policy(rule("R1", "required_meals", meal_types=["breakfast", "lunch", "dinner"]))
    p = plan([meal("breakfast", food()), meal("lunch", food())])
    report = evaluate_policy(p, pol)
    assert "dinner" in report.findings[0].detail


def test_consistency_flags_big_difference():
    pol = policy(rule("R1", "consistency", nutrient="carbs_g", max_difference=15))
    uneven = plan([meal("breakfast", food(carbs_g=30)), meal("lunch", food(carbs_g=80))])
    even = plan([meal("breakfast", food(carbs_g=50)), meal("lunch", food(carbs_g=60))])
    assert not evaluate_policy(uneven, pol).compliant
    assert evaluate_policy(even, pol).compliant


def test_consistency_skips_days_with_fewer_than_two_main_meals():
    pol = policy(rule("R1", "consistency", nutrient="carbs_g", max_difference=15))
    p = plan([meal("breakfast", food(carbs_g=50)), meal("snack", food(carbs_g=5))])
    assert evaluate_policy(p, pol).checks_run == 0


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
def test_rule_missing_required_fields_is_rejected():
    with pytest.raises(ValidationError):
        policy(rule("R1", "nutrient_limit", nutrient="sodium_mg"))      # no min/max
    with pytest.raises(ValidationError):
        policy(rule("R2", "forbidden_tags"))                            # no tags


def test_duplicate_rule_ids_are_rejected():
    with pytest.raises(ValidationError):
        policy(rule("R1", "required_meals", meal_types=["lunch"]),
               rule("R1", "required_meals", meal_types=["dinner"]))


def test_bad_plan_input_is_rejected():
    with pytest.raises(ValidationError):
        MealPlan.model_validate({"days": [{"day": "D", "meals": [meal("brunch", food())]}]})
    with pytest.raises(ValidationError):
        MealPlan.model_validate({"days": [
            {"day": "D", "meals": [meal("lunch", food())]},
            {"day": "D", "meals": [meal("lunch", food())]},
        ]})
    with pytest.raises(ValidationError):
        MealPlan.model_validate({"days": [{"day": "D", "meals": [meal("lunch", food(sodium_mg=-5))]}]})


# --------------------------------------------------------------------------
# Built-in policies and sample plans
# --------------------------------------------------------------------------
def test_builtin_policies_load():
    policies = load_policies()
    assert set(policies) == {"low_sodium", "diabetic_consistent_carb", "heart_healthy"}
    assert all(len(p.rules) > 0 for p in policies.values())


def test_sample_plan_passes_all_policies_cleanly():
    request = ValidationRequest.model_validate(load_sample("all_policies_pass.json"))
    policies = [load_policies()[pid] for pid in request.policy_ids]
    result = evaluate(request.plan, policies)
    assert result.compliant
    for report in result.reports:
        assert report.violations == 0
        assert report.warnings == 0, [f.rule_id for f in report.findings]
        assert report.compliance_rate == 100.0


def test_low_sodium_fail_sample():
    request = ValidationRequest.model_validate(load_sample("low_sodium_fail.json"))
    report = evaluate_policy(request.plan, load_policies()["low_sodium"])
    assert not report.compliant
    assert ids(report, "failed", "violation") == {
        "LS-DAY-SODIUM", "LS-MEAL-SODIUM", "LS-FORBID-HIGH-SODIUM-FOODS"}
    assert {"LS-SNACK-SODIUM", "LS-FORBID-CONDIMENTS", "LS-REQUIRED-MEALS"} <= ids(
        report, "failed", "warning")
    # Day 1 dinner has a food with no sodium data, so it cannot be confirmed
    assert any(f.rule_id == "LS-MEAL-SODIUM" and f.status == "incomplete"
               and f.day == "Day 1" and f.meal == "dinner" for f in report.findings)


def test_diabetic_fail_sample():
    request = ValidationRequest.model_validate(load_sample("diabetic_fail.json"))
    report = evaluate_policy(request.plan, load_policies()["diabetic_consistent_carb"])
    assert not report.compliant
    assert ids(report, "failed", "violation") == {
        "DM-CARB-MEAL-MAX", "DM-CARB-SNACK-MAX", "DM-ADDED-SUGAR-SHARE", "DM-SUGARY-DRINK"}
    assert {"DM-CARB-MEAL-MIN", "DM-CARB-CONSISTENCY", "DM-FIBER-MIN"} <= ids(
        report, "failed", "warning")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def test_api_health_and_policy_endpoints():
    assert client.get("/health").json() == {"status": "ok", "policies_loaded": 3}
    listing = client.get("/policies").json()
    assert {p["id"] for p in listing} == {"low_sodium", "diabetic_consistent_carb", "heart_healthy"}
    assert client.get("/policies/low_sodium").json()["rules"][0]["id"] == "LS-REQUIRED-MEALS"
    assert client.get("/policies/nope").status_code == 404


def test_api_validate_passing_plan():
    response = client.post("/validate", json=load_sample("all_policies_pass.json"))
    assert response.status_code == 200
    body = response.json()
    assert body["compliant"] is True
    assert len(body["reports"]) == 3
    assert body["daily_totals"][0]["totals"]["sodium_mg"] > 0


def test_api_validate_failing_plan():
    response = client.post("/validate", json=load_sample("low_sodium_fail.json"))
    assert response.status_code == 200
    body = response.json()
    assert body["compliant"] is False
    assert body["reports"][0]["violations"] > 0


def test_api_unknown_policy_returns_404():
    request = load_sample("all_policies_pass.json")
    request["policy_ids"] = ["does_not_exist"]
    assert client.post("/validate", json=request).status_code == 404


def test_api_requires_a_policy():
    request = load_sample("all_policies_pass.json")
    request["policy_ids"] = []
    assert client.post("/validate", json=request).status_code == 422


def test_api_rejects_negative_nutrients():
    request = load_sample("all_policies_pass.json")
    request["plan"]["days"][0]["meals"][0]["items"][0]["nutrients"]["sodium_mg"] = -1
    assert client.post("/validate", json=request).status_code == 422


def test_api_accepts_custom_policy_in_request():
    body = {
        "plan": {"days": [{"day": "Day 1", "meals": [meal("lunch", food(sodium_mg=900))]}]},
        "custom_policies": [{
            "id": "ward_7_rule", "name": "Ward 7 rule",
            "rules": [rule("W7-MEAL", "nutrient_limit", scope="meal", nutrient="sodium_mg", max_value=500)],
        }],
    }
    response = client.post("/validate", json=body)
    assert response.status_code == 200
    assert response.json()["compliant"] is False
