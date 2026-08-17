"""DeepSeek JSON Output 适配器和密钥安全测试。"""

import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.agent.config import AgentSettings
from app.agent.llm import (
    DeepSeekAgentLLM,
    DisabledAgentLLM,
    LLMUnavailableError,
    create_agent_llm,
)
from app.agent.models import AnalysisIntent


class FakeCompletions:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content=self.content, reasoning_content="private reasoning")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, content: str | None) -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_provider="deepseek",
        DEEPSEEK_API_KEY=SecretStr("ds-secret-key"),
    )


def test_deepseek_parser_uses_json_mode_without_exposing_reasoning() -> None:
    client = FakeClient(
        json.dumps(
            {
                "intent": "analyze_material_root_cause",
                "parameters": {
                    "material_id": "MAT-SYN-MULTI",
                    "warehouse_id": None,
                    "as_of_date": None,
                    "category": None,
                },
            }
        )
    )
    llm = DeepSeekAgentLLM(_settings(), client=client)

    parsed = llm.parse("分析 MAT-SYN-MULTI")

    assert parsed.intent is AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
    assert parsed.parameters.material_id == "MAT-SYN-MULTI"
    assert client.completions.kwargs["response_format"] == {"type": "json_object"}
    assert client.completions.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "private reasoning" not in parsed.model_dump_json()


def test_empty_model_content_maps_to_stable_unavailable_error() -> None:
    llm = DeepSeekAgentLLM(_settings(), client=FakeClient(None))

    with pytest.raises(LLMUnavailableError, match="llm_empty_content"):
        llm.parse("分析库存")


def test_settings_mask_secret_and_without_key_selects_disabled_llm() -> None:
    settings = _settings()
    disabled = AgentSettings(_env_file=None, llm_provider="deepseek")

    assert "ds-secret-key" not in repr(settings)
    assert "ds-secret-key" not in settings.model_dump_json()
    assert isinstance(create_agent_llm(disabled), DisabledAgentLLM)


@pytest.mark.parametrize("api_key", ["", "   "])
def test_blank_api_key_selects_disabled_llm(api_key: str) -> None:
    """Compose 等运行环境传入空白密钥时仍应安全降级。"""
    settings = AgentSettings(
        _env_file=None,
        llm_provider="deepseek",
        DEEPSEEK_API_KEY=SecretStr(api_key),
    )

    assert settings.llm_available is False
    assert isinstance(create_agent_llm(settings), DisabledAgentLLM)
