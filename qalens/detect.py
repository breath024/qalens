"""파일 종류 판별 — 확장자를 먼저 믿되, 내용으로 뒤집는다."""
from __future__ import annotations

import re
from pathlib import Path

# 종류 이름은 checks 레지스트리 키와 1:1로 맞춘다.
EXT_MAP = {
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
    ".ts": "js", ".tsx": "js",
    ".css": "css",
    ".py": "python", ".pyw": "python",
    ".json": "json",
    ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".kt": "kotlin", ".kts": "kotlin",
    ".cs": "csharp",
    ".md": "text", ".txt": "text", ".yml": "text", ".yaml": "text",
    # 코드가 아니라 데이터. 긴 줄·형식은 정상이므로 스타일 검사를 걸지 않는다.
    ".srt": "data", ".vtt": "data", ".csv": "data", ".tsv": "data",
    ".log": "data", ".edl": "data",
}

DATA_KINDS = {"data", "jsonl"}

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg",
    ".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".flac",
    ".zip", ".gz", ".7z", ".rar", ".exe", ".dll", ".pyd", ".so",
    ".pdf", ".hwp", ".hwpx", ".docx", ".xlsx", ".pptx",
    ".ttf", ".otf", ".woff", ".woff2", ".pyc", ".db", ".sqlite", ".sqlite3",
}

_HTML_SNIFF = re.compile(r"(?is)<!doctype\s+html|<html[\s>]|<head[\s>]|<body[\s>]|<div[\s>]|<script[\s>]")
_PY_SNIFF = re.compile(r"(?m)^\s*(?:def |class |import |from \w+ import |if __name__\s*==)")
_CSS_SNIFF = re.compile(r"(?m)^[\s\w\.\#\*\[\]\-:>,]+\{[^}]*[\w-]+\s*:[^;}]+[;}]")


def sniff(path: Path, text: str | None) -> str:
    """반환값: html/js/css/python/json/kotlin/csharp/text/binary/unknown"""
    ext = path.suffix.lower()
    if ext in BINARY_EXT:
        return "binary"

    guess = EXT_MAP.get(ext)
    if text is None:
        return "binary"

    head = text[:4000]

    # 확장자가 없거나 모르는 경우, 내용으로 판별한다.
    if guess is None:
        if _HTML_SNIFF.search(head):
            return "html"
        stripped = text.strip()
        if stripped[:1] in "{[" and stripped[-1:] in "}]":
            return "json"
        if _PY_SNIFF.search(head):
            return "python"
        if _CSS_SNIFF.search(head):
            return "css"
        return "text" if text.isprintable() or "\n" in text else "unknown"

    # 확장자가 .js 인데 실제로는 HTML 조각인 경우 등, 내용이 확장자를 뒤집는 상황.
    if guess in ("js", "text") and _HTML_SNIFF.search(head):
        # 데이터 팩(.js)에 HTML 문자열이 들어있는 흔한 경우와 구분: 파일 첫 글자가 태그인지 본다.
        if text.lstrip()[:1] == "<":
            return "html"
    if guess == "text" and path.suffix.lower() in (".yml", ".yaml"):
        return "text"
    return guess


def read_text(path: Path) -> tuple[str | None, str]:
    """(내용, 인코딩라벨). 바이너리거나 못 읽으면 (None, 사유)."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, f"읽기 실패: {e}"

    if b"\x00" in raw[:8000]:
        return None, "binary"

    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig"), "utf-8-bom"
        except UnicodeDecodeError:
            # BOM 은 있는데 본문이 UTF-8 이 아님 — 아래 후보들로 넘어간다
            pass
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        # UTF-8 이 아니다 — CP949/EUC-KR 을 차례로 시도한다
        pass
    for enc in ("cp949", "euc-kr"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "unknown"
