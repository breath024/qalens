"""QALens 공통 자료구조."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ERROR = "error"
WARN = "warn"
INFO = "info"

SEVERITY_ORDER = {ERROR: 0, WARN: 1, INFO: 2}


@dataclass
class Finding:
    """검사 결과 한 건."""

    path: str          # 리포트에 찍힐 파일 경로 (프로젝트 루트 기준 상대)
    line: int          # 1-indexed, 위치를 모르면 0
    severity: str      # error / warn / info
    rule: str          # 규칙 ID (html/dup-id 같은 슬래시 표기)
    message: str       # 사람이 읽을 한 줄
    snippet: str = ""  # 문제의 실물 한 조각
    hint: str = ""     # 어떻게 고치는지

    def sort_key(self):
        return (SEVERITY_ORDER.get(self.severity, 9), self.path, self.line, self.rule)


@dataclass
class FileReport:
    path: Path
    rel: str
    kind: str                       # detect.py 가 판별한 종류
    findings: list[Finding] = field(default_factory=list)
    skipped: str = ""               # 검사 못 한 이유 (있으면)

    def add(self, line, severity, rule, message, snippet="", hint=""):
        self.findings.append(
            Finding(self.rel, line, severity, rule, message, snippet.strip()[:300], hint)
        )


def line_of(text: str, index: int) -> int:
    """문자 오프셋 -> 1-indexed 줄 번호."""
    if index < 0:
        return 0
    return text.count("\n", 0, index) + 1


def line_text(text: str, lineno: int) -> str:
    """1-indexed 줄 번호 -> 그 줄 원문."""
    if lineno <= 0:
        return ""
    lines = text.splitlines()
    if lineno > len(lines):
        return ""
    return lines[lineno - 1]


# ----------------------------------------------------------------------
# JS 소스에서 '코드가 아닌 부분'을 지우는 스캐너.
# 주석·문자열·정규식 리터럴의 내용만 공백으로 바꾸고 길이와 줄바꿈은 보존한다.
# ----------------------------------------------------------------------

# 이 단어 바로 뒤의 `/` 는 나눗셈이 아니라 정규식 리터럴의 시작이다.
_REGEX_KEYWORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "case", "do", "else", "yield", "await", "throw",
}
_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")

_NEWLINE = "\n"
_BACKSLASH = "\\"


def _regex_can_start(prev_char: str, prev_word: str) -> bool:
    """직전 토큰을 보고 `/` 가 정규식의 시작인지 나눗셈인지 가른다."""
    if prev_word:
        return prev_word in _REGEX_KEYWORDS
    if prev_char == "":
        return True
    return prev_char not in ")]}"


def strip_js_noise(src: str, regex_literals: bool = True) -> str:
    """주석·문자열·정규식의 내용을 공백으로 치환한 소스를 돌려준다.

    템플릿 리터럴의 `${...}` 안은 문자열이 아니라 코드이므로 지우지 않는다 —
    중첩 백틱을 종료로 오인하면 그 뒤 파싱이 통째로 어긋나기 때문에 재귀로 훑는다.
    regex_literals=False 면 `/.../` 를 정규식으로 보지 않는다 (Kotlin/C# 용).
    """
    out = list(src)
    _scan(src, out, 0, regex_literals, stop_at_brace=False)
    return "".join(out)


def _scan(src: str, out: list, i: int, regex_literals: bool, stop_at_brace: bool) -> int:
    """i 부터 훑으며 out 을 제자리 수정.

    stop_at_brace 면 짝이 맞는 `}` 를 만난 위치를 반환한다 (템플릿 보간의 끝).
    """
    n = len(src)
    prev_char = ""
    prev_word = ""
    depth = 0

    while i < n:
        c = src[i]

        if c in _IDENT_CHARS:
            start = i
            while i < n and src[i] in _IDENT_CHARS:
                i += 1
            prev_word = src[start:i]
            prev_char = src[i - 1]
            continue

        nxt = src[i + 1] if i + 1 < n else ""

        # 줄 주석
        if c == "/" and nxt == "/":
            while i < n and src[i] != _NEWLINE:
                out[i] = " "
                i += 1
            prev_char = prev_word = ""
            continue

        # 블록 주석
        if c == "/" and nxt == "*":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] != _NEWLINE:
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                if i + 1 < n:
                    out[i + 1] = " "
                i += 2
            prev_char = prev_word = ""
            continue

        # 정규식 리터럴
        if c == "/" and regex_literals and _regex_can_start(prev_char, prev_word):
            end = _scan_regex(src, i)
            if end < 0:
                prev_char, prev_word = c, ""      # 나눗셈이었다
                i += 1
                continue
            for j in range(i + 1, end):
                if src[j] != _NEWLINE:
                    out[j] = " "
            i = end + 1
            while i < n and src[i].isalpha():     # 플래그 (g, i, m ...)
                i += 1
            prev_char, prev_word = ")", ""        # 정규식 하나가 곧 하나의 값
            continue

        # 템플릿 리터럴
        if c == "`":
            i = _scan_template(src, out, i, regex_literals)
            prev_char, prev_word = ")", ""
            continue

        # 따옴표 문자열
        if c == '"' or c == "'":
            quote = c
            i += 1
            while i < n:
                if src[i] == _BACKSLASH:
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                    continue
                if src[i] == quote or src[i] == _NEWLINE:
                    break
                out[i] = " "
                i += 1
            if i < n and src[i] == quote:
                i += 1
            prev_char, prev_word = ")", ""
            continue

        if c == "{":
            depth += 1
        elif c == "}":
            if stop_at_brace and depth == 0:
                return i
            depth -= 1

        if not c.isspace():
            prev_char, prev_word = c, ""
        i += 1

    return i


def _scan_template(src: str, out: list, i: int, regex_literals: bool) -> int:
    """src[i] == '`'. 닫는 백틱 '다음' 인덱스를 반환한다."""
    n = len(src)
    i += 1
    while i < n:
        c = src[i]
        if c == _BACKSLASH:
            out[i] = " "
            if i + 1 < n:
                out[i + 1] = " "
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and i + 1 < n and src[i + 1] == "{":
            # ${ ... } 안은 문자열이 아니라 코드다. 지우지 않고 그대로 훑는다.
            end = _scan(src, out, i + 2, regex_literals, stop_at_brace=True)
            i = end + 1 if end < n else n
            continue
        if c != _NEWLINE:
            out[i] = " "
        i += 1
    return n


def _scan_regex(src: str, start: int) -> int:
    """src[start] == '/' 일 때 짝이 되는 닫는 '/' 위치. 정규식이 아니면 -1."""
    i = start + 1
    n = len(src)
    in_class = False
    while i < n:
        c = src[i]
        if c == _BACKSLASH:
            i += 2
            continue
        if c == _NEWLINE:
            return -1          # 정규식 리터럴은 줄을 넘지 못한다
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return i if i > start + 1 else -1   # `//` 는 주석이지 빈 정규식이 아니다
        i += 1
    return -1


# ----------------------------------------------------------------------
SECRET_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "Anthropic API 키"),
    (r"sk-[A-Za-z0-9]{32,}", "OpenAI 계열 API 키"),
    (r"AIza[0-9A-Za-z_\-]{30,}", "Google API 키"),
    (r"ghp_[A-Za-z0-9]{30,}", "GitHub 개인 토큰"),
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "Slack 토큰"),
    (r"AKIA[0-9A-Z]{16}", "AWS 액세스 키"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "개인키 블록"),
]

_SECRET_ASSIGN = re.compile(
    r"""(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd|client[_-]?secret)\b\s*[:=]\s*["']([^"']{8,})["']"""
)

_PLACEHOLDER = re.compile(
    r"(?i)^(your|example|test|dummy|changeme|xxx+|\.\.\.|<.*>|\$\{|os\.environ|process\.env|placeholder|none|null|--)"
)


def scan_secrets(rep: FileReport, text: str) -> None:
    """하드코딩된 자격증명 탐지. 확장자와 무관하게 전 파일 공통."""
    for pat, label in SECRET_PATTERNS:
        for m in re.finditer(pat, text):
            ln = line_of(text, m.start())
            rep.add(
                ln, ERROR, "secret/hardcoded",
                f"{label}로 보이는 문자열이 코드에 박혀 있음",
                m.group(0)[:12] + "…(가림)",
                "환경변수나 별도 키 파일로 빼고, 이미 커밋됐다면 해당 키는 폐기할 것",
            )
    for m in _SECRET_ASSIGN.finditer(text):
        value = m.group(2)
        if _PLACEHOLDER.match(value.strip()):
            continue
        ln = line_of(text, m.start())
        rep.add(
            ln, WARN, "secret/assigned",
            f"`{m.group(1)}` 에 값이 직접 대입되어 있음",
            m.group(1) + " = \"" + value[:4] + "…(가림)\"",
            "실행 시점에 외부 파일/환경변수에서 읽도록 바꿀 것",
        )
