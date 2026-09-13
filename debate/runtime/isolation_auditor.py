from __future__ import annotations

import json
from typing import Any

A_CANARY = "A-PRIVATE-CANARY-7f31c9"
B_CANARY = "B-PRIVATE-CANARY-52bd04"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def audit_agent_context(role: str, context: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    blob = _json_text(context)
    if role == "A":
        if B_CANARY in blob:
            findings.append("A_CONTEXT contains B_PRIVATE canary")
    else:
        if A_CANARY in blob:
            findings.append("B_CONTEXT contains A_PRIVATE canary")
    return findings


def audit_cross_output(role: str, speech: str) -> list[str]:
    findings: list[str] = []
    if role == "A" and B_CANARY in speech:
        findings.append("A_OUTPUT leaked B_PRIVATE canary")
    if role == "B" and A_CANARY in speech:
        findings.append("B_OUTPUT leaked A_PRIVATE canary")
    return findings


def audit_public_state(state: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    blob = _json_text(state)
    if A_CANARY in blob:
        findings.append("PUBLIC_STATE contains A_PRIVATE canary")
    if B_CANARY in blob:
        findings.append("PUBLIC_STATE contains B_PRIVATE canary")
    return findings
