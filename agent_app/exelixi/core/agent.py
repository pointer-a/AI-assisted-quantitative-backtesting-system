from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from langgraph.graph import add_messages

from exelixi.core.env import env_var, load_env

from exelixi.core.checkpoint import CheckpointManager, load_resume_inputs, normalize_checkpoint_mode
from exelixi.core.paths import default_workspace
from exelixi.core.session import (
    append_assistant_turn,
    append_user_turn,
    build_session_context,
    load_or_create_session,
    save_session,
    session_started_event,
    session_turn_saved_event,
    session_turn_started_event,
)
from exelixi.core.state import RuntimeState
from exelixi.core.trace import TraceRecorder, normalize_trace_mode
from exelixi.graph.workflow import build_complex_workflow, build_entry_workflow


def create_runtime(
    workspace: Path | None = None,
    *,
    approval_mode: str = "inline",
    approval_handler=None,
    human_request_handler=None,
    checkpoint_mode: str | None = None,
    resume_from: Path | None = None,
    trace_mode: str | None = None,
) -> RuntimeState:
    load_env()
    selected = workspace or resume_from or default_workspace()
    selected.mkdir(parents=True, exist_ok=True)
    return RuntimeState(
        workspace=selected,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        human_request_handler=human_request_handler,
        bash_default_timeout_seconds=_env_int("EXELIXI_BASH_DEFAULT_TIMEOUT_SECONDS", 120),
        bash_max_timeout_seconds=_env_int("EXELIXI_BASH_MAX_TIMEOUT_SECONDS", 600),
        bash_max_output_chars=_env_int("EXELIXI_BASH_MAX_OUTPUT_CHARS", 6000),
        bash_env_file=_env_path("EXELIXI_BASH_ENV_FILE"),
        checkpoint_mode=normalize_checkpoint_mode(checkpoint_mode or env_var("EXELIXI_CHECKPOINT_MODE", "light")),
        resume_from=resume_from,
        trace_mode=normalize_trace_mode(trace_mode or env_var("EXELIXI_TRACE_MODE", "on")),
    )


def stream_agent_events(
    task: str | None = None,
    *,
    workspace: Path | None = None,
    max_attempts: int = 3,
    approval_mode: str = "inline",
    approval_handler=None,
    human_request_handler=None,
    checkpoint_mode: str | None = None,
    resume_workspace: Path | None = None,
    trace_mode: str | None = None,
) -> Iterator[dict[str, Any]]:
    resume_path = resume_workspace.expanduser() if resume_workspace is not None else None
    if resume_path is None:
        route = "workflow"
        entry_state: dict[str, Any] = {"task": task or "", "messages": []}
        for mode, event in build_entry_workflow().stream(entry_state, stream_mode=["updates", "custom"]):
            if mode == "custom":
                yield {"type": "custom_event", "event": event}
                if isinstance(event, dict) and event.get("type") == "intent_decision":
                    route = str(event.get("route") or "workflow")
            else:
                _merge_graph_update(entry_state, event)
                yield {"type": "graph_event", "event": event}
        if route == "chat":
            return

    selected_workspace = resume_path or workspace
    state = create_runtime(
        selected_workspace,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        human_request_handler=human_request_handler,
        checkpoint_mode=checkpoint_mode,
        resume_from=resume_path,
        trace_mode=trace_mode,
    )
    workflow = build_complex_workflow()
    yield {"type": "workspace", "path": str(state.workspace)}

    resumed = False
    resume_event: dict[str, Any] | None = None
    if resume_path is not None:
        inputs, resume_event = load_resume_inputs(state, task=task, max_attempts=max_attempts)
        resumed = True
        yield {"type": "custom_event", "event": resume_event}
    else:
        inputs = {
            "task": task or "",
            "runtime": state,
            "messages": [],
            "attempts": 0,
            "max_attempts": max_attempts,
        }

    current_state: dict[str, Any] = dict(inputs)
    manager = CheckpointManager(state, task=str(current_state.get("task", "")))
    trace = TraceRecorder(state, task=str(current_state.get("task", "")))
    trace.start(current_state, resumed=resumed, resume_event=resume_event)
    if resume_event is not None:
        trace.record_custom_event(resume_event)
    started_checkpoint = manager.save(current_state, status="started", latest_node="start")
    if started_checkpoint:
        trace.record_custom_event(started_checkpoint)
    latest_node = "start"

    try:
        for mode, event in workflow.stream(inputs, stream_mode=["updates", "custom"]):
            if mode == "custom":
                trace.record_custom_event(event)
                if _custom_event_needs_checkpoint(event):
                    saved = manager.save(current_state, status="running", latest_node=latest_node, event={"mode": mode, "payload": event})
                    if saved:
                        trace.record_custom_event(saved)
                yield {"type": "custom_event", "event": event}
            else:
                latest_node = _latest_graph_node(event) or latest_node
                _merge_graph_update(current_state, event)
                trace.record_graph_update(event)
                saved = manager.save(current_state, status="running", latest_node=latest_node, event={"mode": mode, "payload": event})
                if saved:
                    trace.record_custom_event(saved)
                yield {"type": "graph_event", "event": event}
    except KeyboardInterrupt:
        saved = manager.save(current_state, status="interrupted", latest_node=latest_node)
        if saved:
            trace.record_custom_event(saved)
            yield {"type": "custom_event", "event": saved}
        trace_event = trace.end(status="interrupted", latest_node=latest_node, final_state=current_state)
        if trace_event:
            yield {"type": "custom_event", "event": trace_event}
        return

    saved = manager.save(current_state, status="finished", latest_node=latest_node)
    if saved:
        trace.record_custom_event(saved)
        yield {"type": "custom_event", "event": saved}
    trace_event = trace.end(status="finished", latest_node=latest_node, final_state=current_state)
    if trace_event:
        yield {"type": "custom_event", "event": trace_event}


