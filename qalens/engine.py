"""검사 오케스트레이션."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import detect, walker
from .checks import brace_check, common, css_check, html_check, js_check, json_check, py_check
from .core import ERROR, INFO, WARN, FileReport

# 런타임 검사를 붙일 수 있는 종류
RUNTIME_KINDS = {"html", "js", "python"}
_ES_MODULE = re.compile(r"(?m)^\s*(?:import\s+[\w{*\s,]+from\s+['\"]|export\s+(?:default|const|function|class|\{))")


@dataclass
class Options:
    runtime: bool = True
    deep: bool = False               # Python 을 실제로 import 해본다
    ignore: list[str] = field(default_factory=list)
    min_severity: str = INFO
    timeout_ms: int = 8000
    max_per_rule: int = 5            # 한 파일에서 같은 규칙을 몇 건까지 낱개로 보고할지


def _collapse_repeats(rep: FileReport, limit: int) -> None:
    """같은 규칙이 한 파일에서 쏟아지면 앞의 몇 건만 남기고 나머지는 한 줄로 접는다.

    같은 지적이 스무 번 반복되면 정작 중요한 다른 지적이 묻힌다.
    """
    if limit <= 0:
        return
    groups: dict[str, list] = {}
    for f in rep.findings:
        groups.setdefault(f.rule, []).append(f)

    kept = []
    seen: dict[str, int] = {}
    for f in rep.findings:
        n = seen.get(f.rule, 0)
        seen[f.rule] = n + 1
        if len(groups[f.rule]) <= limit or n < limit:
            kept.append(f)
    for rule, g in groups.items():
        if len(g) > limit:
            rest = g[limit:]
            nums = sorted({f.line for f in rest if f.line})
            lines = ", ".join(str(n) for n in nums[:12])
            kept.append(type(g[0])(
                path=rep.rel, line=0, severity=g[0].severity, rule=rule,
                message=f"같은 지적 {len(rest)}건 더 (앞의 {limit}건만 펼침)",
                snippet=(lines + ("…" if len(nums) > 12 else "") + "행") if lines else "",
                hint="",
            ))
    rep.findings = kept


@dataclass
class Run:
    root: Path
    reports: list[FileReport] = field(default_factory=list)
    elapsed: float = 0.0
    runtime_used: bool = False
    runtime_note: str = ""

    @property
    def findings(self):
        return [f for r in self.reports for f in r.findings]

    def counts(self):
        c = {ERROR: 0, WARN: 0, INFO: 0}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c


def analyze(target: Path, opt: Options, progress=None) -> Run:
    target = target.resolve()
    root = target if target.is_dir() else target.parent
    run = Run(root=root)
    started = time.time()

    files = walker.collect(target, opt.ignore)
    parsed_py: list[tuple[FileReport, Path, object]] = []
    html_files: list[tuple[FileReport, Path]] = []
    js_sources: list[tuple[FileReport, str, str]] = []

    for i, path in enumerate(files, 1):
        text, enc = detect.read_text(path)
        kind = detect.sniff(path, text)
        rel = path.relative_to(root).as_posix() if path != root else path.name
        rep = FileReport(path=path, rel=rel, kind=kind)

        if progress:
            progress(i, len(files), rel)

        if text is None or kind == "binary":
            rep.kind = "binary"
            rep.skipped = enc if enc != "binary" else "바이너리 파일"
            run.reports.append(rep)
            continue

        # 미니파이/생성 코드는 손댈 수 없으므로 지적해봐야 소음이다.
        if kind in ("js", "css", "html") and walker.looks_minified(text):
            rep.skipped = "미니파이/생성된 코드로 보여 건너뜀"
            run.reports.append(rep)
            continue

        # JSON 은 한 줄로 덤프되는 것이 정상이라 스타일 검사에서는 데이터로 취급한다.
        common.run(rep, text, enc, is_data=kind in detect.DATA_KINDS or kind == "json")

        if kind == "html":
            info = html_check.run(rep, text, path)
            for src, line in info["scripts"]:
                if src.strip():
                    js_check.run(rep, src, base_line=line, origin=f"{line}행 <script>")
            for src, line in info["styles"]:
                if src.strip():
                    css_check.run(rep, src, base_line=line, origin=f"{line}행 <style>")
            html_files.append((rep, path))

        elif kind == "js":
            js_check.run(rep, text)
            js_sources.append((rep, text, rel))

        elif kind == "css":
            css_check.run(rep, text)

        elif kind == "python":
            tree = py_check.run(rep, text, str(path))
            if tree is not None:
                parsed_py.append((rep, path, tree))

        elif kind == "json":
            json_check.run(rep, text)

        elif kind == "jsonl":
            json_check.run_lines(rep, text)

        elif kind in ("kotlin", "csharp"):
            brace_check.run(rep, text, kind)

        run.reports.append(rep)

    # ---- 런타임 단계 -------------------------------------------------
    if opt.runtime and (html_files or js_sources):
        from .runtime.browser import BrowserRunner
        with BrowserRunner(timeout_ms=opt.timeout_ms) as br:
            if not br.ok:
                run.runtime_note = br.error
            else:
                run.runtime_used = True
                for rep, path in html_files:
                    if progress:
                        progress(-1, 0, f"브라우저로 여는 중: {rep.rel}")
                    br.check_html(rep, path)
                for rep, src, rel in js_sources:
                    if _ES_MODULE.search(src):
                        rep.add(0, INFO, "js/module-skip",
                                "ES 모듈 문법이라 단독 문법 검사는 건너뜀 "
                                "(import/export 는 모듈 컨텍스트에서만 유효)")
                        continue
                    if progress:
                        progress(-1, 0, f"JS 문법 확인: {rel}")
                    br.check_js_syntax(rep, src, rel)

    if parsed_py:
        from .runtime import pyrun
        # 같은 모듈을 파일마다 반복해서 알리지 않는다.
        seen_modules: set = set()
        soft = pyrun.uses_own_environment(root)
        for rep, path, tree in parsed_py:
            if opt.runtime:
                if progress:
                    progress(-1, 0, f"의존 모듈 확인: {rep.rel}")
                pyrun.check_imports(rep, tree, path, seen=seen_modules, soft=soft)
            if opt.deep:
                if progress:
                    progress(-1, 0, f"실제 import: {rep.rel}")
                pyrun.deep_import(rep, path)
        run.runtime_used = run.runtime_used or opt.runtime

    # ---- 정리 --------------------------------------------------------
    order = {ERROR: 0, WARN: 1, INFO: 2}
    keep = order[opt.min_severity]
    for rep in run.reports:
        rep.findings = [f for f in rep.findings if order.get(f.severity, 9) <= keep]
        _collapse_repeats(rep, opt.max_per_rule)
        rep.findings.sort(key=lambda f: (order.get(f.severity, 9), f.line))

    run.elapsed = time.time() - started
    return run
