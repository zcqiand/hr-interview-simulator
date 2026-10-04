"""配置装配（M04.F01.I01）：fail-fast，无默认兜底。

LLM_MODE 必填（mock|live）；live 模式下 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 缺一即拒绝启动。
优先级：真实环境变量 > .env 文件。密钥只放 .env，不入库。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    llm_mode: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    db_path: str
    app_port: int


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_settings(
    environ: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> Settings:
    env = dict(os.environ if environ is None else environ)
    if env_file is None:
        env_file = Path.cwd() / ".env"
    merged = {**_parse_env_file(Path(env_file)), **{k: v for k, v in env.items() if v != ""}}

    mode = merged.get("LLM_MODE", "")
    if not mode:
        raise ValueError(
            "缺少 LLM_MODE（必填：mock | live）。"
            "请复制 .env.example 为 .env 并填写。本项目禁止 env 默认值兜底。"
        )
    if mode not in ("mock", "live"):
        raise ValueError(f"LLM_MODE 必须是 mock 或 live，当前为: {mode}")

    base_url = merged.get("LLM_BASE_URL", "")
    api_key = merged.get("LLM_API_KEY", "")
    model = merged.get("LLM_MODEL", "")
    if mode == "live":
        missing = [
            name
            for name, val in (
                ("LLM_BASE_URL", base_url),
                ("LLM_API_KEY", api_key),
                ("LLM_MODEL", model),
            )
            if not val
        ]
        if missing:
            raise ValueError(f"LLM_MODE=live 需要完整配置，缺少: {', '.join(missing)}（见 .env.example）")

    return Settings(
        llm_mode=mode,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_model=model,
        db_path=merged.get("APP_DB_PATH", "data/app.db"),
        app_port=int(merged.get("APP_PORT", "8802")),
    )
