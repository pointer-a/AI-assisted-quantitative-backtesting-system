from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from exelixi.graph.state import ExelixiGraphState
from exelixi.prompts.stage3 import SEARCH_AGENT_PROMPT
from exelixi.providers.openai_provider import create_model, is_content_risk_error, safe_model_error
from exelixi.tools.web_search_tool import build_web_search_tool


Writer = Callable[[dict[str, Any]], None]


def run_search_agent(
    state: ExelixiGraphState,
    instruction: str,
    *,
    writer: Writer | None = None,
    max_loops: int = 4,
) -> dict[str, Any]:
    writer = writer or (lambda _: None)
    model = create_model()
    search_agent = model.bind_tools([build_web_search_tool()])
    messages = [
        SystemMessage(content=SEARCH_AGENT_PROMPT),
        HumanMessage(
            content=(
                f"Task: {state['task']}\n\n"
                f"Planner instruction:\n{instruction}\n\n"
                f"Existing research notes:\n{state.get('research_notes', '')}\n\n"
                "Search as needed and finish with a concise research summary plus source URLs."
            )
        ),
    ]

    produced_messages: list[Any] = []
    queries: list[str] = []
    sources: list[dict[str, Any]] = []
    answers: list[str] = []
    tool_events: list[dict[str, Any]] = []

    for _ in range(max_loops):
        try:
            response = search_agent.invoke(messages)
        except Exception as exc:
            if not is_content_risk_error(exc):
                raise
            summary = _content_risk_fallback_summary(instruction, queries, answers, sources)
            response = AIMessage(content=summary)
            produced_messages.append(response)
            writer(
                {
                    "type": "model_warning",
                    "node": "searchAgent",
                    "message": safe_model_error(exc),
                    "fallback": summary,
                }
            )
            break
        produced_messages.append(response)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break
        for call in tool_calls:
            args = call.get("args") or {}
            query = str(args.get("query", ""))
            if query:
                queries.append(query)
            writer({"type": "tool_call", "node": "searchAgent", "name": call.get("name"), "args": args})
            tool_result = _execute_search_tool(call)
            event = _tool_result_event(tool_result)
            tool_events.append(event)
            writer(event)
            parsed = _parse_tool_content(tool_result.content)
            if isinstance(parsed, dict):
                if parsed.get("answer"):
                    answers.append(str(parsed["answer"]))
                for item in parsed.get("results", []) or []:
                    if isinstance(item, dict):
                        sources.append(item)
                writer(
                    {
                        "type": "search_results",
                        "query": parsed.get("query", query),
                        "answer": parsed.get("answer", ""),
                        "sources": parsed.get("results", []),
                    }
                )
            safe_tool_result = _sanitize_tool_result_for_model(tool_result)
            produced_messages.append(safe_tool_result)
            messages.append(safe_tool_result)

    summary = _last_ai_content(produced_messages) or "\n".join(answers) or _sources_brief(sources)
    result = {
        "ok": True,
        "summary": summary,
        "queries": queries,
        "sources": _dedupe_sources(sources),
        "messages": produced_messages,
        "tool_events": tool_events,
    }
    writer(
        {
            "type": "search_summary",
            "summary": result["summary"],
            "queries": result["queries"],
            "sources": result["sources"],
        }
    )
    return result


def _execute_search_tool(call: dict[str, Any]) -> ToolMessage:
    tool = build_web_search_tool()
    name = call.get("name", "")
    args = call.get("args") or {}
    if name != tool.name:
        result = {"ok": False, "error": f"unknown tool: {name}"}
    else:
        try:
            result = tool.invoke(args)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name=name,
        tool_call_id=call.get("id") or f"{name}-call",
    )


def _tool_result_event(tool_message: ToolMessage) -> dict[str, Any]:
    parsed = _parse_tool_content(tool_message.content)
    return {"type": "tool_result", "node": "searchAgent", "name": tool_message.name, "result": parsed}


def _parse_tool_content(content: Any) -> Any:
    try:
        return json.loads(str(content))
    except json.JSONDecodeError:
        return content


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped = []
    for source in sources:
        url = str(source.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(source)
    return deduped


def _sanitize_tool_result_for_model(tool_message: ToolMessage) -> ToolMessage:
    parsed = _parse_tool_content(tool_message.content)
    if not isinstance(parsed, dict):
        content = str(parsed)
        payload: Any = {"ok": True, "text": content[:600]}
    else:
        payload = {
            "ok": parsed.get("ok", False),
            "query": parsed.get("query", ""),
            "answer": str(parsed.get("answer", ""))[:500],
            "error": parsed.get("error", ""),
            "results": [
                {
                    "title": str(item.get("title", ""))[:180],
                    "url": str(item.get("url", "")),
                    "score": item.get("score"),
                }
                for item in parsed.get("results", []) or []
                if isinstance(item, dict)
            ],
        }
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        name=tool_message.name,
        tool_call_id=getattr(tool_message, "tool_call_id", None) or f"{tool_message.name}-call",
    )


def _content_risk_fallback_summary(
    instruction: str,
    queries: list[str],
    answers: list[str],
    sources: list[dict[str, Any]],
) -> str:
    parts = [
        "搜索已完成，但模型在汇总网页结果时触发内容风险保护；已改用降级汇总，避免工作流卡死。",
        f"任务指令：{instruction}",
    ]
    if queries:
        parts.append("已执行搜索：" + "；".join(queries[-6:]))
    if answers:
        parts.append("搜索引擎摘要：" + _short_text(" ".join(answers), 900))
    source_text = _sources_brief(sources)
    if source_text:
        parts.append("已收集来源：\n" + source_text)
    return "\n\n".join(parts)


def _sources_brief(sources: list[dict[str, Any]], *, limit: int = 10) -> str:
    lines = []
    for source in _dedupe_sources(sources)[:limit]:
        title = str(source.get("title", "")).strip() or "source"
        url = str(source.get("url", "")).strip()
        if url:
            lines.append(f"- {title}: {url}")
    return "\n".join(lines)


def _short_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _last_ai_content(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        content = getattr(message, "content", "")
        if content:
            return str(content)
    return ""
