from __future__ import annotations

import json
import re
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
    options: Any = None,
) -> dict[str, Any]:
    question = str(question or "").strip()
    if not question:
        return {"ok": False, "error": "question must not be empty"}
    normalized_options = _normalize_options(options)
    request = make_user_input_request(
        question,
        context=str(context or ""),
        default=str(default or ""),
        options=normalized_options,
    )
    base = {
        "requires_user_input": True,
        "request_id": request.id,
        "question": request.question,
        "context": request.context,
        "default": request.default,
        "options": request.options,
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


def _normalize_options(options: Any) -> list[str]:
    if not options:
        return []
    if isinstance(options, str):
        text = options.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            parsed_options = _normalize_options(parsed)
            if parsed_options:
                return parsed_options
        except json.JSONDecodeError:
            pass
        text = re.sub(r"^[\s#>*•\-]+", "", text)
        text = re.sub(r"^(?:选项|可选项|choices?|options?)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
        parts = [
            part.strip(" \t\r\n\"'“”‘’*`")
            for part in re.split(r"\s*(?:[,，、;；|/]|或|还是)\s*", text)
        ]
        normalized = [part for part in parts if part]
        if len(normalized) < 2:
            numbered = _normalize_numbered_lines(text)
            if numbered:
                return numbered
        return _dedupe_options(normalized)
    if isinstance(options, dict):
        for key in ("options", "choices", "values"):
            if key in options:
                return _normalize_options(options[key])
        label = options.get("label") or options.get("name") or options.get("title") or options.get("value")
        return [str(label).strip()] if label else []
    if isinstance(options, (list, tuple, set)):
        normalized: list[str] = []
        for option in options:
            normalized.extend(_normalize_options(option) if isinstance(option, (dict, list, tuple, set)) else [str(option).strip()])
        return _dedupe_options([option for option in normalized if option])
    return [str(options).strip()]


def _normalize_numbered_lines(text: str) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return []
    options: list[str] = []
    for line in lines:
        match = re.match(
            r"^(?:[-*•]\s*)?(?:(?:\d+\ufe0f?\u20e3)|[①②③④⑤⑥⑦⑧⑨⑩]|(?:方案|选项)\s*[一二三四五六七八九十A-Ha-h\d]+|[A-Ha-h]|\d+)\s*[.)、:：-]?\s*(.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            value = re.sub(r"[\s*`]+", " ", match.group(1)).strip()
            if value:
                options.append(value)
    return _dedupe_options(options) if len(options) >= 1 else []


def _dedupe_options(options: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for option in options:
        value = str(option or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


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
