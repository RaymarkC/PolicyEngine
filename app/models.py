"""Data models for meal plans, diet policies, and validation reports."""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MAIN_MEALS = ["breakfast", "lunch", "dinner"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


# --------------------------------------------------------------------------
# Meal plan (the input that gets checked)
# --------------------------------------------------------------------------
class FoodItem(BaseModel):
    """One food in a meal. Nutrient values are PER SERVING."""

    name: str = Field(min_length=1)
    servings: float = Field(default=1.0, gt=0)
    tags: List[str] = Field(default_factory=list)
    nutrients: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-serving values, e.g. calories_kcal, sodium_mg, carbs_g.",
    )

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: List[str]) -> List[str]:
        return [t.strip().lower() for t in value if t.strip()]

    @field_validator("nutrients")
    @classmethod
    def _no_negative_nutrients(cls, value: Dict[str, float]) -> Dict[str, float]:
        for key, amount in value.items():
            if amount < 0:
                raise ValueError(f"nutrient '{key}' cannot be negative")
        return value


class Meal(BaseModel):
    type: Literal["breakfast", "lunch", "dinner", "snack"]
    items: List[FoodItem] = Field(min_length=1)

    @field_validator("type", mode="before")
    @classmethod
    def _lower_type(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class DayPlan(BaseModel):
    day: str = Field(min_length=1)
    meals: List[Meal] = Field(min_length=1)


class MealPlan(BaseModel):
    plan_id: Optional[str] = None
    description: Optional[str] = None
    days: List[DayPlan] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_days(self):
        names = [d.day for d in self.days]
        if len(names) != len(set(names)):
            raise ValueError("each day must have a unique 'day' label")
        return self


# --------------------------------------------------------------------------
# Policy (the rules)
# --------------------------------------------------------------------------
RuleType = Literal[
    "nutrient_limit",   # min and/or max of a nutrient, per meal or per day
    "energy_share",     # nutrient energy as a % of total calories (per day)
    "forbidden_tags",   # foods carrying certain tags are not allowed
    "tag_servings",     # servings of tagged foods per day (e.g. fruit + veg)
    "consistency",      # nutrient difference between meals in a day
    "required_meals",   # meal types that must appear each day
]


class Rule(BaseModel):
    id: str = Field(min_length=1)
    type: RuleType
    scope: Literal["meal", "day"] = "day"
    nutrient: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    energy_nutrient: str = "calories_kcal"
    kcal_per_g: Optional[float] = None
    max_pct: Optional[float] = None
    max_difference: Optional[float] = None
    tags: List[str] = Field(default_factory=list)
    meal_types: List[str] = Field(default_factory=list)
    severity: Literal["violation", "warning"] = "violation"
    message: str = Field(min_length=1)
    source: Optional[str] = None

    @field_validator("tags", "meal_types")
    @classmethod
    def _lower_lists(cls, value: List[str]) -> List[str]:
        return [v.strip().lower() for v in value if v.strip()]

    @model_validator(mode="after")
    def _check_required_fields(self):
        t = self.type
        problem = None
        if t == "nutrient_limit":
            if not self.nutrient:
                problem = "needs 'nutrient'"
            elif self.min_value is None and self.max_value is None:
                problem = "needs 'min_value' and/or 'max_value'"
        elif t == "energy_share":
            if not (self.nutrient and self.kcal_per_g and self.max_pct is not None):
                problem = "needs 'nutrient', 'kcal_per_g' and 'max_pct'"
        elif t == "forbidden_tags":
            if not self.tags:
                problem = "needs 'tags'"
        elif t == "tag_servings":
            if not self.tags or (self.min_value is None and self.max_value is None):
                problem = "needs 'tags' and 'min_value' and/or 'max_value'"
        elif t == "consistency":
            if not (self.nutrient and self.max_difference is not None):
                problem = "needs 'nutrient' and 'max_difference'"
        elif t == "required_meals":
            if not self.meal_types:
                problem = "needs 'meal_types'"
        if problem:
            raise ValueError(f"rule '{self.id}' of type '{t}' {problem}")
        return self


class Policy(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    version: str = "1.0"
    rules: List[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_rule_ids(self):
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"policy '{self.id}' has duplicate rule ids")
        return self


class PolicySummary(BaseModel):
    id: str
    name: str
    description: str
    version: str
    rule_count: int


# --------------------------------------------------------------------------
# Request / response
# --------------------------------------------------------------------------
class ValidationRequest(BaseModel):
    plan: MealPlan
    policy_ids: List[str] = Field(default_factory=list)
    custom_policies: List[Policy] = Field(default_factory=list)

    @model_validator(mode="after")
    def _need_a_policy(self):
        if not self.policy_ids and not self.custom_policies:
            raise ValueError("give at least one policy_id or custom_policy")
        return self


class Finding(BaseModel):
    policy_id: str
    rule_id: str
    status: Literal["failed", "incomplete"]
    severity: Literal["violation", "warning"]
    day: str
    meal: Optional[str] = None
    item: Optional[str] = None
    message: str
    detail: str = ""
    actual: Optional[float] = None
    expected: Optional[str] = None
    source: Optional[str] = None


class PolicyReport(BaseModel):
    policy_id: str
    policy_name: str
    policy_version: str
    compliant: bool
    checks_run: int
    checks_passed: int
    compliance_rate: float
    violations: int
    warnings: int
    findings: List[Finding]


class DailyTotals(BaseModel):
    day: str
    totals: Dict[str, float]


class ValidationResponse(BaseModel):
    plan_id: Optional[str]
    compliant: bool
    reports: List[PolicyReport]
    daily_totals: List[DailyTotals]
