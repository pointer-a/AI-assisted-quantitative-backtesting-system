from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path

from .models import Bar


REQUIRED_COLUMNS = {"date", "open", "high", "low", "close"}
DATE_ALIASES = ("date", "datetime", "time", "timestamp", "candle_begin_time", "open_time")
YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")


def load_csv(path: str | Path, resample: str = "none") -> list[Bar]:
    """Load OHLCV rows from a CSV file.

    Accepted headers are case-insensitive. Required columns:
    date, open, high, low, close. Volume is optional.
    """
    file_path = Path(path)
    bars = _load_csv_with_fallback_encodings(file_path)

    bars.sort(key=lambda item: item.date)
    bars = resample_bars(bars, resample)
    if len(bars) < 30:
        raise ValueError("At least 30 bars are recommended for a meaningful backtest")
    return bars


def _load_csv_with_fallback_encodings(file_path: Path) -> list[Bar]:
    encodings = ("utf-8-sig", "utf-8", "gbk", "cp936")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            with file_path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                header = _find_header_row(reader, file_path)
                field_map = {name.lower().strip(): name for name in header}
                date_column = _find_column(field_map, DATE_ALIASES)
                missing = {"open", "high", "low", "close"}.difference(field_map)
                if date_column is None:
                    missing.add("date")
                if missing:
                    missing_text = ", ".join(sorted(missing))
                    raise ValueError(f"{file_path} is missing columns: {missing_text}")

                bars: list[Bar] = []
                for row_number, values in enumerate(reader, start=2):
                    if not values or all(not value.strip() for value in values):
                        continue
                    row = dict(zip(header, values))
                    try:
                        bars.append(
                            Bar(
                                date=_parse_date(row[date_column]),
                                open=float(row[field_map["open"]]),
                                high=float(row[field_map["high"]]),
                                low=float(row[field_map["low"]]),
                                close=float(row[field_map["close"]]),
                                volume=float(row.get(field_map.get("volume", ""), 0) or 0),
                            )
                        )
                    except Exception as exc:
                        raise ValueError(f"Invalid row {row_number} in {file_path}: {exc}") from exc
                return bars
        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"无法识别文件编码：{file_path}。已尝试 {', '.join(encodings)}",
    ) from last_error


def _find_header_row(reader: csv.reader, file_path: Path) -> list[str]:
    for _ in range(20):
        try:
            row = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{file_path} has no header row") from exc
        normalized = {item.lower().strip() for item in row}
        has_date = any(alias in normalized for alias in DATE_ALIASES)
        has_prices = {"open", "high", "low", "close"}.issubset(normalized)
        if has_date and has_prices:
            return row
    raise ValueError(f"{file_path} 前20行未找到有效表头")


def discover_year_files(path: str | Path) -> dict[int, list[Path]]:
    """Find CSV files by year from a file or directory path."""
    source = Path(path)
    if source.is_file():
        year = extract_year_from_name(source.name)
        return {year: [source]} if year is not None else {}
    if not source.exists():
        raise FileNotFoundError(f"找不到行情路径：{source}")
    if not source.is_dir():
        raise ValueError(f"行情路径必须是 CSV 文件或目录：{source}")

    files = sorted(source.glob("*.csv"))
    if not files:
        files = sorted(source.rglob("*.csv"))

    year_files: dict[int, list[Path]] = {}
    for file_path in files:
        year = extract_year_from_name(file_path.name)
        if year is not None:
            year_files.setdefault(year, []).append(file_path)
    return year_files


def extract_year_from_name(name: str) -> int | None:
    match = YEAR_PATTERN.search(name)
    return int(match.group(0)) if match else None


def load_year_csvs(path: str | Path, years: list[int], resample: str = "none") -> list[Bar]:
    year_files = discover_year_files(path)
    missing = [year for year in years if year not in year_files]
    if missing:
        missing_text = ", ".join(str(year) for year in missing)
        raise ValueError(f"缺少年份数据文件：{missing_text}")

    bars: list[Bar] = []
    for year in years:
        for file_path in year_files[year]:
            bars.extend(load_csv(file_path, resample="none"))

    bars = filter_bars_by_years(bars, years)
    bars.sort(key=lambda item: item.date)
    bars = _dedupe_bars(bars)
    bars = resample_bars(bars, resample)
    if len(bars) < 30:
        raise ValueError("拼合后的K线数量少于30，无法进行有意义的回测")
    return bars


def filter_bars_by_years(bars: list[Bar], years: list[int]) -> list[Bar]:
    year_set = set(years)
    return [bar for bar in bars if _to_datetime(bar.date).year in year_set]


def longest_contiguous_years(years: list[int]) -> list[int]:
    unique_years = sorted(set(years))
    if not unique_years:
        return []

    best: list[int] = []
    current: list[int] = []
    previous = None
    for year in unique_years:
        if previous is None or year == previous + 1:
            current.append(year)
        else:
            if len(current) > len(best):
                best = current
            current = [year]
        previous = year
    if len(current) > len(best):
        best = current
    return best


def split_bars(bars: list[Bar], train_ratio: float = 0.7) -> tuple[list[Bar], list[Bar]]:
    if not 0.1 <= train_ratio <= 0.9:
        raise ValueError("train_ratio must be between 0.1 and 0.9")
    split_index = max(2, min(len(bars) - 2, int(len(bars) * train_ratio)))
    return bars[:split_index], bars[split_index:]


def _dedupe_bars(bars: list[Bar]) -> list[Bar]:
    deduped: dict[datetime, Bar] = {}
    for bar in bars:
        deduped[_to_datetime(bar.date)] = bar
    return [deduped[key] for key in sorted(deduped)]


def resample_bars(bars: list[Bar], timeframe: str = "none") -> list[Bar]:
    normalized = timeframe.strip().lower()
    if normalized in {"", "none", "raw"}:
        return bars
    if normalized not in {"daily", "1d", "hourly", "1h"}:
        raise ValueError("resample must be one of: none, daily, hourly")

    grouped: dict[datetime, list[Bar]] = {}
    for bar in bars:
        moment = _to_datetime(bar.date)
        if normalized in {"daily", "1d"}:
            key = datetime(moment.year, moment.month, moment.day)
        else:
            key = datetime(moment.year, moment.month, moment.day, moment.hour)
        grouped.setdefault(key, []).append(bar)

    output: list[Bar] = []
    for key in sorted(grouped):
        group = grouped[key]
        output.append(
            Bar(
                date=key,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum(item.volume for item in group),
            )
        )
    return output


def _parse_date(value: str) -> datetime:
    text = value.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text)


def _find_column(field_map: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in field_map:
            return field_map[alias]
    return None


def _to_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day)
