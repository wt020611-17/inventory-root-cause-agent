"""可替换的 DeepSeek LLM 适配器；只负责解析和展示摘要。"""

import json
from typing import Protocol

from pydantic import ValidationError

from app.agent.config import AgentSettings
from app.agent.models import GeneratedSummary, ParsedRequest


class LLMUnavailableError(RuntimeError):
    """模型不可用或返回不符合契约时使用的稳定异常。"""


class AgentLLM(Protocol):
    """工作流依赖的最小模型接口，便于单元测试注入 Mock。"""

    @property
    def available(self) -> bool: ...

    def parse(self, question: str) -> ParsedRequest: ...

    def summarize(self, result_json: str) -> GeneratedSummary: ...


class DisabledAgentLLM:
    """无密钥时显式进入降级路径。"""

    @property
    def available(self) -> bool:
        return False

    def parse(self, question: str) -> ParsedRequest:
        del question
        raise LLMUnavailableError("llm_unavailable")

    def summarize(self, result_json: str) -> GeneratedSummary:
        del result_json
        raise LLMUnavailableError("llm_unavailable")


class DeepSeekAgentLLM:
    """通过 OpenAI-compatible SDK 调用 DeepSeek JSON Output。"""

    def __init__(self, settings: AgentSettings, client: object | None = None) -> None:
        if not settings.llm_available:
            raise ValueError("DeepSeek API key is required")
        self._settings = settings
        self._client = client or self._create_client(settings)

    @staticmethod
    def _create_client(settings: AgentSettings) -> object:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMUnavailableError("llm_sdk_unavailable") from exc
        return OpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    @property
    def available(self) -> bool:
        return True

    def parse(self, question: str) -> ParsedRequest:
        schema = ParsedRequest.model_json_schema()
        payload = self._call_json(
            system=(
                "你是库存分析请求解析器。只输出 json，不输出推理过程。"
                "仅提取用户原文明确出现的物料ID、仓库ID、日期、类别；不得猜测ID。"
                f"输出必须符合此 JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
            ),
            user=question,
        )
        try:
            return ParsedRequest.model_validate(payload)
        except ValidationError as exc:
            raise LLMUnavailableError("llm_invalid_parse") from exc

    def summarize(self, result_json: str) -> GeneratedSummary:
        schema = GeneratedSummary.model_json_schema()
        payload = self._call_json(
            system=(
                "你是库存分析结果展示器。只输出 json，不输出推理过程。"
                "不得修改、补充或猜测结构化结果中的任何数字和事实。"
                f"输出必须符合此 JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
            ),
            user=result_json,
        )
        try:
            return GeneratedSummary.model_validate(payload)
        except ValidationError as exc:
            raise LLMUnavailableError("llm_invalid_summary") from exc

    def _call_json(self, *, system: str, user: str) -> dict[str, object]:
        try:
            response = self._client.chat.completions.create(
                model=self._settings.llm_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=1000,
                extra_body={"thinking": {"type": "disabled"}},
            )
            content = response.choices[0].message.content
            if not content:
                raise LLMUnavailableError("llm_empty_content")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise LLMUnavailableError("llm_invalid_json")
            return parsed
        except LLMUnavailableError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError, IndexError) as exc:
            raise LLMUnavailableError("llm_invalid_response") from exc
        except Exception as exc:
            raise LLMUnavailableError("llm_request_failed") from exc


def create_agent_llm(settings: AgentSettings) -> AgentLLM:
    """有密钥时创建 DeepSeek 适配器，否则返回显式禁用实现。"""
    if not settings.llm_available:
        return DisabledAgentLLM()
    return DeepSeekAgentLLM(settings)
