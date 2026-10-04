"""config.py：env 装配 fail-fast——树锚点 M04.F01.I01。

所有测试必须显式传 env_file 指向不存在的文件做隔离——
项目根有真 .env（live 凭据），默认 cwd/.env 会污染测试。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hr_interview.config import load_settings


@pytest.fixture()
def no_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def test_missing_mode_raises(no_env_file):
    with pytest.raises(ValueError, match="LLM_MODE"):
        load_settings(environ={}, env_file=no_env_file)


def test_bad_mode_raises(no_env_file):
    with pytest.raises(ValueError, match="mock.*live|live.*mock"):
        load_settings(environ={"LLM_MODE": "yolo"}, env_file=no_env_file)


def test_live_requires_all_three(no_env_file):
    with pytest.raises(ValueError) as ei:
        load_settings(
            environ={
                "LLM_MODE": "live",
                "LLM_BASE_URL": "https://api.example.com/v1",
            },
            env_file=no_env_file,
        )
    msg = str(ei.value)
    assert "LLM_API_KEY" in msg and "LLM_MODEL" in msg


def test_live_ok(no_env_file):
    s = load_settings(
        environ={
            "LLM_MODE": "live",
            "LLM_BASE_URL": "https://api.minimaxi.com/v1",
            "LLM_API_KEY": "sk-test",
            "LLM_MODEL": "MiniMax-M3",
        },
        env_file=no_env_file,
    )
    assert s.llm_mode == "live"
    assert s.llm_model == "MiniMax-M3"
    assert s.db_path == "data/app.db"  # 运行性默认（非密钥兜底）
    assert s.app_port == 8802


def test_mock_needs_nothing_else(no_env_file):
    s = load_settings(environ={"LLM_MODE": "mock"}, env_file=no_env_file)
    assert s.llm_mode == "mock"
    assert s.llm_base_url == ""


def test_env_file_loaded_but_real_env_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_MODE=live\nLLM_BASE_URL=https://file.example/v1\n"
        "LLM_API_KEY=sk-file\nLLM_MODEL=File-Model\n",
        encoding="utf-8",
    )
    s = load_settings(environ={"LLM_MODE": "mock"}, env_file=env_file)
    assert s.llm_mode == "mock"  # 真实 env 优先于 .env 文件

    s2 = load_settings(environ={}, env_file=env_file)
    assert s2.llm_mode == "live"
    assert s2.llm_base_url == "https://file.example/v1"


def test_env_file_ignores_junk_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nLLM_MODE=mock\nBAD LINE WITHOUT EQUALS\n  APP_PORT=9001 \n",
        encoding="utf-8",
    )
    s = load_settings(environ={}, env_file=env_file)
    assert s.llm_mode == "mock"
    assert s.app_port == 9001


def test_real_env_beats_file_for_same_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODE=mock\nAPP_PORT=9001\n", encoding="utf-8")
    s = load_settings(environ={"APP_PORT": "9002"}, env_file=env_file)
    assert s.app_port == 9002
