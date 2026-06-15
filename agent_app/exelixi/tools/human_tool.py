from __future__ import annotations

from typing import Any

from exelixi.core.approval import (
    ApprovalDecision,
    UserInputResponse,
    make_approval_request,
    make_user_input_request,
)
from exelixi.core.state import RuntimeState


def ask_user(
    state: RuntimeState,
    question: str,
    context: str = "",
    default: str = "",
) -> dict[str, Any]:
    question = str(question or "").strip()
    if not question:
        return {"ok": False, "error": "question must not be empty"}
    request = make_user_input_request(question, context=str(context or ""), default=str(default or ""))
    base = {
        "requires_user_input": True,
        "request_id": request.id,
        "question": request.question,
        "context": request.context,
        "default": request.default,
    }
    if state.human_request_handler is None:
        return {**base, "ok": False, "error": "user input required, but no handler is available"}
    response = state.human_request_handler(request)
    if isinstance(response, UserInputResponse):
        answer = response.answer
        canceled = response.canceled
    elif response is None:
        answer = ""
        canceled = True
    else:
        answer = str(response)
        canceled = False
    if canceled:
        return {**base, "ok": False, "canceled": True, "error": "user canceled the request"}
    return {**base, "ok": True, "answer": answer}


def request_write_approval(
    state: RuntimeState,
    *,
    tool_name: str,
    action: str,
    path: str,
    preview: str,
) -> dict[str, Any] | None:
    command = f"{action}: {path}"
    risk_reason = preview[:4000] or command
    request = make_approval_request(command, risk_reason, tool_name=tool_name)
    base = {
        "requires_approval": True,
        "approval_id": request.id,
        "risk_reason": risk_reason,
        "command": command,
        "tool_name": tool_name,
    }
    if state.approval_mode == "auto":
        return {**base, "approved": True}
    if state.approval_mode == "deny":
        return {
            **base,
            "ok": False,
            "approved": False,
            "error": f"human approval required for {command}",
        }
    if state.approval_handler is None:
        return None

    decision = state.approval_handler(request)
    if isinstance(decision, ApprovalDecision):
        approved = decision.approved
        decision_reason = decision.reason
    else:
        approved = bool(decision)
        decision_reason = ""
    if approved:
        return {**base, "approved": True}
    return {
        **base,
        "ok": False,
        "approved": False,
        "error": decision_reason or f"human rejected {command}",
    }
