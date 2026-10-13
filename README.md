# Diet Policy Compliance Engine

**Lab 1: Evidence-Based Policy Rule Engine** (Public Policy & Program Outcomes)

A backend (Python + FastAPI) that checks a custom meal plan against diet policies such as a low-sodium or diabetic hospital diet. The rules are written as data (JSON files), not buried in code, so a policy can be changed without touching the engine.

> **Note:** The limits in this project are example values taken from public guidelines (WHO, ADA) and common hospital practice. They are for learning. A registered dietitian must review them before any real clinical use.

---

## What it does

1. Policies are stored as JSON files in `app/policy_files/`. Each policy is a list of rules.
2. You send a meal plan (days → meals → foods with nutrient values).
3. The engine checks every rule and returns:
   - whether the plan is **compliant**
   - each **finding** (which rule, which day/meal/food, the actual value, the allowed value, the source)
   - a **compliance rate** and daily nutrient totals

### Built-in policies

| Policy id | What it checks |
|---|---|
| `low_sodium` | Sodium per day (2,000 mg) and per meal, salty foods, salty condiments |
| `diabetic_consistent_carb` | Carbs per meal and snack, similar carbs across meals, added sugar, sugary drinks, fiber, sodium, saturated fat |
| `heart_healthy` | Sodium, saturated fat, trans fat, fruit and vegetable servings, fried and cured foods |

You can check one plan against several policies at once.

### Results: violation, warning, incomplete

- **Violation**: breaks a rule. The plan is **not compliant**.
- **Warning**: breaks a softer rule. The plan is still compliant but you see the warning.
- **Incomplete**: a food has no value for the nutrient, so the rule cannot be confirmed. Shown as a warning.

Missing data is never treated as zero. If the foods that do have data already break a maximum, it is still a violation.

---

## Quick start

```bash
pip install -r requirements.txt

# Run the tests
python -m pytest

# Try it in the terminal (no server needed)
python -m app.cli samples/low_sodium_fail.json
python -m app.cli samples/all_policies_pass.json

# Start the API server
uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000/docs** for the interactive Swagger page where you can try every endpoint.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Check the server is running |
| GET | `/policies` | List available policies |
| GET | `/policies/{id}` | Show all rules in one policy |
| POST | `/validate` | Check a meal plan against policies |

### Example request

```bash
curl -X POST http://127.0.0.1:8000/validate \
  -H "Content-Type: application/json" \
  -d @samples/low_sodium_fail.json
```

Request body:

```json
{
  "plan": {
    "plan_id": "WARD-3-MON",
    "days": [
      {
        "day": "Day 1",
        "meals": [
          {
            "type": "lunch",
            "items": [
              {
                "name": "Grilled chicken breast (100 g)",
                "servings": 1,
                "tags": [],
                "nutrients": { "calories_kcal": 165, "sodium_mg": 74, "carbs_g": 0 }
              }
            ]
          }
        ]
      }
    ]
  },
  "policy_ids": ["low_sodium"]
}
```

Notes:
- `type` must be `breakfast`, `lunch`, `dinner`, or `snack`.
- Nutrient values are **per serving**. The engine multiplies by `servings`.
- `tags` are labels such as `fruit`, `vegetable`, `salted_fish`, `sugary_drink`. Some rules use them.
- You can also send your own rules inline with `custom_policies` (same format as the policy files).

Error codes: `404` unknown policy id, `422` bad input (for example a negative nutrient, an unknown meal type, or no policy given).

### Nutrient names used by the built-in policies

`calories_kcal`, `sodium_mg`, `carbs_g`, `fiber_g`, `added_sugar_g`, `sat_fat_g`, `trans_fat_g`

---

## Rule types

| Type | What it checks | Main fields |
|---|---|---|
| `nutrient_limit` | Minimum and/or maximum of a nutrient, per day or per meal | `scope`, `nutrient`, `min_value`, `max_value`, `meal_types` |
| `energy_share` | A nutrient's calories as a % of the day's calories | `nutrient`, `kcal_per_g`, `max_pct` |
| `forbidden_tags` | Foods with certain tags are not allowed | `tags`, `meal_types` |
| `tag_servings` | Servings of tagged foods per day | `tags`, `min_value`, `max_value` |
| `consistency` | Nutrient difference between main meals in a day | `nutrient`, `max_difference`, `meal_types` |
| `required_meals` | Meal types that must appear every day | `meal_types` |

Every rule also has `id`, `message`, `severity` (`violation` or `warning`), and an optional `source`.

### Add your own rule

Open a file in `app/policy_files/` and add an object to `rules`. Example: limit potassium to 2,000 mg per day for a kidney diet:

```json
{
  "id": "KD-POTASSIUM-DAY",
  "type": "nutrient_limit",
  "scope": "day",
  "nutrient": "potassium_mg",
  "max_value": 2000,
  "severity": "violation",
  "message": "Potassium for the day must not go over 2,000 mg.",
  "source": "Your reference here"
}
```

To add a whole new policy, create a new `.json` file in the same folder with `id`, `name`, and `rules`. It is loaded automatically. A broken policy file stops the server at startup with a clear error.

---

## Project structure

```
diet_policy_engine/
├── app/
│   ├── main.py            FastAPI endpoints
│   ├── engine.py          Rule engine (one check function per rule type)
│   ├── models.py          Meal plan, policy, and report models + input validation
│   ├── policies.py        Loads policy JSON files
│   ├── cli.py             Run from the terminal
│   └── policy_files/      The policies (JSON)
│       ├── low_sodium.json
│       ├── diabetic_consistent_carb.json
│       └── heart_healthy.json
├── samples/               Ready-to-use request files
│   ├── all_policies_pass.json
│   ├── low_sodium_fail.json
│   └── diabetic_fail.json
├── tests/test_engine.py   30 tests (engine, policies, API)
├── requirements.txt
└── pytest.ini
```

---

## Limits of this project

- Nutrient values come from the plan you send. The engine does not look up foods in a food database.
- Daily totals leave out foods that have no value for a nutrient.
- The example limits are not personal. Real diets depend on the patient's age, weight, medicines, and doctor's orders.

## Sources for the example limits

- WHO, *Guideline: Sodium intake for adults and children* (2012)
- WHO, *Guideline: Sugars intake for adults and children* (2015)
- WHO, *Saturated fatty acid and trans-fatty acid intake for adults and children* (2023)
- WHO, fruit and vegetable intake of at least 400 g per day
- American Diabetes Association, *Standards of Care* (sodium under 2,300 mg per day)
- Per-meal carbohydrate, snack, fiber, and food-list values are institutional examples and should be set by the hospital dietitian
