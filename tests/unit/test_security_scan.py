"""安全扫描识别真实形态并允许明确占位符。"""

from pathlib import Path

from scripts.security_scan import repository_files, scan_repository, scan_text


def test_scan_text_detects_secret_internal_url_and_enterprise_marker() -> None:
    key_name = "DEEPSEEK_" + "API_KEY"
    token = "sk-" + "a" * 24
    internal_url = "https://" + "erp.internal.example.com/api"
    enterprise_marker = "king" + "dee.com"
    text = "\n".join(
        (
            f"{key_name}={token}",
            internal_url,
            enterprise_marker,
        )
    )

    rules = {issue.rule for issue in scan_text(".env", text)}

    assert "nonempty_secret_assignment" in rules
    assert "openai_style_token" in rules
    assert "internal_url" in rules
    assert "enterprise_identifier" in rules


def test_scan_text_allows_empty_and_documented_placeholders() -> None:
    key_name = "DEEPSEEK_" + "API_KEY"
    text = f"{key_name}=\n{key_name}=你的密钥\n{key_name}=<your-key>"

    assert scan_text(".env.example", text) == []


def test_current_repository_passes_and_tracks_only_env_example() -> None:
    root = Path(__file__).parents[2]
    _, tracked_env = repository_files(root)

    assert tracked_env == [".env.example"]
    assert scan_repository(root) == []
