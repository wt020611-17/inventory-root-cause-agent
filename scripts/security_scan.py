"""扫描仓库可见文件中的密钥形态、内部标识和意外环境文件。"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PRIVATE_KEY_PATTERN = re.compile(r"-{5}BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-{5}")
_TOKEN_PATTERNS = (
    ("openai_style_token", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
)
_ENV_ASSIGNMENT = re.compile(
    r"\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))\s*=\s*([^\s#]+)"
)
_INTERNAL_URL = re.compile(r"(?i)https?://[^\s/]*(?:corp|internal)(?:[.:/]|$)")
_ABSOLUTE_USER_PATH = re.compile(r"(?i)(?:[A-Z]:\\Users\\|/home/[^/\s]+/)")
_ENTERPRISE_MARKERS = ("king" + "dee.com", "金蝶" + "内部")
_PLACEHOLDER_PREFIXES = ("your", "example", "placeholder", "replace", "<", "${", "你的")
_ALLOWED_ENV_FILES = {".env.example"}


@dataclass(frozen=True, slots=True)
class SecurityIssue:
    path: str
    line: int
    rule: str


def repository_files(root: Path) -> tuple[list[Path], list[str]]:
    """返回 Git 已跟踪和未忽略文件，以及已提交的环境文件。"""
    visible = _git_paths(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    tracked = _git_paths(root, ["ls-files", "-z"])
    tracked_env = [path for path in tracked if Path(path).name.startswith(".env")]
    return [root / path for path in visible], tracked_env


def scan_repository(root: Path) -> list[SecurityIssue]:
    files, tracked_env = repository_files(root)
    issues = [
        SecurityIssue(path=path, line=0, rule="unexpected_tracked_env_file")
        for path in tracked_env
        if Path(path).name not in _ALLOWED_ENV_FILES
    ]
    for path in files:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "scripts/security_scan.py":
            continue
        issues.extend(scan_text(relative, text))
    return issues


def scan_text(path: str, text: str) -> list[SecurityIssue]:
    issues: list[SecurityIssue] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _PRIVATE_KEY_PATTERN.search(line):
            issues.append(SecurityIssue(path, line_number, "private_key"))
        for rule, pattern in _TOKEN_PATTERNS:
            if pattern.search(line):
                issues.append(SecurityIssue(path, line_number, rule))
        assignment = _ENV_ASSIGNMENT.search(line) if Path(path).name.startswith(".env") else None
        if assignment and not _is_placeholder(assignment.group(2)):
            issues.append(SecurityIssue(path, line_number, "nonempty_secret_assignment"))
        if _INTERNAL_URL.search(line) and ".invalid" not in line.lower():
            issues.append(SecurityIssue(path, line_number, "internal_url"))
        if _ABSOLUTE_USER_PATH.search(line):
            issues.append(SecurityIssue(path, line_number, "absolute_user_path"))
        if any(marker.lower() in line.lower() for marker in _ENTERPRISE_MARKERS):
            issues.append(SecurityIssue(path, line_number, "enterprise_identifier"))
    return issues


def _is_placeholder(value: str) -> bool:
    normalized = value.strip("\"'").lower()
    return not normalized or normalized.startswith(_PLACEHOLDER_PREFIXES)


def _git_paths(root: Path, arguments: list[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", *arguments],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    issues = scan_repository(args.root.resolve())
    if issues:
        for issue in issues:
            print(f"{issue.path}:{issue.line}: {issue.rule}")
        print(f"security_scan=failed issues={len(issues)}")
        return 1
    print("security_scan=passed issues=0 tracked_env=.env.example")
    return 0


if __name__ == "__main__":
    sys.exit(main())
