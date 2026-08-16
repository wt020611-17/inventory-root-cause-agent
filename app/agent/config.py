"""DeepSeek 与 Agent 运行时配置。"""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """从环境变量读取配置；密钥始终保持为 SecretStr。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["deepseek", "disabled"] = "deepseek"
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "LLM_API_KEY"),
    )
    llm_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("DEEPSEEK_MODEL", "LLM_MODEL"),
    )
    llm_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("DEEPSEEK_BASE_URL", "LLM_BASE_URL"),
    )
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    agent_max_retries: int = Field(default=1, ge=0, le=3)
    agent_max_steps: int = Field(default=12, ge=3, le=50)
    session_ttl_seconds: int = Field(default=1800, ge=1, le=86400)
    session_max_turns: int = Field(default=4, ge=1, le=50)
    system_prompt_version: str = "inventory-agent-system-v1"
    tool_schema_version: str = "inventory-tools-v1"

    @property
    def llm_available(self) -> bool:
        return self.llm_provider == "deepseek" and self.llm_api_key is not None
