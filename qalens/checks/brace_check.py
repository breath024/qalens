"""Kotlin / C# 표층 검사.

두 언어는 파서를 내장하지 않았다. 컴파일러가 잡아줄 문법 오류를 흉내내지 않고,
컴파일은 통과하지만 사람이 놓치는 것들만 본다. 리포트에 '표층'이라고 표시된다.
"""
from __future__ import annotations

import re

from ..core import ERROR, INFO, WARN, FileReport, line_of, line_text, strip_js_noise

PAIRS = {")": "(", "]": "[", "}": "{"}
OPEN = set("([{")

_KT_FUN = re.compile(r"(?m)^\s*(?:(?:private|public|internal|protected|open|override|suspend|inline|operator|@\w+)\s+)*fun\s+([\w<>.]+)\s*\(")
_CS_METHOD = re.compile(
    r"(?m)^\s*(?:\[[^\]]+\]\s*)*(?:(?:public|private|protected|internal|static|virtual|override|async|sealed|partial|new|extern|unsafe)\s+)+"
    r"[\w<>\[\],\?\.]+\s+(\w+)\s*\([^)]*\)\s*(?:where[^{]*)?\{"
)
_KT_IMPORT = re.compile(r"(?m)^\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?\s*$")
_CS_USING = re.compile(r"(?m)^\s*using\s+(?:static\s+)?([\w.]+)\s*;")
_EMPTY_CATCH = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")
_NPE_BANG = re.compile(r"[\w\)\]]!!")


def _balance(rep: FileReport, clean: str) -> None:
    stack: list[tuple[str, int]] = []
    for i, c in enumerate(clean):
        if c in OPEN:
            stack.append((c, i))
        elif c in PAIRS:
            if not stack:
                ln = line_of(clean, i)
                rep.add(ln, ERROR, "brace/unbalanced", f"짝 없는 `{c}`",
                        line_text(clean, ln).strip())
                return
            op, oi = stack.pop()
            if op != PAIRS[c]:
                ln = line_of(clean, oi)
                rep.add(ln, ERROR, "brace/unbalanced",
                        f"`{op}` 를 열고 `{c}` 로 닫았음", line_text(clean, ln).strip())
                return
    if stack:
        op, oi = stack[0]
        ln = line_of(clean, oi)
        rep.add(ln, ERROR, "brace/unbalanced", f"`{op}` 가 끝까지 닫히지 않음",
                line_text(clean, ln).strip())


def run(rep: FileReport, text: str, lang: str) -> None:
    # 주석/문자열 제거 방식은 C 계열과 같다. 다만 두 언어에 정규식 리터럴은 없으므로 끈다.
    clean = strip_js_noise(text, regex_literals=False)
    _balance(rep, clean)

    fn_re = _KT_FUN if lang == "kotlin" else _CS_METHOD
    seen: dict[str, int] = {}
    for m in fn_re.finditer(clean):
        name = m.group(1)
        ln = line_of(clean, m.start())
        if name in seen:
            rep.add(ln, INFO, "brace/dup-name",
                    f"`{name}` 라는 이름의 함수가 {seen[name]}번째 줄에도 있음 "
                    "(오버로드면 정상, 아니면 중복 정의 — 표층 검사라 구분은 못 함)",
                    line_text(text, ln).strip()[:100])
        else:
            seen[name] = ln

    imp_re = _KT_IMPORT if lang == "kotlin" else _CS_USING
    for m in imp_re.finditer(clean):
        full = m.group(1)
        alias = (m.group(2) if lang == "kotlin" and m.lastindex and m.lastindex >= 2 else None)
        symbol = alias or full.split(".")[-1]
        if symbol == "*":
            continue
        body = clean[:m.start()] + clean[m.end():]
        if not re.search(rf"(?<![\w.]){re.escape(symbol)}\b", body):
            ln = line_of(clean, m.start())
            rep.add(ln, INFO, "brace/unused-import",
                    f"`{symbol}` 를 임포트했지만 파일 안에서 쓰지 않음",
                    line_text(text, ln).strip())

    for m in _EMPTY_CATCH.finditer(clean):
        ln = line_of(clean, m.start())
        rep.add(ln, WARN, "brace/empty-catch",
                "빈 catch — 예외가 조용히 사라진다", line_text(text, ln).strip())

    if lang == "kotlin":
        for m in _NPE_BANG.finditer(clean):
            ln = line_of(clean, m.start())
            rep.add(ln, WARN, "kotlin/not-null-assert",
                    "`!!` — null 이면 그 자리에서 앱이 죽는다",
                    line_text(text, ln).strip()[:100],
                    "?. 나 ?: 로 대체할 수 있는지 볼 것")

    if lang == "csharp":
        for m in re.finditer(r"(?m)^\s*catch\s*\(\s*Exception\s+\w+\s*\)\s*\{", clean):
            ln = line_of(clean, m.start())
            rep.add(ln, INFO, "csharp/broad-catch",
                    "Exception 을 통째로 잡고 있음 — 어떤 실패인지 구분이 안 된다")

    # 너무 긴 함수 (중괄호 깊이로 대략 측정)
    for m in fn_re.finditer(clean):
        start = clean.find("{", m.end() - 1)
        if start < 0:
            continue
        depth, end = 0, start
        for j in range(start, len(clean)):
            if clean[j] == "{":
                depth += 1
            elif clean[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        length = clean.count("\n", start, end)
        if length > 120:
            ln = line_of(clean, m.start())
            rep.add(ln, INFO, "brace/long-function",
                    f"`{m.group(1)}` 가 {length}줄 — 한 함수가 너무 많은 일을 하고 있다")
