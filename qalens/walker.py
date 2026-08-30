"""검사 대상 파일 수집."""
from __future__ import annotations

import fnmatch
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".pytest_cache", ".mypy_cache",
    "node_modules", "venv", ".venv", "env", ".env", "site-packages",
    "dist", "build", ".idea", ".vscode", ".gradle", "bin", "obj",
    ".next", ".nuxt", "coverage", ".claude", "logs",
    "vendor", "third_party", "thirdparty",     # 남이 빌드해 넣은 코드
}

# 남의 빌드 산출물과 백업본. 여기서 나온 지적은 고칠 수도 없고 의미도 없다.
VENDOR_NAMES = (
    ".min.js", ".min.css", "-min.js", ".bundle.js", ".pack.js", ".map",
    ".bak", ".orig", ".rej", ".tmp", ".swp", "~",
)

MAX_BYTES = 3_000_000  # 3MB 넘는 텍스트는 생성물로 보고 건너뛴다


def looks_minified(text: str) -> bool:
    """미니파이/생성 코드 판정 — 줄 수 대비 줄이 비정상적으로 길다."""
    if len(text) < 2000:
        return False
    lines = text.splitlines() or [""]
    longest = max(len(l) for l in lines)
    avg = len(text) / len(lines)
    return longest > 2000 or (avg > 300 and len(lines) < 200)


def collect(target: Path, extra_ignores: list[str] | None = None) -> list[Path]:
    extra_ignores = extra_ignores or []

    if target.is_file():
        return [target]

    files: list[Path] = []
    for p in sorted(target.rglob("*")):
        if not p.is_file():
            continue
        parts = set(p.relative_to(target).parts[:-1])
        if parts & IGNORE_DIRS:
            continue
        rel = p.relative_to(target).as_posix()
        if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat) for pat in extra_ignores):
            continue
        if p.name.lower().endswith(VENDOR_NAMES):
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue  # 권한이 없거나 사라진 파일
        files.append(p)
    return files
