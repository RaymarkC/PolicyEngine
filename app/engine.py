"""Rule engine: checks a meal plan against one or more diet policies.

How results work
----------------
Every rule creates one or more "checks" (for example, one per day or one per
meal). Each check ends in one of three ways:

  pass        the plan follows the rule
  failed      the plan breaks the rule (severity "violation" or "warning")
  incomplete  some foods have no value for the nutrient, so the rule cannot be
              confirmed (always reported as a warning)

A plan is compliant with a policy when it has zero failed "violation" findings.
Warnings and incomplete checks do not block compliance but are always shown.

Missing nutrient data is never treated as zero. If the foods we DO have data for
already break a maximum, that is still a real failure. If the known total is
under the maximum but data is missing, we cannot be sure, so it is "incomplete".
"""
from typing import Dict, List, Optional, Tuple

from .models import (
    MAIN_MEALS,
    DailyTotals,
    DayPlan,
    Finding,
    FoodItem,
    Meal,
    MealPlan,
    Policy,
    PolicyReport,
    Rule,
    ValidationResponse,
)

EPS = 1e-9


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _all_items(day: DayPlan) -> List[FoodItem]:
    return [item for meal in day.meals for item in meal.items]


def _labeled_meals(day: DayPlan) -> List[Tuple[str, Meal]]:
    """Give each meal a readable label ('dinner', or 'snack 1' / 'snack 2')."""
    totals: Dict[str, int] = {}
    for meal in day.meals:
        totals[meal.type] = totals.get(meal.type, 0) + 1
    seen: Dict[str, int] = {}
    labeled = []
    for meal in day.meals:
        seen[meal.type] = seen.get(meal.type, 0) + 1
        label = meal.type if totals[meal.type] == 1 else f"{meal.type} {seen[meal.type]}"
        labeled.append((label, meal))
    return labeled


def _sum(items: List[FoodItem], nutrient: str) -> Tuple[float, List[str]]:
    """Total of a nutrient (per-serving value x servings) and foods missing it."""
    total = 0.0
    missing: List[str] = []
    for item in items:
        if nutrient in item.nutrients:
            total += item.nutrients[nutrient] * item.servings
        else:
            missing.append(item.name)
    return total, missing


def _range_text(low: Optional[float], high: Optional[float]) -> str:
    if low is not None and high is not None:
        return f"{low:g} to {high:g}"
    if high is not None:
        return f"at most {high:g}"
    return f"at least {low:g}"


def _targets(rule: Rule, day: DayPlan) -> List[Tuple[Optional[str], List[FoodItem]]]:
    """The groups of foods a nutrient rule looks at: the whole day, or each meal."""
    if rule.scope == "day":
        return [(None, _all_items(day))]
    groups = []
    for label, meal in _labeled_meals(day):
        if not rule.meal_types or meal.type in rule.meal_types:
            groups.append((label, meal.items))
    return groups


class _Collector:
    """Counts checks and records findings for one rule."""

    def __init__(self, policy_id: str, rule: Rule):
        self.policy_id = policy_id
        self.rule = rule
        self.checks = 0
        self.passed = 0
        self.findings: List[Finding] = []

    def ok(self) -> None:
        self.checks += 1
        self.passed += 1

    def fail(self, day: str, meal: Optional[str] = None, item: Optional[str] = None,
             actual: Optional[float] = None, expected: Optional[str] = None,
             detail: str = "") -> None:
        self.checks += 1
        self.findings.append(Finding(
            policy_id=self.policy_id, rule_id=self.rule.id, status="failed",
            severity=self.rule.severity, day=day, meal=meal, item=item,
            message=self.rule.message, detail=detail, actual=actual,
            expected=expected, source=self.rule.source,
        ))

    def incomplete(self, day: str, meal: Optional[str] = None, detail: str = "") -> None:
        self.checks += 1
        self.findings.append(Finding(
            policy_id=self.policy_id, rule_id=self.rule.id, status="incomplete",
            severity="warning",
            message=f"Could not fully check this rule: {self.rule.message}",
            day=day, meal=meal, detail=detail, source=self.rule.source,
        ))


# --------------------------------------------------------------------------
# One check function per rule type
# --------------------------------------------------------------------------
def _check_nutrient_limit(c: _Collector, rule: Rule, plan: MealPlan) -> None:
    expected = _range_text(rule.min_value, rule.max_value)
    for day in plan.days:
        for label, items in _targets(rule, day):
            total, missing = _sum(items, rule.nutrient)
            over = rule.max_value is not None and total > rule.max_value + EPS
            under = rule.min_value is not None and total < rule.min_value - EPS
            if over or (under and not missing):
                c.fail(day.day, label, actual=round(total, 1), expected=expected,
                       detail=f"{rule.nutrient} = {total:.1f}; allowed {expected}")
            elif missing:
                c.incomplete(day.day, label,
                             detail=f"No {rule.nutrient} data for: {', '.join(missing)}")
            else:
                c.ok()


