"""Python 정적 검사. ast 로 실제 구문 트리를 보므로 정확도가 가장 높다."""
from __future__ import annotations

import ast
import builtins
import re

from ..core import ERROR, INFO, WARN, FileReport

BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "self", "cls",
}


class _Collector(ast.NodeVisitor):
    """파일 안에서 이름이 어떻게 묶이는지 전부 모은다 (스코프는 구분하지 않는다)."""

    def __init__(self):
        self.bound: set[str] = set()
        self.used: set[str] = set()
        self.imports: list[tuple[str, str, int]] = []   # 바인딩명, 원문, 줄
        # 이름 -> 정의들. 같은 이름이 여러 스코프에 있으면 어느 것이 불리는지 알 수 없다.
        self.funcs: dict[str, list[ast.FunctionDef]] = {}
        self.methods: set[str] = set()                  # 클래스 안에 정의된 이름
        self.calls: list[ast.Call] = []
        self.attr_roots: set[str] = set()

    def _bind(self, name):
        if name:
            self.bound.add(name)

    def visit_Import(self, node):
        for a in node.names:
            name = a.asname or a.name.split(".")[0]
            self._bind(name)
            self.imports.append((name, f"import {a.name}" + (f" as {a.asname}" if a.asname else ""), node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name == "*":
                self.bound.add("*")
                continue
            name = a.asname or a.name
            self._bind(name)
            mod = ("." * node.level) + (node.module or "")
            self.imports.append((name, f"from {mod} import {a.name}", node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._bind(node.name)
        self.funcs.setdefault(node.name, []).append(node)
        for arg in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
            self._bind(arg.arg)
        if node.args.vararg:
            self._bind(node.args.vararg.arg)
        if node.args.kwarg:
            self._bind(node.args.kwarg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for arg in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
            self._bind(arg.arg)
        if node.args.vararg:
            self._bind(node.args.vararg.arg)
        if node.args.kwarg:
            self._bind(node.args.kwarg.arg)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._bind(node.name)
        # 메서드는 self 가 붙어 인자 수가 다르므로 맨이름 호출과 대조하면 안 된다.
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.methods.add(stmt.name)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self._bind(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        for n in node.names:
            self._bind(n)
        self.generic_visit(node)

    visit_Nonlocal = visit_Global

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)
        else:
            self.used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        n = node
        while isinstance(n, ast.Attribute):
            n = n.value
        if isinstance(n, ast.Name):
            self.attr_roots.add(n.id)
        self.generic_visit(node)

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node):
        self._bind(node.name)
        self.generic_visit(node)


_FSTRING_NO_PLACEHOLDER = re.compile(r"""(?<![\w'"])[fF](['"])(?:(?!\1)[^\\{])*\1""")


def _dup_defs(rep: FileReport, tree: ast.AST) -> None:
    """같은 몸통(모듈/클래스) 안에서 같은 이름을 두 번 def — 뒤엣것만 살아남는다."""
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        where = "클래스" if isinstance(node, ast.ClassDef) else "이 범위"
        seen: dict[str, int] = {}
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if stmt.name in seen:
                    rep.add(stmt.lineno, ERROR, "py/dup-def",
                            f"`{stmt.name}` 를 {seen[stmt.name]}번째 줄에서 이미 정의했음 — "
                            f"{where} 안에서는 뒤엣것이 앞 정의를 통째로 덮는다",
                            f"def {stmt.name}(...)")
                seen[stmt.name] = stmt.lineno


def _unreachable(rep: FileReport, tree: ast.AST) -> None:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body[:-1]):
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                nxt = body[i + 1]
                rep.add(nxt.lineno, WARN, "py/unreachable",
                        f"{stmt.__class__.__name__.lower()} 뒤의 코드 — 절대 실행되지 않는다")
                break


def run(rep: FileReport, text: str, filename: str) -> ast.AST | None:
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as e:
        rep.add(e.lineno or 0, ERROR, "py/syntax",
                f"문법 오류: {e.msg}",
                (e.text or "").rstrip(),
                "이 파일은 import 조차 되지 않는다 — 다른 검사는 건너뛴다")
        return None
    except ValueError as e:
        rep.add(0, ERROR, "py/parse", f"파싱 실패: {e}")
        return None

    src_lines = text.splitlines()
    col = _Collector()
    col.visit(tree)

    # 정의된 적 없는 이름을 읽고 있음
    if "*" not in col.bound:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in col.bound and node.id not in BUILTINS:
                    rep.add(node.lineno, ERROR, "py/undefined-name",
                            f"`{node.id}` 가 어디에도 정의/임포트되어 있지 않음 — 실행하면 NameError",
                            f"{node.id}")

    # 안 쓰는 import
    for name, raw, line in col.imports:
        if name in col.used or name in col.attr_roots:
            continue
        if raw.startswith("from __future__"):
            continue        # 컴파일러 지시자라 '쓰이지 않는' 것이 정상이다
        if re.search(rf'(?<![\w.]){re.escape(name)}\b', text[text.find("\n", 0) :].replace(raw, "", 1)):
            # __all__ 이나 문자열로만 참조되는 경우가 있어 한 번 더 본다
            if len(re.findall(rf'(?<![\w.]){re.escape(name)}\b', text)) > 1:
                continue
        rep.add(line, WARN, "py/unused-import",
                f"`{name}` 를 임포트했지만 쓰지 않음", raw)

    _dup_defs(rep, tree)
    _unreachable(rep, tree)

    # 함수별 세부 검사
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    and d.func.id in ("list", "dict", "set")
                ):
                    rep.add(d.lineno, ERROR, "py/mutable-default",
                            "기본값이 가변 객체 — 호출들끼리 같은 객체를 공유해서 값이 쌓인다",
                            f"def {node.name}(... = {ast.unparse(d)})",
                            "None 을 기본값으로 두고 함수 안에서 만들 것")

        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                rep.add(node.lineno, WARN, "py/bare-except",
                        "`except:` 는 KeyboardInterrupt/SystemExit 까지 삼킨다",
                        "except:", "최소한 `except Exception:` 으로")
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                # 왜 무시하는지 주석으로 밝혀둔 자리는 의도된 것으로 본다.
                nearby = "\n".join(
                    src_lines[max(0, node.lineno - 2):node.body[0].lineno]
                )
                if "#" in nearby:
                    rep.add(node.lineno, INFO, "py/silent-except-noted",
                            "예외를 무시하고 있음 (이유가 주석으로 적혀 있음)")
                else:
                    rep.add(node.lineno, WARN, "py/silent-except",
                            "예외를 잡아서 아무것도 안 함 — 실패가 조용히 묻힌다",
                            hint="무시가 의도라면 왜 안전한지 주석으로 남길 것")

        elif isinstance(node, ast.Compare):
            for op, cmp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    if isinstance(cmp, ast.Constant) and cmp.value is None:
                        rep.add(node.lineno, WARN, "py/eq-none",
                                "None 비교는 `is None` / `is not None` 으로")
                    elif isinstance(cmp, ast.Constant) and cmp.value in (True, False):
                        rep.add(node.lineno, INFO, "py/eq-bool",
                                f"`== {cmp.value}` 비교 — 값 자체로 판정하는 편이 낫다")

        elif isinstance(node, ast.Assert):
            if isinstance(node.test, ast.Tuple) and node.test.elts:
                rep.add(node.lineno, ERROR, "py/assert-tuple",
                        "assert 에 튜플을 넘김 — 항상 참이라 검사가 전혀 동작하지 않는다",
                        ast.unparse(node)[:80])

        elif isinstance(node, ast.Call):
            fn = node.func
            # open() 인코딩 미지정 — 윈도우에서 한글이 CP949 로 읽혀 깨진다
            if isinstance(fn, ast.Name) and fn.id == "open":
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                kw = {k.arg for k in node.keywords}
                if "b" not in mode and "encoding" not in kw:
                    rep.add(node.lineno, WARN, "py/open-no-encoding",
                            "open() 에 encoding 이 없음 — 윈도우에서는 CP949 로 읽혀 한글이 깨진다",
                            ast.unparse(node)[:80],
                            'encoding="utf-8" 을 명시할 것')
            # 같은 파일에 정의된 함수를 잘못된 인자 개수로 호출.
            # 확실할 때만 짚는다 — 이름이 하나뿐이고, 메서드가 아니고, 언패킹이 없을 때.
            if (isinstance(fn, ast.Name)
                    and len(col.funcs.get(fn.id, ())) == 1
                    and fn.id not in col.methods):
                target = col.funcs[fn.id][0]
                a = target.args
                starred = any(isinstance(x, ast.Starred) for x in node.args)
                double_starred = any(k.arg is None for k in node.keywords)
                if not a.vararg and not a.kwarg and not starred and not double_starred:
                    maxp = len(a.posonlyargs) + len(a.args)
                    minp = maxp - len(a.defaults)
                    given = len(node.args)
                    names = {k.arg for k in node.keywords if k.arg}
                    total = given + len(names)
                    if given > maxp or total < minp:
                        rep.add(node.lineno, ERROR, "py/arity",
                                f"`{fn.id}()` 는 인자 {minp}~{maxp}개를 받는데 {total}개를 넘김 "
                                f"(정의: {target.lineno}번째 줄)",
                                ast.unparse(node)[:80])

    for m in _FSTRING_NO_PLACEHOLDER.finditer(text):
        # 여러 줄 문자열을 이어붙일 때는 조각마다 f 를 붙이는 편이 오히려 일관적이다.
        before = text[:m.start()].rstrip()
        after = text[m.end():].lstrip()
        if before.endswith(("'", '"')) or after[:1] in ("'", '"') or after[:2] in ("f'", 'f"'):
            continue
        line = text.count("\n", 0, m.start()) + 1
        rep.add(line, WARN, "py/pointless-fstring",
                "f-string 인데 `{}` 가 없음 — 변수를 넣으려다 만 자리일 수 있다",
                m.group(0)[:60])

    return tree