def stream_session_events(
    task: str | None = None,
    *,
    session_workspace: Path | None = None,
    max_attempts: int = 3,
    approval_mode: str = "inline",
    approval_handler=None,
    human_request_handler=None,
    checkpoint_mode: str | None = None,
    resume_workspace: Path | None = None,
    trace_mode: str | None = None,
) -> Iterator[dict[str, Any]]:
    workspace = (resume_workspace or session_workspace or default_workspace()).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    session = load_or_create_session(workspace)
    resumed = resume_workspace is not None
    yield {"type": "custom_event", "event": session_started_event(workspace, session, resumed=resumed)}
    yield {"type": "workspace", "path": str(workspace)}

    if not task:
        return

    turn = append_user_turn(session, task)
    save_session(workspace, session)
    yield {"type": "custom_event", "event": session_turn_started_event(workspace, session, turn=turn, task=task)}
    session_context = build_session_context(workspace, session)

    route = "workflow"
    entry_state: dict[str, Any] = {
        "task": task or "",
        "messages": [],
        "session_id": session.get("session_id", ""),
        "session_turn": turn,
        "session_context": session_context,
    }
    for mode, event in build_entry_workflow().stream(entry_state, stream_mode=["updates", "custom"]):
        if mode == "custom":
            yield {"type": "custom_event", "event": event}
            if isinstance(event, dict) and event.get("type") == "intent_decision":
                route = str(event.get("route") or "workflow")
        else:
            _merge_graph_update(entry_state, event)
            yield {"type": "graph_event", "event": event}

    if route == "chat":
        response = str(entry_state.get("chat_response") or entry_state.get("final_answer") or "")
        append_assistant_turn(session, turn=turn, route="chat", content=response, summary=response)
        save_session(workspace, session)
        yield {"type": "custom_event", "event": session_turn_saved_event(workspace, session, turn=turn, route="chat")}
        return

    workflow_events = _stream_complex_workflow(
        task=task,
        workspace=workspace,
        max_attempts=max_attempts,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        human_request_handler=human_request_handler,
        checkpoint_mode=checkpoint_mode,
        resume_workspace=resume_workspace,
        trace_mode=trace_mode,
        session=session,
        turn=turn,
        session_context=session_context,
    )
    final_answer = ""
    workflow_history: list[dict[str, Any]] = []
    for event in workflow_events:
        final_answer = _final_answer_from_event(event) or final_answer
        snapshot = _workflow_event_snapshot(event)
        if snapshot:
            workflow_history.append(snapshot)
        yield event

    append_assistant_turn(
        session,
        turn=turn,
        route="workflow",
        content=final_answer,
        summary=final_answer,
        workflow_events=workflow_history,
    )
    save_session(workspace, session)
    yield {"type": "custom_event", "event": session_turn_saved_event(workspace, session, turn=turn, route="workflow")}


