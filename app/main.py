"""FastAPI backend for the diet policy compliance engine."""
from typing import List

from fastapi import FastAPI, HTTPException

from .engine import evaluate
from .models import Policy, PolicySummary, ValidationRequest, ValidationResponse
from .policies import UnknownPolicyError, get_policy, load_policies, resolve_policies

app = FastAPI(
    title="Diet Policy Compliance Engine",
    version="1.0.0",
    description=(
        "Checks custom meal plans against rule-based diet policies "
        "(low-sodium, diabetic consistent-carbohydrate, heart-healthy). "
        "Example limits are for learning and must be reviewed by a dietitian "
        "before any real clinical use."
    ),
)

load_policies()  # fail fast at startup if a policy file is broken


@app.get("/health")
def health():
    return {"status": "ok", "policies_loaded": len(load_policies())}


@app.get("/policies", response_model=List[PolicySummary])
def list_policies():
    return [
        PolicySummary(id=p.id, name=p.name, description=p.description,
                      version=p.version, rule_count=len(p.rules))
        for p in load_policies().values()
    ]


@app.get("/policies/{policy_id}", response_model=Policy)
def read_policy(policy_id: str):
    policy = get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Unknown policy '{policy_id}'.")
    return policy


@app.post("/validate", response_model=ValidationResponse)
def validate(request: ValidationRequest):
    try:
        policies = resolve_policies(request.policy_ids, request.custom_policies)
    except UnknownPolicyError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return evaluate(request.plan, policies)
