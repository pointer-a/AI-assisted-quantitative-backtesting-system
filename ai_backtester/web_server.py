from __future__ import annotations

import argparse
import json
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .data import discover_year_files, load_year_csvs, longest_contiguous_years
from .engine import BacktestEngine
from .jobs import BacktestJobStore
from .report import METRIC_LABELS
from .strategies import create_strategy


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"
DATA_ROOT = PROJECT_ROOT / "数据"
BARS_CACHE: dict[tuple[str, tuple[int, ...], str], list] = {}
JOB_STORE = BacktestJobStore(
    PROJECT_ROOT / "reports" / "backtest_jobs.sqlite",
    lambda payload, progress_callback: _backtest_payload(payload, progress_callback=progress_callback, include_prices=False),
)


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/markets":
            self._send_json({"markets": _list_markets()})
            return
        if parsed.path == "/api/backtest-jobs/latest":
            self._send_json({"job": JOB_STORE.latest()})
            return
        if parsed.path.startswith("/api/backtest-jobs/") and parsed.path.endswith("/stream"):
            job_id = unquote(parsed.path.removeprefix("/api/backtest-jobs/").removesuffix("/stream").strip("/"))
            self._send_job_stream(job_id)
            return
        if parsed.path.startswith("/api/backtest-jobs/"):
            job_id = unquote(parsed.path.removeprefix("/api/backtest-jobs/").strip("/"))
            job = JOB_STORE.get(job_id)
            if not job:
                self._send_json({"error": "回测任务不存在"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"job": job})
            return
        if parsed.path.startswith("/api/"):
            self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/prices":
                self._send_json(_price_payload(payload))
            elif parsed.path == "/api/backtest":
                self._send_json(_backtest_payload(payload))
            elif parsed.path == "/api/backtest-stream":
                self._send_backtest_stream(payload)
            elif parsed.path == "/api/backtest-jobs":
                self._send_json({"job": JOB_STORE.create(payload)})
            else:
                self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
        except UserVisibleError as exc:
            self._send_json(exc.payload, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_backtest_stream(self, payload: dict[str, Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send_event(event: dict[str, Any]) -> None:
            data = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
            self.wfile.write(data)
            self.wfile.flush()

        try:
            result = _backtest_payload(payload, progress_callback=lambda event: send_event(event), include_prices=False)
            send_event({"type": "result", "payload": result})
        except UserVisibleError as exc:
            send_event({"type": "error", "payload": exc.payload})
        except Exception as exc:
            send_event({"type": "error", "payload": {"error": str(exc)}})

    def _send_job_stream(self, job_id: str) -> None:
        job = JOB_STORE.get(job_id)
        if not job:
            self._send_json({"error": "回测任务不存在"}, status=HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send_event(event: dict[str, Any]) -> bool:
            try:
                data = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        last_progress = None
        while True:
            job = JOB_STORE.get(job_id)
            if not job:
                send_event({"type": "error", "payload": {"error": "回测任务不存在"}})
                return

            progress = job.get("progress")
            progress_text = json.dumps(progress, ensure_ascii=False, sort_keys=True) if progress else ""
            if progress and progress_text != last_progress:
                if not send_event(progress):
                    return
                last_progress = progress_text

            if job["status"] == "completed":
                send_event({"type": "result", "payload": job["result"], "job_id": job_id})
                return
            if job["status"] in {"failed", "interrupted"}:
                send_event({"type": "error", "payload": job.get("error") or {"error": "回测任务失败"}, "job_id": job_id})
                return

            time.sleep(0.8)


class UserVisibleError(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("error", "请求失败"))
        self.payload = payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动 AI 智能回测 Web 前端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--quiet", action="store_true", help="静默启动，不输出提示")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), WebHandler)
    if not args.quiet:
        print(f"Web 前端已启动：http://{args.host}:{args.port}")
        print("按 Ctrl+C 停止服务")
    server.serve_forever()


def _list_markets() -> list[dict[str, Any]]:
    if not DATA_ROOT.exists():
        return []
    markets = []
    for item in sorted(DATA_ROOT.iterdir(), key=lambda path: path.name.lower()):
        if not item.is_dir():
            continue
        year_files = discover_year_files(item)
        if not year_files:
            continue
        markets.append(
            {
                "id": item.name,
                "name": item.name.upper(),
                "years": sorted(year_files),
            }
        )
    return markets


def _price_payload(payload: dict[str, Any]) -> dict[str, Any]:
    market, years, resample = _request_data_options(payload)
    bars = _load_or_raise(market, years, resample)
    return {
        "market": market,
        "years": years,
        "resample": resample,
        "prices": [_bar_to_dict(bar) for bar in bars],
    }


def _backtest_payload(payload: dict[str, Any], progress_callback=None, include_prices: bool = True) -> dict[str, Any]:
    market, years, resample = _request_data_options(payload)
    if progress_callback is not None:
        progress_callback({"type": "progress", "phase": "加载数据", "ratio": 0.02, "index": 0, "total": 0})
    bars = _load_or_raise(market, years, resample)
    if progress_callback is not None:
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
    strategy = create_strategy(strategy_name, **{key: value for key, value in strategy_config.items() if key != "name"})
    def on_engine_progress(index: int, total: int, bar) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "type": "progress",
                "phase": "回测中",
                "ratio": 0.08 + (index / max(1, total - 1)) * 0.88,
                "index": index,
                "total": total,
                "date": bar.date.isoformat(sep=" "),
            }
        )

    result = engine.run(bars, strategy, progress_callback=on_engine_progress)
    if progress_callback is not None:
        progress_callback({"type": "progress", "phase": "整理结果", "ratio": 0.98, "index": len(bars) - 1, "total": len(bars)})

    response = {
        "market": market,
        "years": years,
        "resample": resample,
        "bar_count": len(bars),
        "start_date": bars[0].date.isoformat(sep=" ") if bars else "",
        "end_date": bars[-1].date.isoformat(sep=" ") if bars else "",
        "markers": _trade_markers(result),
        "capital_events": [_capital_event_to_dict(event) for event in result.capital_events],
        "orders": [_order_to_dict(order) for order in result.orders],
        "trades": [_trade_to_dict(trade) for trade in result.round_trips],
        "metrics": [{"key": key, "label": METRIC_LABELS.get(key, key), "value": value} for key, value in result.metrics.items()],
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
        raise UserVisibleError(
            {
                "error": "现有数据不满足配置年份要求",
                "requested_years": years,
                "available_years": available,
                "missing_years": missing,
                "max_contiguous_years": max_years,
            }
        )
    bars = load_year_csvs(data_dir, years, resample=resample)
    BARS_CACHE[cache_key] = bars
    return bars


def _bar_to_dict(bar) -> dict[str, Any]:
    return {
        "date": bar.date.isoformat(sep=" "),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _order_to_dict(order) -> dict[str, Any]:
    return {
        "date": order.date.isoformat(sep=" "),
        "side": "买入" if order.side == "BUY" else "卖出",
        "price": order.price,
        "shares": order.shares,
        "value": order.value,
        "commission": order.commission,
    }


def _trade_to_dict(trade) -> dict[str, Any]:
    status = "盈利" if trade.pnl >= 0 else "亏损"
    return {
        "entry_date": trade.entry_date.isoformat(sep=" "),
        "exit_date": trade.exit_date.isoformat(sep=" "),
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "shares": trade.shares,
        "pnl": trade.pnl,
        "return_pct": trade.return_pct,
        "status": status,
    }


def _capital_event_to_dict(event) -> dict[str, Any]:
    is_zero = event.event_type == "zero"
    return {
        "date": event.date.isoformat(sep=" "),
        "type": event.event_type,
        "label": "资金归零" if is_zero else "资金补充",
        "amount": event.amount,
        "equity_before": event.equity_before,
        "equity_after": event.equity_after,
        "color": "#ef4444" if is_zero else "#f5b700",
    }


def _trade_markers(result) -> list[dict[str, Any]]:
    markers = []
    for index, trade in enumerate(result.round_trips, start=1):
        profit = trade.pnl >= 0
        markers.append(
            {
                "date": trade.entry_date.isoformat(sep=" "),
                "price": trade.entry_price,
                "type": "entry",
                "status": "开仓",
                "trade_index": index,
                "label": f"第 {index} 笔开仓",
                "pnl": trade.pnl,
                "return_pct": trade.return_pct,
                "color": "#f5b700",
            }
        )
        markers.append(
            {
                "date": trade.exit_date.isoformat(sep=" "),
                "price": trade.exit_price,
                "type": "exit",
                "status": "盈利" if profit else "亏损",
                "trade_index": index,
                "label": f"第 {index} 笔平仓",
                "pnl": trade.pnl,
                "return_pct": trade.return_pct,
                "color": "#22c55e" if profit else "#ef4444",
            }
        )
    return markers