def _check_energy_share(c: _Collector, rule: Rule, plan: MealPlan) -> None:
    expected = f"at most {rule.max_pct:g}% of energy"
    for day in plan.days:
        items = _all_items(day)
        energy, missing_energy = _sum(items, rule.energy_nutrient)
        amount, missing_amount = _sum(items, rule.nutrient)
        if energy <= 0 or missing_energy:
            c.incomplete(day.day, detail=f"Missing {rule.energy_nutrient} data, "
                                         f"so the energy share of {rule.nutrient} is unknown")
            continue
        share = amount * rule.kcal_per_g / energy * 100
        if share > rule.max_pct + EPS:
            c.fail(day.day, actual=round(share, 1), expected=expected,
                   detail=f"{rule.nutrient} gives {share:.1f}% of energy; allowed {expected}")
        elif missing_amount:
            c.incomplete(day.day,
                         detail=f"No {rule.nutrient} data for: {', '.join(missing_amount)}")
        else:
            c.ok()


def _check_forbidden_tags(c: _Collector, rule: Rule, plan: MealPlan) -> None:
    banned = set(rule.tags)
    for day in plan.days:
        for label, meal in _labeled_meals(day):
            if rule.meal_types and meal.type not in rule.meal_types:
                continue
            for item in meal.items:
                hit = sorted(banned & set(item.tags))
                if hit:
                    c.fail(day.day, label, item=item.name,
                           detail=f"'{item.name}' is tagged: {', '.join(hit)}")
                else:
                    c.ok()


def _check_tag_servings(c: _Collector, rule: Rule, plan: MealPlan) -> None:
    wanted = set(rule.tags)
    expected = _range_text(rule.min_value, rule.max_value) + " servings"
    for day in plan.days:
        total = sum(i.servings for i in _all_items(day) if wanted & set(i.tags))
        too_low = rule.min_value is not None and total < rule.min_value - EPS
        too_high = rule.max_value is not None and total > rule.max_value + EPS
        if too_low or too_high:
            c.fail(day.day, actual=round(total, 1), expected=expected,
                   detail=f"{total:g} servings of {'/'.join(rule.tags)}; allowed {expected}")
        else:
            c.ok()


def _check_consistency(c: _Collector, rule: Rule, plan: MealPlan) -> None:
    meal_types = rule.meal_types or MAIN_MEALS
    expected = f"at most {rule.max_difference:g} between meals"
    for day in plan.days:
        totals: List[float] = []
        missing_all: List[str] = []
        for _, meal in _labeled_meals(day):
            if meal.type in meal_types:
                total, missing = _sum(meal.items, rule.nutrient)
                totals.append(total)
                missing_all.extend(missing)
        if len(totals) < 2:
            continue  # nothing to compare; 'required_meals' covers missing meals
        spread = max(totals) - min(totals)
        if missing_all:
            c.incomplete(day.day,
                         detail=f"No {rule.nutrient} data for: {', '.join(missing_all)}")
        elif spread > rule.max_difference + EPS:
            c.fail(day.day, actual=round(spread, 1), expected=expected,
                   detail=f"{rule.nutrient} differs by {spread:.1f} between meals "
                          f"({', '.join(f'{t:.1f}' for t in totals)})")
        else:
            c.ok()


def _check_required_meals(c: _Collector, rule: Rule, plan: MealPlan) -> None:
    for day in plan.days:
        present = {m.type for m in day.meals}
        absent = [t for t in rule.meal_types if t not in present]
        if absent:
            c.fail(day.day, detail=f"Missing: {', '.join(absent)}",
                   expected=f"has {', '.join(rule.meal_types)}")
        else:
            c.ok()


CHECKS = {
    "nutrient_limit": _check_nutrient_limit,
    "energy_share": _check_energy_share,
    "forbidden_tags": _check_forbidden_tags,
    "tag_servings": _check_tag_servings,
    "consistency": _check_consistency,
    "required_meals": _check_required_meals,
}


# --------------------------------------------------------------------------
# Public functions
# --------------------------------------------------------------------------
def evaluate_policy(plan: MealPlan, policy: Policy) -> PolicyReport:
    findings: List[Finding] = []
    checks = 0
    passed = 0
    for rule in policy.rules:
        collector = _Collector(policy.id, rule)
        CHECKS[rule.type](collector, rule, plan)
        checks += collector.checks
        passed += collector.passed
        findings.extend(collector.findings)

    violations = sum(1 for f in findings if f.status == "failed" and f.severity == "violation")
    return PolicyReport(
        policy_id=policy.id,
        policy_name=policy.name,
        policy_version=policy.version,
        compliant=violations == 0,
        checks_run=checks,
        checks_passed=passed,
        compliance_rate=round(passed / checks * 100, 1) if checks else 100.0,
        violations=violations,
        warnings=len(findings) - violations,
        findings=findings,
    )


def daily_totals(plan: MealPlan) -> List[DailyTotals]:
    """Nutrient totals per day. Foods missing a nutrient add nothing to it."""
    result = []
    for day in plan.days:
        totals: Dict[str, float] = {}
        for item in _all_items(day):
            for name, value in item.nutrients.items():
                totals[name] = totals.get(name, 0.0) + value * item.servings
        result.append(DailyTotals(
            day=day.day,
            totals={k: round(v, 1) for k, v in sorted(totals.items())},
        ))
    return result


def evaluate(plan: MealPlan, policies: List[Policy]) -> ValidationResponse:
    reports = [evaluate_policy(plan, policy) for policy in policies]
    return ValidationResponse(
        plan_id=plan.plan_id,
        compliant=all(r.compliant for r in reports),
        reports=reports,
        daily_totals=daily_totals(plan),
    )