def _stream_complex_workflow(
    *,
    task: str | None,
    workspace: Path,
    max_attempts: int,
    approval_mode: str,
    approval_handler,
    human_request_handler,
    checkpoint_mode: str | None,
    resume_workspace: Path | None,
    trace_mode: str | None,
    session: dict[str, Any] | None = None,
    turn: int | None = None,
    session_context: str = "",
) -> Iterator[dict[str, Any]]:
    resume_path = resume_workspace.expanduser() if resume_workspace is not None else None
    state = create_runtime(
        workspace,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        human_request_handler=human_request_handler,
        checkpoint_mode=checkpoint_mode,
        resume_from=resume_path,
        trace_mode=trace_mode,
    )
    workflow = build_complex_workflow()

    resumed = False
    resume_event: dict[str, Any] | None = None
    if resume_path is not None:
        inputs, resume_event = load_resume_inputs(state, task=task, max_attempts=max_attempts)
        resumed = True
        yield {"type": "custom_event", "event": resume_event}
    else:
        inputs = {
            "task": task or "",
            "runtime": state,
            "messages": [],
            "attempts": 0,
            "max_attempts": max_attempts,
        }

    if session is not None:
        inputs["session_id"] = session.get("session_id", "")
    if turn is not None:
        inputs["session_turn"] = turn
    if session_context:
        inputs["session_context"] = session_context
    metadata = dict(inputs.get("metadata", {}))
    if session is not None:
        metadata["session_id"] = session.get("session_id", "")
    if turn is not None:
        metadata["session_turn"] = turn
    if metadata:
        inputs["metadata"] = metadata

    current_state: dict[str, Any] = dict(inputs)
    manager = CheckpointManager(state, task=str(current_state.get("task", "")))
    trace = TraceRecorder(state, task=str(current_state.get("task", "")))
    trace.start(current_state, resumed=resumed, resume_event=resume_event)
    if resume_event is not None:
        trace.record_custom_event(resume_event)
    started_checkpoint = manager.save(current_state, status="started", latest_node="start")
    if started_checkpoint:
        trace.record_custom_event(started_checkpoint)
    latest_node = "start"

    try:
        for mode, event in workflow.stream(inputs, stream_mode=["updates", "custom"]):
            if mode == "custom":
                trace.record_custom_event(event)
                if _custom_event_needs_checkpoint(event):
                    saved = manager.save(current_state, status="running", latest_node=latest_node, event={"mode": mode, "payload": event})
                    if saved:
                        trace.record_custom_event(saved)
                yield {"type": "custom_event", "event": event}
            else:
                latest_node = _latest_graph_node(event) or latest_node
                _merge_graph_update(current_state, event)
                trace.record_graph_update(event)
                saved = manager.save(current_state, status="running", latest_node=latest_node, event={"mode": mode, "payload": event})
                if saved:
                    trace.record_custom_event(saved)
                yield {"type": "graph_event", "event": event}
    except KeyboardInterrupt:
        saved = manager.save(current_state, status="interrupted", latest_node=latest_node)
        if saved:
            trace.record_custom_event(saved)
            yield {"type": "custom_event", "event": saved}
        trace_event = trace.end(status="interrupted", latest_node=latest_node, final_state=current_state)
        if trace_event:
            yield {"type": "custom_event", "event": trace_event}
        return

    saved = manager.save(current_state, status="finished", latest_node=latest_node)
    if saved:
        trace.record_custom_event(saved)
        yield {"type": "custom_event", "event": saved}
    trace_event = trace.end(status="finished", latest_node=latest_node, final_state=current_state)
    if trace_event:
        yield {"type": "custom_event", "event": trace_event}


