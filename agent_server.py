"""
AI 智能回测 + Exelixi Agent 集成服务器。

启动方式:
    python agent_server.py
    # 回测前端: http://127.0.0.1:8765
    # Agent 聊天: 内嵌在回测前端右侧面板
    # Agent WebSocket: ws://127.0.0.1:8765/ws

Agent 的写入权限限制在 Agent_strategy/ 目录内。
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import mimetypes
import threading
import time
import uuid
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import uvicorn

import sys as _sys
PROJECT_ROOT = Path(__file__).resolve().parent
_sys.path.insert(0, str(PROJECT_ROOT / "agent_app"))
WEB_ROOT = PROJECT_ROOT / "web"
DATA_ROOT = PROJECT_ROOT / "数据"
REPORTS_ROOT = PROJECT_ROOT / "reports"
AGENT_STRATEGY_ROOT = PROJECT_ROOT / "Agent_strategy"
AGENT_WORKSPACE = PROJECT_ROOT / "workspace"

# 确保 mimetypes 识别 .js 等文件
mimetypes.init()


# ── 回测后端 (复用 ai_backtester 模块) ────────────────────────────────────────
from ai_backtester.data import discover_year_files, load_year_csvs, longest_contiguous_years
from ai_backtester.engine import BacktestEngine
from ai_backtester.jobs import BacktestJobStore
from ai_backtester.report import METRIC_LABELS, make_report_path, write_csv_exports, write_html_report
from ai_backtester.strategies import create_strategy

BARS_CACHE: dict[tuple[str, tuple[int, ...], str], list] = {}

JOB_STORE = BacktestJobStore(
    REPORTS_ROOT / "backtest_jobs.sqlite",
    lambda payload, progress_callback: _backtest_payload(payload, progress_callback=progress_callback, include_prices=False),
)


class UserVisibleError(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("error", "请求失败"))
        self.payload = payload


# ── FastAPI Agent 后端 ───────────────────────────────────────────────────────
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

agent_app = FastAPI(title="AI Backtester Agent")

try:
    from agent_app.exelixi.core.agent import stream_session_events
    from agent_app.exelixi.core.approval import ApprovalDecision, ApprovalRequest, UserInputRequest, UserInputResponse

    AGENT_AVAILABLE = True
except ImportError:
    AGENT_AVAILABLE = False


class _ApprovalBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, concurrent.futures.Future] = {}
        self._pending_inputs: dict[str, concurrent.futures.Future] = {}

    def make_handler(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        def handler(request) -> Any:
            future: concurrent.futures.Future = concurrent.futures.Future()
            with self._lock:
                self._pending[request.id] = future
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
            return future.result()
        return handler

    def make_user_input_handler(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        def handler(request) -> Any:
            future: concurrent.futures.Future = concurrent.futures.Future()
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


@agent_app.websocket("/ws")
async def ws_handler(ws: WebSocket) -> None:
    if not AGENT_AVAILABLE:
        await ws.accept()
        await ws.send_json({"type": "error", "message": "Agent 未安装依赖，请运行: pip install -r requirements-agent.txt"})
        return

    await ws.accept()
    event_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    try:
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict) or data.get("type") != "run":
                if data.get("type") in ("ping",):
                    await ws.send_json({"type": "pong"})
                    continue
                await ws.send_json({"type": "error", "message": "expected {'type':'run','task':'…'}"})
                continue

            task: str = data.get("task", "")
            workspace_raw: str | None = data.get("workspace")
            session_workspace = _resolve_agent_conversation_workspace(workspace_raw)

            def run_in_thread(task=task, sw=session_workspace) -> None:
                try:
                    handler = _bridge.make_handler(event_queue, loop)
                    user_input_handler = _bridge.make_user_input_handler(event_queue, loop)
                    for event in stream_session_events(
                        task,
                        session_workspace=sw,
                        max_attempts=int(data.get("max_attempts", 3)),
                        approval_mode=str(data.get("approval_mode", "auto")),
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

            while True:
                event = await event_queue.get()
                if event is None or event.get("type") == "__done__":
                    break
                await _safe_send(ws, event)

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
    try:
        await ws.send_json(event)
    except Exception:
        pass


@agent_app.get("/api/workspaces")
async def list_workspaces() -> list[dict[str, Any]]:
    base = AGENT_WORKSPACE
    if not base.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            entries.append(_workspace_session_info(child))
    return sorted(entries, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def _resolve_agent_conversation_workspace(workspace_raw: str | None) -> Path:
    """Resume an existing conversation folder, or create a new one under workspace/."""
    AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    if workspace_raw:
        requested = Path(workspace_raw).expanduser()
        try:
            resolved = requested.resolve()
            root = AGENT_WORKSPACE.resolve()
            resolved.relative_to(root)
            if resolved != root:
                resolved.mkdir(parents=True, exist_ok=True)
                return resolved
        except (OSError, ValueError):
            pass
    return _new_agent_conversation_workspace()


def _new_agent_conversation_workspace() -> Path:
    AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for _ in range(100):
        workspace = AGENT_WORKSPACE / f"conversation-{stamp}-{uuid.uuid4().hex[:6]}"
        try:
            workspace.mkdir(parents=True, exist_ok=False)
            return workspace
        except FileExistsError:
            continue
    workspace = AGENT_WORKSPACE / f"conversation-{stamp}-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


def _workspace_session_info(workspace: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": workspace.name,
        "path": str(workspace),
        "title": workspace.name,
        "session_id": "",
        "turn_index": 0,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(workspace.stat().st_mtime)),
        "last_route": "",
        "last_task": "",
        "recent_turns": [],
    }
    session_file = workspace / ".exelixi" / "session" / "session.json"
    if not session_file.is_file():
        return info
    try:
        raw = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return info
    if not isinstance(raw, dict):
        return info
    recent_turns = raw.get("recent_turns")
    if not isinstance(recent_turns, list):
        recent_turns = []
    last_task = str(raw.get("last_task", "") or "")
    info.update({
        "title": last_task[:80] or str(raw.get("session_id", "") or "") or workspace.name,
        "session_id": str(raw.get("session_id", "") or ""),
        "turn_index": int(raw.get("turn_index") or 0),
        "updated_at": str(raw.get("updated_at", "") or info["updated_at"]),
        "last_route": str(raw.get("last_route", "") or ""),
        "last_task": last_task,
        "recent_turns": recent_turns[-8:],
    })
    return info


# ── 回测 API (FastAPI 路由) ──────────────────────────────────────────────────
from fastapi import Request
from fastapi.responses import JSONResponse, Response


@agent_app.post("/api/prices")
async def api_prices(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        return JSONResponse(_price_payload(payload))
    except UserVisibleError as exc:
        return JSONResponse(exc.payload, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@agent_app.post("/api/backtest-jobs")
async def api_backtest_jobs(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        return JSONResponse({"job": JOB_STORE.create(payload)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@agent_app.get("/api/backtest-jobs/latest")
async def api_latest_job() -> JSONResponse:
    return JSONResponse({"job": JOB_STORE.latest()})


@agent_app.get("/api/backtest-jobs/{job_id}")
async def api_get_job(job_id: str) -> JSONResponse:
    job = JOB_STORE.get(job_id)
    if not job:
        return JSONResponse({"error": "回测任务不存在"}, status_code=404)
    return JSONResponse({"job": job})


@agent_app.get("/api/backtest-jobs/{job_id}/stream")
async def api_job_stream(job_id: str, request: Request) -> Response:
    job = JOB_STORE.get(job_id)
    if not job:
        return JSONResponse({"error": "回测任务不存在"}, status_code=404)

    async def generate():
        last_progress = None
        while True:
            job = JOB_STORE.get(job_id)
            if not job:
                yield json.dumps({"type": "error", "payload": {"error": "回测任务不存在"}}, ensure_ascii=False) + "\n"
                return
            progress = job.get("progress")
            progress_text = json.dumps(progress, ensure_ascii=False, sort_keys=True) if progress else ""
            if progress and progress_text != last_progress:
                yield json.dumps(progress, ensure_ascii=False) + "\n"
                last_progress = progress_text
            if job["status"] == "completed":
                yield json.dumps({"type": "result", "payload": job["result"], "job_id": job_id}, ensure_ascii=False) + "\n"
                return
            if job["status"] in {"failed", "interrupted"}:
                yield json.dumps({"type": "error", "payload": job.get("error") or {"error": "回测任务失败"}, "job_id": job_id}, ensure_ascii=False) + "\n"
                return
            await asyncio.sleep(0.8)

    return Response(generate(), media_type="application/x-ndjson; charset=utf-8")


@agent_app.get("/api/markets")
async def api_markets() -> JSONResponse:
    return JSONResponse({"markets": _list_markets()})


@agent_app.get("/reports/{path:path}")
async def serve_report(path: str) -> Response:
    if ".." in path:
        return JSONResponse({"error": "路径不合法"}, status_code=403)
    file_path = (REPORTS_ROOT / path).resolve()
    if not str(file_path).startswith(str(REPORTS_ROOT.resolve())):
        return JSONResponse({"error": "路径不合法"}, status_code=403)
    if not file_path.is_file():
        return JSONResponse({"error": "报告不存在"}, status_code=404)
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return Response(file_path.read_bytes(), media_type=mime_type or "application/octet-stream")


# ── 回测辅助函数 ────────────────────────────────────────────────────────────

def _price_payload(payload: dict[str, Any]) -> dict[str, Any]:
    market, years, resample = _request_data_options(payload)
    bars = _load_or_raise(market, years, resample)
    return {
        "market": market, "years": years, "resample": resample,
        "prices": [_bar_to_dict(bar) for bar in bars],
    }


def _backtest_payload(payload: dict[str, Any], progress_callback=None, include_prices: bool = True) -> dict[str, Any]:
    market, years, resample = _request_data_options(payload)
    if progress_callback:
        progress_callback({"type": "progress", "phase": "加载数据", "ratio": 0.02, "index": 0, "total": 0})
    bars = _load_or_raise(market, years, resample)
    if progress_callback:
        progress_callback({"type": "progress", "phase": "准备回测", "ratio": 0.08, "index": 0, "total": len(bars)})
    engine_config = payload.get("engine", {})
    strategy_config = payload.get("strategy", {})
    strategy_name = str(strategy_config.get("name", "sma_cross"))

    engine = BacktestEngine(
        initial_cash=float(engine_config.get("capital", 100000)),
        commission_rate=float(engine_config.get("commission", 0.0005)),
        slippage_bps=float(engine_config.get("slippage_bps", 1.0)),
        auto_replenish=bool(engine_config.get("auto_replenish", True)),
        replenish_amount=float(engine_config.get("replenish_amount", engine_config.get("capital", 100000))),
        zero_equity_threshold=float(engine_config.get("zero_equity_threshold", max(1.0, float(engine_config.get("capital", 100000)) * 0.00001))),
    )
    strategy = create_strategy(strategy_name, **{k: v for k, v in strategy_config.items() if k != "name"})

    def on_engine_progress(index: int, total: int, bar) -> None:
        if progress_callback is None:
            return
        progress_callback({
            "type": "progress", "phase": "回测中",
            "ratio": 0.08 + (index / max(1, total - 1)) * 0.88,
            "index": index, "total": total, "date": bar.date.isoformat(sep=" "),
        })

    result = engine.run(bars, strategy, progress_callback=on_engine_progress)
    if progress_callback:
        progress_callback({"type": "progress", "phase": "整理结果", "ratio": 0.98, "index": len(bars) - 1, "total": len(bars)})

    report_dir = make_report_path(strategy_name, years)
    report_path = write_html_report(result, report_dir / "report.html", title="AI 智能回测报告")
    write_csv_exports(result, report_dir)
    report_url = f"/reports/{report_path.relative_to(REPORTS_ROOT).as_posix()}"

    response: dict[str, Any] = {
        "market": market, "years": years, "resample": resample,
        "bar_count": len(bars),
        "start_date": bars[0].date.isoformat(sep=" ") if bars else "",
        "end_date": bars[-1].date.isoformat(sep=" ") if bars else "",
        "report_url": report_url,
        "markers": _trade_markers(result),
        "capital_events": [_capital_event_to_dict(e) for e in result.capital_events],
        "orders": [_order_to_dict(o) for o in result.orders],
        "trades": [_trade_to_dict(t) for t in result.round_trips],
        "metrics": [{"key": k, "label": METRIC_LABELS.get(k, k), "value": v} for k, v in result.metrics.items()],
    }
    if include_prices:
        response["prices"] = [_bar_to_dict(bar) for bar in bars]
    return response


def _request_data_options(payload: dict[str, Any]) -> tuple[str, list[int], str]:
    market = str(payload.get("market", "")).strip()
    if not market:
        raise UserVisibleError({"error": "请选择币种"})
    if "/" in market or "\\" in market or market in {".", ".."}:
        raise UserVisibleError({"error": "币种名称不合法"})
    years = sorted({int(year) for year in payload.get("years", [])})
    if not years:
        raise UserVisibleError({"error": "请选择年份"})
    resample = str(payload.get("resample", "daily"))
    return market, years, resample


def _load_or_raise(market: str, years: list[int], resample: str):
    cache_key = (market, tuple(years), resample)
    if cache_key in BARS_CACHE:
        return BARS_CACHE[cache_key]
    data_dir = DATA_ROOT / market
    year_files = discover_year_files(data_dir)
    missing = [year for year in years if year not in year_files]
    if missing:
        available = sorted(year_files)
        usable = [year for year in years if year in year_files]
        max_years = longest_contiguous_years(usable)
        raise UserVisibleError({
            "error": "现有数据不满足配置年份要求",
            "requested_years": years, "available_years": available,
            "missing_years": missing, "max_contiguous_years": max_years,
        })
    bars = load_year_csvs(data_dir, years, resample=resample)
    BARS_CACHE[cache_key] = bars
    return bars


def _list_markets() -> list[dict[str, Any]]:
    if not DATA_ROOT.exists():
        return []
    markets = []
    for item in sorted(DATA_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir():
            continue
        year_files = discover_year_files(item)
        if not year_files:
            continue
        markets.append({"id": item.name, "name": item.name.upper(), "years": sorted(year_files)})
    return markets


# ── 数据转换辅助 ────────────────────────────────────────────────────────────

def _bar_to_dict(bar) -> dict:
    return {"date": bar.date.isoformat(sep=" "), "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume}

def _order_to_dict(order) -> dict:
    return {"date": order.date.isoformat(sep=" "), "side": "买入" if order.side == "BUY" else "卖出", "price": order.price, "shares": order.shares, "value": order.value, "commission": order.commission}

def _trade_to_dict(trade) -> dict:
    return {"entry_date": trade.entry_date.isoformat(sep=" "), "exit_date": trade.exit_date.isoformat(sep=" "), "entry_price": trade.entry_price, "exit_price": trade.exit_price, "shares": trade.shares, "pnl": trade.pnl, "return_pct": trade.return_pct, "status": "盈利" if trade.pnl >= 0 else "亏损"}

def _capital_event_to_dict(event) -> dict:
    is_zero = event.event_type == "zero"
    return {"date": event.date.isoformat(sep=" "), "type": event.event_type, "label": "资金归零" if is_zero else "资金补充", "amount": event.amount, "equity_before": event.equity_before, "equity_after": event.equity_after, "color": "#ef4444" if is_zero else "#f5b700"}

def _trade_markers(result) -> list[dict]:
    markers = []
    for idx, trade in enumerate(result.round_trips, start=1):
        profit = trade.pnl >= 0
        markers.append({"date": trade.entry_date.isoformat(sep=" "), "price": trade.entry_price, "type": "entry", "status": "开仓", "trade_index": idx, "label": f"第 {idx} 笔开仓", "pnl": trade.pnl, "return_pct": trade.return_pct, "color": "#f5b700"})
        markers.append({"date": trade.exit_date.isoformat(sep=" "), "price": trade.exit_price, "type": "exit", "status": "盈利" if profit else "亏损", "trade_index": idx, "label": f"第 {idx} 笔平仓", "pnl": trade.pnl, "return_pct": trade.return_pct, "color": "#22c55e" if profit else "#ef4444"})
    return markers


# ── 静态文件 ────────────────────────────────────────────────────────────────

if WEB_ROOT.is_dir():
    agent_app.mount("/", StaticFiles(directory=str(WEB_ROOT), html=True), name="static")


# ── 主入口 ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AI 智能回测 + Agent 集成服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    print(f"AI 智能回测 + Agent 集成服务器")
    print(f"回测前端: http://{args.host}:{args.port}")
    print(f"Agent 对话: 前端右侧面板")
    if not AGENT_AVAILABLE:
        print("⚠ Agent 依赖未安装，对话功能不可用。运行: pip install -r requirements-agent.txt")

    uvicorn.run(agent_app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
