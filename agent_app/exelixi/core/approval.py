from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import uuid4


VALID_APPROVAL_MODES = {"inline", "auto", "deny"}


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    command: str
    risk_reason: str
    tool_name: str = "BashTool"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str = ""


@dataclass(frozen=True)
class UserInputRequest:
    id: str
    question: str
    context: str = ""
    default: str = ""
    options: list[str] = field(default_factory=list)
    tool_name: str = "AskUserTool"


@dataclass(frozen=True)
class UserInputResponse:
    answer: str
    canceled: bool = False


RISK_PATTERNS = [
    (r"(?:^|&&|\|\||;)\s*(?:python\s+-m\s+)?pip\s+install\b", "Python package installation"),
    (r"(?:^|&&|\|\||;)\s*uv\s+add\b", "Project dependency change with uv add"),
    (r"(?:^|&&|\|\||;)\s*uv\s+sync\b", "Dependency synchronization with uv sync"),
    (r"(?:^|&&|\|\||;)\s*uv\s+pip\s+install\b", "Python package installation with uv pip"),
    (r"(?:^|&&|\|\||;)\s*npm\s+install\b", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*pnpm\s+install\b", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*yarn\s+(?:install\b|add\b)", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*(?:curl|wget)\b", "Network download command"),
    (r"(?:^|&&|\|\||;)\s*uvicorn\b", "Long-running development server"),
    (r"(?:^|&&|\|\||;)\s*python\s+-m\s+http\.server\b", "Long-running development server"),
]


def normalize_approval_mode(mode: str | None) -> str:
    normalized = (mode or "inline").strip().lower()
    return normalized if normalized in VALID_APPROVAL_MODES else "inline"


def classify_command_risk(command: str) -> str | None:
    for pattern, reason in RISK_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            return reason
    return None


def make_approval_request(command: str, risk_reason: str, *, tool_name: str = "BashTool") -> ApprovalRequest:
    return ApprovalRequest(id=f"approval-{uuid4().hex[:8]}", command=command, risk_reason=risk_reason, tool_name=tool_name)


def make_user_input_request(
    question: str,
    *,
    context: str = "",
    default: str = "",
    options: list[str] | None = None,
    tool_name: str = "AskUserTool",
) -> UserInputRequest:
    return UserInputRequest(
        id=f"input-{uuid4().hex[:8]}",
        question=question,
        context=context,
        default=default,
        options=list(options or []),
        tool_name=tool_name,
    )
