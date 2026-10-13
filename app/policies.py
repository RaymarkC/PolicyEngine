"""Loads policy files (JSON) from app/policy_files/."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from .models import Policy

POLICY_DIR = Path(__file__).parent / "policy_files"


class UnknownPolicyError(Exception):
    """Raised when a requested policy id does not exist."""


@lru_cache(maxsize=1)
def load_policies() -> Dict[str, Policy]:
    policies: Dict[str, Policy] = {}
    for path in sorted(POLICY_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            policy = Policy.model_validate(json.load(f))
        if policy.id in policies:
            raise ValueError(f"duplicate policy id '{policy.id}' in {path.name}")
        policies[policy.id] = policy
    return policies


def get_policy(policy_id: str):
    return load_policies().get(policy_id)


def resolve_policies(policy_ids: List[str], custom_policies: List[Policy]) -> List[Policy]:
    """Turn requested ids (plus any inline policies) into Policy objects."""
    selected: List[Policy] = []
    for policy_id in dict.fromkeys(policy_ids):  # remove duplicates, keep order
        policy = get_policy(policy_id)
        if policy is None:
            raise UnknownPolicyError(
                f"Unknown policy '{policy_id}'. Available: {', '.join(load_policies())}"
            )
        selected.append(policy)
    selected.extend(custom_policies)
    return selected