def _env_int(name: str, default: int) -> int:
    try:
        value = int(env_var(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_path(name: str) -> Path | None:
    raw = (env_var(name, "") or "").strip()
    return Path(raw).expanduser() if raw else None


def _latest_graph_node(event: Any) -> str | None:
    if isinstance(event, dict) and event:
        return str(next(reversed(event)))
    return None


def _merge_graph_update(state: dict[str, Any], event: Any) -> None:
    if not isinstance(event, dict):
        return
    for update in event.values():
        if not isinstance(update, dict):
            continue
        for key, value in update.items():
            if key == "messages":
                state["messages"] = list(add_messages(state.get("messages", []), value))
            else:
                state[key] = value


def _custom_event_needs_checkpoint(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    if event.get("type") != "tool_result":
        return False
    result = event.get("result")
    if not isinstance(result, dict):
        return False
    return result.get("ok") is False or bool(result.get("requires_approval"))


def _final_answer_from_event(event: dict[str, Any]) -> str:
    if event.get("type") != "graph_event":
        return ""
    payload = event.get("event")
    if not isinstance(payload, dict):
        return ""
    update = payload.get("final")
    if not isinstance(update, dict):
        return ""
    return str(update.get("final_answer") or "")


def _workflow_event_snapshot(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type", ""))
    if event_type == "custom_event":
        payload = event.get("event")
        if not isinstance(payload, dict):
            return {}
        inner_type = str(payload.get("type", "custom_event"))
        return {
            "kind": "custom",
            "type": inner_type,
            "node": str(payload.get("node") or payload.get("from") or payload.get("to") or ""),
            "name": str(payload.get("name") or ""),
            "title": _workflow_custom_title(payload),
            "detail": _workflow_custom_detail(payload),
        }
    if event_type == "graph_event":
        payload = event.get("event")
        if not isinstance(payload, dict) or not payload:
            return {}
        node = str(next(iter(payload)))
        update = payload.get(node) if isinstance(payload.get(node), dict) else {}
        return {
            "kind": "graph",
            "type": "graph_event",
            "node": node,
            "name": "",
            "title": _workflow_graph_title(node, update),
            "detail": _workflow_graph_detail(node, update),
        }
    if event_type == "workspace":
        return {
            "kind": "runtime",
            "type": "workspace",
            "node": "",
            "name": "",
            "title": "工作区",
            "detail": str(event.get("path", "")),
        }
    return {
        "kind": "runtime",
        "type": event_type,
        "node": "",
        "name": "",
        "title": event_type,
        "detail": _short_json(event, 900),
    }


def _workflow_custom_title(event: dict[str, Any]) -> str:
    event_type = str(event.get("type", ""))
    if event_type == "plan_snapshot":
        return "计划快照"
    if event_type == "todo_update":
        return "Todo 更新"
    if event_type == "tool_call":
        return f"{event.get('node') or 'agent'} · {event.get('name') or 'tool'}"
    if event_type == "tool_result":
        return f"工具返回 · {event.get('name') or 'tool'}"
    if event_type == "handoff":
        return f"交接 · {event.get('from') or 'agent'} -> {event.get('to') or 'agent'}"
    if event_type == "handoff_result":
        return f"返回 · {event.get('from') or 'agent'}"
    if event_type in {"search_results", "search_summary"}:
        return "搜索结果"
    if event_type == "memory_snapshot":
        return "记忆快照"
    if event_type == "context_monitor":
        return "上下文监视"
    if event_type == "context_compression":
        return "上下文压缩"
    if event_type == "checkpoint_saved":
        return "检查点已保存"
    if event_type == "checkpoint_resumed":
        return "检查点已恢复"
    if event_type == "trace_summary":
        return "运行摘要"
    return event_type


def _workflow_custom_detail(event: dict[str, Any]) -> str:
    event_type = str(event.get("type", ""))
    if event_type in {"plan_snapshot", "todo_update"}:
        return _workflow_plan_detail(event)
    if event_type == "tool_call":
        if event.get("name") in {"FileWriteTool", "FileEditTool"}:
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            file_name = Path(str(args.get("file_path") or args.get("path") or "file")).name
            action = "准备写入文件" if event.get("name") == "FileWriteTool" else "准备修改文件"
            return f"{action}: {file_name}"
        return _short_json(event.get("args", {}), 900)
    if event_type == "tool_result":
        if event.get("name") in {"FileWriteTool", "FileEditTool"}:
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            file_name = Path(str(result.get("path") or "file")).name
            action = "生成文件" if result.get("type") == "create" else "修改文件"
            status = "ok" if result.get("ok") is not False else "failed"
            return f"{action}: {file_name}\nstatus: {status}"
        return _short_json(event.get("result", {}), 1200)
    if event_type == "handoff":
        return str(event.get("instruction") or event.get("task") or "")
    if event_type == "handoff_result":
        return str(event.get("summary") or event.get("result") or "")
    if event_type in {"search_results", "search_summary"}:
        sources = event.get("sources") if isinstance(event.get("sources"), list) else []
        source_text = "\n".join(
            f"{idx + 1}. {source.get('title', '')} {source.get('url', '')}"
            for idx, source in enumerate(sources[:8])
            if isinstance(source, dict)
        )
        return "\n\n".join(part for part in [str(event.get("answer") or event.get("summary") or ""), source_text] if part)
    if event_type == "memory_snapshot":
        return _short_json(event.get("layers", {}), 1000)
    if event_type == "context_monitor":
        return f"Tokens: {event.get('context_token_count') or event.get('token_count')} / {event.get('context_token_limit') or event.get('token_limit')}\nCompress: {event.get('context_should_compress') or event.get('should_compress')}"
    if event_type == "context_compression":
        return _short_json(event, 1200)
    if event_type == "trace_summary":
        return _short_json(
            {
                "status": event.get("status"),
                "node_visits": event.get("node_visits", {}),
                "tool_calls": event.get("tool_calls"),
                "failed_tool_calls": event.get("failed_tool_calls"),
                "checkpoint_count": event.get("checkpoint_count"),
            },
            1200,
        )
    return _short_json(event, 1200)


def _workflow_graph_title(node: str, update: dict[str, Any]) -> str:
    labels = {
        "planner": "规划器",
        "codeAgent": "codeAgent 摘要",
        "actor": "actor 摘要",
        "verifier": "验证器",
        "final": "最终结果",
        "context_monitor": "上下文监视",
        "context_compressor": "上下文压缩",
    }
    return labels.get(node, node)


def _workflow_graph_detail(node: str, update: dict[str, Any]) -> str:
    if node == "planner":
        return _workflow_plan_detail(update)
    if node in {"codeAgent", "actor"}:
        return str(update.get("code_agent_summary") or update.get("last_actor_summary") or "")
    if node == "verifier":
        checks = update.get("verification_checks") if isinstance(update.get("verification_checks"), list) else []
        check_text = "\n".join(
            f"- {'PASS' if check.get('passed') else 'FAIL'} {check.get('name', '')}: {check.get('detail', '')}"
            for check in checks[:12]
            if isinstance(check, dict)
        )
        return "\n".join(part for part in [str(update.get("verifier_summary") or ""), check_text] if part)
    if node == "final":
        return str(update.get("final_answer") or "")[:1200]
    return _short_json(update, 1200)


def _workflow_plan_detail(update: dict[str, Any]) -> str:
    lines = []
    if update.get("plan_summary"):
        lines.append(str(update.get("plan_summary")))
    todos = update.get("todos")
    if isinstance(todos, list):
        lines.extend(
            f"- [{todo.get('status', '')}] {todo.get('content') or todo.get('description') or ''}"
            for todo in todos[:20]
            if isinstance(todo, dict)
        )
    commands = update.get("verification_commands")
    if isinstance(commands, list) and commands:
        lines.append("Verification:")
        lines.extend(f"- {command}" for command in commands[:10])
    return "\n".join(lines)


def _short_json(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    return text if len(text) <= limit else text[: limit - 3] + "..."
