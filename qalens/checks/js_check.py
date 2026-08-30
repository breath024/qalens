"""JavaScript 정적 검사. 문법 자체의 유효성은 런타임 검사(브라우저)가 맡는다."""
from __future__ import annotations

import re

from ..core import ERROR, INFO, WARN, FileReport, line_of, line_text, strip_js_noise

PAIRS = {")": "(", "]": "[", "}": "{"}
OPEN = set("([{")

_DECL = re.compile(r"(?m)^(?:export\s+)?(?:async\s+)?(function|class|const|let|var)\s+([A-Za-z_$][\w$]*)")
_LOOSE_EQ = re.compile(r"[^=!<>]([=!]=)(?!=)")
_EMPTY_CATCH = re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}")
_TIMER_STRING = re.compile(r"\b(setTimeout|setInterval)\s*\(\s*['\"]")
_IF_ASSIGN = re.compile(r"\b(if|while)\s*\(\s*[A-Za-z_$][\w$.\[\]]*\s*=(?![=>])")
_ASYNC_FN = re.compile(r"\basync\s+(?:function\s*[\w$]*\s*)?\(")
_CONSOLE = re.compile(r"\bconsole\.(log|debug|dir)\s*\(")
_DEBUGGER = re.compile(r"(?<![\w.])debugger\b")
_DOC_WRITE = re.compile(r"\bdocument\.write(?:ln)?\s*\(")


def _balance(rep: FileReport, clean: str, base_line: int) -> None:
    stack: list[tuple[str, int]] = []
    for i, c in enumerate(clean):
        if c in OPEN:
            stack.append((c, i))
        elif c in PAIRS:
            if not stack:
                rep.add(base_line + line_of(clean, i) - 1, ERROR, "js/unbalanced",
                        f"짝 없는 `{c}` — 여는 괄호보다 닫는 괄호가 많다",
                        line_text(clean, line_of(clean, i)))
                return
            op, oi = stack.pop()
            if op != PAIRS[c]:
                rep.add(base_line + line_of(clean, oi) - 1, ERROR, "js/unbalanced",
                        f"`{op}` 를 열고 `{c}` 로 닫았음 — 괄호 종류가 어긋남",
                        line_text(clean, line_of(clean, oi)))
                return
    if stack:
        op, oi = stack[0]
        rep.add(base_line + line_of(clean, oi) - 1, ERROR, "js/unbalanced",
                f"`{op}` 가 끝까지 닫히지 않음",
                line_text(clean, line_of(clean, oi)))


def _body_after(clean: str, start: int) -> str:
    """start 이후 첫 `{` 부터 짝이 맞는 `}` 까지."""
    i = clean.find("{", start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(clean)):
        if clean[j] == "{":
            depth += 1
        elif clean[j] == "}":
            depth -= 1
            if depth == 0:
                return clean[i:j + 1]
    return clean[i:]


def run(rep: FileReport, src: str, base_line: int = 1, origin: str = "") -> None:
    """base_line: HTML 인라인 스크립트일 때 <script> 가 있던 줄."""
    clean = strip_js_noise(src)
    tag = f" ({origin})" if origin else ""

    def add(offset, sev, rule, msg, snippet="", hint=""):
        ln = base_line + line_of(clean, offset) - 1
        rep.add(ln, sev, rule, msg + tag, snippet or line_text(src, line_of(src, offset)), hint)

    _balance(rep, clean, base_line)

    # 같은 이름을 두 번 선언 — 뒤엣것이 이겨서 앞 구현이 통째로 죽는다.
    seen: dict[tuple[str, str], int] = {}
    for m in _DECL.finditer(clean):
        kind, name = m.group(1), m.group(2)
        key = ("fn" if kind in ("function", "class") else "var", name)
        ln = base_line + line_of(clean, m.start()) - 1
        if key in seen:
            sev = ERROR if kind in ("function", "class") else WARN
            rep.add(ln, sev, "js/dup-decl",
                    f"`{name}` 을 {seen[key]}번째 줄에서 이미 선언했음 — "
                    + ("뒤엣것이 앞 구현을 덮어쓴다" if kind in ("function", "class")
                       else "같은 스코프면 SyntaxError") + tag,
                    line_text(src, line_of(src, m.start())).strip())
        else:
            seen[key] = ln

    for m in _LOOSE_EQ.finditer(clean):
        add(m.start() + 1, INFO, "js/loose-eq",
            f"`{m.group(1)}` 느슨한 비교 — 타입 강제변환이 끼어든다",
            hint=f"`{m.group(1)}=` 로 바꿀 것")

    for m in _IF_ASSIGN.finditer(clean):
        add(m.start(), WARN, "js/assign-in-condition",
            f"{m.group(1)} 조건 안에서 대입(`=`)을 하고 있음 — 비교(`==`)의 오타일 가능성",
            hint="의도한 대입이면 `((x = f()))` 처럼 괄호를 한 겹 더 씌워 명시할 것")

    for m in _EMPTY_CATCH.finditer(clean):
        # 주석으로 "왜 무시하는지" 적어둔 catch 는 의도적인 것으로 본다.
        original = src[m.start():m.end()]
        if "//" in original or "/*" in original:
            continue
        # try 와 catch 가 한 줄에 있으면 localStorage 류의 관용적 무시 — 급을 낮춘다.
        one_liner = "try" in line_text(clean, line_of(clean, m.start()))
        if one_liner:
            add(m.start(), INFO, "js/empty-catch-inline",
                "한 줄짜리 try/catch 로 예외를 통째로 무시함 (관용적 무시로 보임)")
        else:
            add(m.start(), WARN, "js/empty-catch",
                "빈 catch — 예외가 조용히 사라져서 버그를 못 찾게 된다",
                hint="무시가 의도라면 이유를 주석으로 남길 것")

    for m in _TIMER_STRING.finditer(src):
        add(m.start(), WARN, "js/timer-string",
            f"{m.group(1)} 첫 인자가 문자열 — eval 과 같고 CSP 에서 막힌다",
            hint="함수를 직접 넘길 것")

    for m in _DOC_WRITE.finditer(clean):
        add(m.start(), WARN, "js/document-write",
            "document.write 는 로드 후 호출하면 문서를 통째로 날린다")

    n_console = len(_CONSOLE.findall(clean))
    if n_console:
        m = _CONSOLE.search(clean)
        add(m.start(), INFO, "js/console-left",
            f"console 출력이 {n_console}곳 남아 있음")

    for m in _DEBUGGER.finditer(clean):
        add(m.start(), ERROR, "js/debugger",
            "`debugger` 문이 남아 있음 — 개발자도구가 열려 있으면 실행이 멈춘다")

    for m in _ASYNC_FN.finditer(clean):
        body = _body_after(clean, m.end())
        if body and not re.search(r"\bawait\b", body):
            add(m.start(), INFO, "js/async-no-await",
                "async 함수 안에 await 가 없음 — 불필요하게 Promise 로 감싸진다")

    n_var = len(re.findall(r"(?m)^\s*var\s+", clean))
    if n_var:
        m = re.search(r"(?m)^\s*var\s+", clean)
        add(m.start(), INFO, "js/var-usage",
            f"`var` 선언 {n_var}곳 — 함수 스코프라 반복문/클로저에서 값이 새어나간다")
