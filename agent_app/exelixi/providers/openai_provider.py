from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from exelixi.core.env import load_env


CONTENT_RISK_MARKERS = (
    "content exists risk",
    "content_exists_risk",
)


def create_model() -> ChatOpenAI:
    load_env()

    api_key = os.getenv("API_KEY")
    model = os.getenv("MODEL")
    base_url = os.getenv("BASE_URL")

    missing = [name for name, value in {"API_KEY": api_key, "MODEL": model, "BASE_URL": base_url}.items() if not value]
    if missing:
        raise RuntimeError(f"missing required .env setting(s): {', '.join(missing)}")

    return ChatOpenAI(
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=0,
    )


def is_content_risk_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in CONTENT_RISK_MARKERS)


def safe_model_error(exc: BaseException) -> str:
    if is_content_risk_error(exc):
        return "模型供应商触发了内容风险保护，已改用降级结果，避免工作流卡死。"
    return f"{type(exc).__name__}: {exc}"
