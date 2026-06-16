from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from exelixi.core.agent import stream_session_events
from exelixi.core.approval import ApprovalDecision, ApprovalRequest, UserInputRequest, UserInputResponse

STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title="Exelixi Web")


# ── Approval bridge: agent thread ↔ async WebSocket ──────────────────────

class _ApprovalBridge:
    """Bridge a blocking approval_handler call inside the agent thread
    to an async WebSocket round-trip."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, concurrent.futures.Future[ApprovalDecision]] = {}
        self._pending_inputs: dict[str, concurrent.futures.Future[UserInputResponse]] = {}

    def make_handler(
        self,
        event_queue: asyncio.Queue[dict[str, Any] | None],
        loop: asyncio.AbstractEventLoop,
    ):
        def handler(request: ApprovalRequest) -> ApprovalDecision:
            future: concurrent.futures.Future[ApprovalDecision] = concurrent.futures.Future()
            with self._lock:
                self._pending[request.id] = future
            # notify the async side → WebSocket
            asyncio.run_coroutine_threadsafe(
                event_queue.put({
                    "type": "approval_request",
                    "request_id": request.id,
                    "command": request.command,
                    "risk_reason": request.risk_reason,
                    "tool_name": request.tool_name,
                }),
                loop,
            ).result()
            # block the agent thread until the user decides
            return future.result()

        return handler

    def make_user_input_handler(
        self,
        event_queue: asyncio.Queue[dict[str, Any] | None],
        loop: asyncio.AbstractEventLoop,
    ):
        def handler(request: UserInputRequest) -> UserInputResponse:
            future: concurrent.futures.Future[UserInputResponse] = concurrent.futures.Future()
            with self._lock:
                self._pending_inputs[request.id] = future
            asyncio.run_coroutine_threadsafe(
                event_queue.put({
                    "type": "user_input_request",
                    "request_id": request.id,
                    "question": request.question,
                    "context": request.context,
                    "default": request.default,
                    "options": getattr(request, "options", []),
                    "tool_name": request.tool_name,
                }),
                loop,
            ).result()
            return future.result()

        return handler

    def resolve(self, request_id: str, approved: bool, reason: str = "") -> None:
        with self._lock:
            future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(ApprovalDecision(approved=approved, reason=reason))

    def resolve_user_input(self, request_id: str, answer: str = "", canceled: bool = False) -> None:
        with self._lock:
            future = self._pending_inputs.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(UserInputResponse(answer=answer, canceled=canceled))


_bridge = _ApprovalBridge()


# ── JSON sanitizer ───────────────────────────────────────────────────────

_JSON_SAFE_TYPES = (type(None), bool, int, float, str, list, dict, tuple)
# LangChain message classes to handle explicitly
_LANGCHAIN_MESSAGE_CLASSES: tuple[type, ...] | None = None


def _get_langchain_message_classes():
    """Lazy-import LangChain message types so the server can start without them."""
    global _LANGCHAIN_MESSAGE_CLASSES
    if _LANGCHAIN_MESSAGE_CLASSES is not None:
        return _LANGCHAIN_MESSAGE_CLASSES
    try:
        from langchain_core.messages import BaseMessage
        _LANGCHAIN_MESSAGE_CLASSES = (BaseMessage,)
    except ImportError:
        _LANGCHAIN_MESSAGE_CLASSES = ()
    return _LANGCHAIN_MESSAGE_CLASSES


def _sanitize(obj: Any, max_depth: int = 10) -> Any:
    """Recursively convert non-JSON-serializable objects to safe representations.

    Handles LangChain messages, datetimes, and any other types that
    ``json.dumps`` cannot serialize.
    """
    if max_depth <= 0:
        return str(obj)[:200] if obj is not None else None

    if isinstance(obj, _JSON_SAFE_TYPES):
        if isinstance(obj, dict):
            return {k: _sanitize(v, max_depth - 1) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_sanitize(item, max_depth - 1) for item in obj]
        return obj

    # LangChain BaseMessage → dict via message_to_dict
    msg_classes = _get_langchain_message_classes()
    if msg_classes and isinstance(obj, msg_classes):
        try:
            from langchain_core.messages import message_to_dict
            return _sanitize(message_to_dict(obj), max_depth - 1)
        except Exception:
            return {"type": type(obj).__name__, "content": str(obj.content)[:500] if hasattr(obj, "content") else str(obj)[:500]}

    # datetime → ISO string
    if isinstance(obj, datetime):
        return obj.isoformat()

    # fallback: string representation
    return str(obj)[:1000]


# ── WebSocket ────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_handler(ws: WebSocket) -> None:
    await ws.accept()
    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    try:
        while True:
            # ── wait for next "run" command ────────────────────────────
            data = await ws.receive_json()
            if not isinstance(data, dict) or data.get("type") != "run":
                if data.get("type") in ("ping",):
                    await ws.send_json({"type": "pong"})
                    continue
                await ws.send_json({"type": "error", "message": "expected {'type':'run','task':'…'}"})
                continue

            task: str = data.get("task", "")
            workspace_raw: str | None = data.get("workspace")
            session_workspace = Path(workspace_raw).expanduser() if workspace_raw else None

            # ── spawn agent thread for this turn ──────────────────────
            def run_in_thread(task=task, sw=session_workspace) -> None:
                try:
                    handler = _bridge.make_handler(event_queue, loop)
                    user_input_handler = _bridge.make_user_input_handler(event_queue, loop)
                    for event in stream_session_events(
                        task,
                        session_workspace=sw,
                        max_attempts=int(data.get("max_attempts", 3)),
                        approval_mode=str(data.get("approval_mode", "inline")),
                        approval_handler=handler,
                        human_request_handler=user_input_handler,
                        checkpoint_mode=data.get("checkpoint_mode"),
                        trace_mode=data.get("trace_mode"),
                    ):
                        asyncio.run_coroutine_threadsafe(
                            event_queue.put(event), loop,
                        ).result()
                except Exception as exc:
                    asyncio.run_coroutine_threadsafe(
                        event_queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"}),
                        loop,
                    ).result()
                finally:
                    asyncio.run_coroutine_threadsafe(
                        event_queue.put({"type": "__done__", "status": "finished"}), loop,
                    ).result()

            thread = threading.Thread(target=run_in_thread, daemon=True)
            thread.start()

            # ── forward events while also listening for approvals ─────
            while True:
                event = await event_queue.get()
                if event is None or event.get("type") == "__done__":
                    break

                await _safe_send(ws, event)

                # If this is a human request, the agent thread is blocked
                # waiting for the browser's response.
                if event.get("type") == "approval_request":
                    rid = event.get("request_id", "")
                    while True:
                        try:
                            resp = await asyncio.wait_for(ws.receive_json(), timeout=120)
                            if isinstance(resp, dict) and resp.get("type") == "approval":
                                _bridge.resolve(
                                    resp.get("request_id", ""),
                                    bool(resp.get("approved", False)),
                                    str(resp.get("reason", "")),
                                )
                                break
                        except asyncio.TimeoutError:
                            # User didn't respond in 2 minutes; reject
                            _bridge.resolve(rid, False, "approval timed out")
                            break
                if event.get("type") == "user_input_request":
                    rid = event.get("request_id", "")
                    while True:
                        try:
                            resp = await asyncio.wait_for(ws.receive_json(), timeout=300)
                            if isinstance(resp, dict) and resp.get("type") == "user_input":
                                _bridge.resolve_user_input(
                                    resp.get("request_id", ""),
                                    str(resp.get("answer", "")),
                                    bool(resp.get("canceled", False)),
                                )
                                break
                        except asyncio.TimeoutError:
                            _bridge.resolve_user_input(rid, "", True)
                            break

            thread.join(timeout=5)
            await ws.send_json({"type": "__ready__", "message": "ready for next task"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass


async def _safe_send(ws: WebSocket, event: dict[str, Any]) -> None:
    """Send an event dict as JSON, sanitizing non-serializable values."""
    try:
        safe = _sanitize(event)
        await ws.send_json(safe)
    except Exception:
        try:
            await ws.send_json({"type": "error", "message": "failed to serialize event"})
        except Exception:
            pass


# ── REST helpers ─────────────────────────────────────────────────────────

@app.get("/api/workspaces")
async def list_workspaces() -> list[dict[str, Any]]:
    """List existing Exelixi workspaces."""
    base = Path.cwd() / ".exelixi" / "workspaces"
    if not base.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if child.is_dir():
            info = _workspace_session_info(child)
            todo_file = child / "TODO.md"
            if todo_file.is_file():
                info["has_todo"] = True
            entries.append(info)
    return sorted(entries, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def _workspace_session_info(workspace: Path) -> dict[str, Any]:
    """Return lightweight metadata used by the Web conversation list."""
    info: dict[str, Any] = {
        "name": workspace.name,
        "path": str(workspace),
        "title": workspace.name,
        "session_id": "",
        "turn_index": 0,
        "updated_at": "",
        "last_route": "",
        "last_task": "",
        "recent_turns": [],
    }
    path = workspace / ".exelixi" / "session" / "session.json"
    if not path.is_file():
        return info
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return info
    if not isinstance(raw, dict):
        return info
    recent_turns = raw.get("recent_turns")
    if not isinstance(recent_turns, list):
        recent_turns = []
    last_task = str(raw.get("last_task", "") or "")
    info.update({
        "title": last_task[:80] or raw.get("session_id", "") or workspace.name,
        "session_id": str(raw.get("session_id", "") or ""),
        "turn_index": int(raw.get("turn_index") or 0),
        "updated_at": str(raw.get("updated_at", "") or ""),
        "last_route": str(raw.get("last_route", "") or ""),
        "last_task": last_task,
        "recent_turns": recent_turns[-8:],
    })
    return info


# ── Static frontend ──────────────────────────────────────────────────────

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
