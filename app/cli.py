"""Run the engine from the terminal (no server needed).

Usage:
    python -m app.cli samples/low_sodium_fail.json

Exit code: 0 = compliant, 1 = not compliant, 2 = bad input.
"""
import json
import sys

from pydantic import ValidationError

from .engine import evaluate
from .models import ValidationRequest, ValidationResponse
from .policies import UnknownPolicyError, resolve_policies


def format_report(result: ValidationResponse) -> str:
    lines = [f"Plan: {result.plan_id or '(no id)'}",
             f"Overall: {'COMPLIANT' if result.compliant else 'NOT COMPLIANT'}", ""]
    for report in result.reports:
        status = "PASS" if report.compliant else "FAIL"
        lines.append(f"[{status}] {report.policy_name} - "
                     f"{report.checks_passed}/{report.checks_run} checks passed "
                     f"({report.compliance_rate}%), "
                     f"{report.violations} violation(s), {report.warnings} warning(s)")
        for f in report.findings:
            where = f.day + (f" / {f.meal}" if f.meal else "")
            what = "VIOLATION" if f.status == "failed" and f.severity == "violation" else (
                "INCOMPLETE" if f.status == "incomplete" else "WARNING")
            lines.append(f"   {what:<10} {f.rule_id} ({where})")
            lines.append(f"              {f.message}")
            if f.detail:
                lines.append(f"              -> {f.detail}")
        lines.append("")
    return "\n".join(lines)


def run(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as handle:
            request = ValidationRequest.model_validate(json.load(handle))
        policies = resolve_policies(request.policy_ids, request.custom_policies)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read '{path}': {error}")
        return 2
    except ValidationError as error:
        print(f"Invalid request file:\n{error}")
        return 2
    except UnknownPolicyError as error:
        print(error)
        return 2

    result = evaluate(request.plan, policies)
    print(format_report(result))
    return 0 if result.compliant else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.cli <request.json>")
        sys.exit(2)
    sys.exit(run(sys.argv[1]))
