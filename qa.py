#!/usr/bin/env python
"""QALens — 파일을 훑어 종류를 판별하고, 그 종류에 맞는 QA 검사를 돌린다.

    python qa.py <파일이나폴더>            정적 + 런타임 검사, HTML 리포트 생성
    python qa.py . --no-runtime            브라우저 없이 정적 검사만
    python qa.py app.py --deep             파이썬을 실제로 import 까지 해봄
    python qa.py . --min error --fail-on error
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            # 콘솔이 재설정을 거부해도 검사 자체는 돌아간다
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qalens import __version__, report
from qalens.core import ERROR, INFO, WARN
from qalens.engine import Options, analyze

HERE = Path(__file__).resolve().parent
SEV = {"error": ERROR, "warn": WARN, "info": INFO}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="qa",
        description="파일 종류를 판별해 HTML/JS/CSS/Python/JSON/Kotlin/C# 을 검사한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("target", nargs="?", default=".", help="검사할 파일 또는 폴더 (기본: 현재 폴더)")
    p.add_argument("--no-runtime", action="store_true",
                   help="브라우저 렌더링/의존모듈 확인을 건너뛰고 정적 분석만")
    p.add_argument("--deep", action="store_true",
                   help="Python 파일을 별도 프로세스에서 실제로 import 해본다 (부작용 주의)")
    p.add_argument("--html", metavar="경로", help="HTML 리포트 저장 위치")
    p.add_argument("--no-html", action="store_true", help="HTML 리포트를 만들지 않는다")
    p.add_argument("--json", metavar="경로", help="결과를 JSON 으로도 저장")
    p.add_argument("--open", action="store_true", help="끝나면 리포트를 브라우저로 연다")
    p.add_argument("--min", choices=["error", "warn", "info"], default="info",
                   help="이 심각도까지만 보고 (기본: info = 전부)")
    p.add_argument("--ignore", action="append", default=[], metavar="패턴",
                   help="제외할 파일 glob 패턴 (여러 번 지정 가능)")
    p.add_argument("--fail-on", choices=["error", "warn", "none"], default="none",
                   help="이 심각도가 하나라도 있으면 종료코드 1 (CI 용)")
    p.add_argument("--timeout", type=int, default=8000, metavar="ms",
                   help="페이지 하나당 로드 제한 시간 (기본 8000ms)")
    p.add_argument("--max-per-rule", type=int, default=5, metavar="N",
                   help="한 파일에서 같은 규칙을 낱개로 몇 건까지 보고할지 (0=제한 없음, 기본 5)")
    p.add_argument("--quiet", action="store_true", help="진행 표시를 끈다")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--version", action="version", version=f"QALens {__version__}")
    a = p.parse_args(argv)

    target = Path(a.target).expanduser()
    if not target.exists():
        print(f"경로를 찾을 수 없음: {target}", file=sys.stderr)
        return 2

    opt = Options(
        runtime=not a.no_runtime,
        deep=a.deep,
        ignore=a.ignore,
        min_severity=SEV[a.min],
        timeout_ms=a.timeout,
        max_per_rule=a.max_per_rule,
    )

    state = {"last": 0}

    def progress(i, total, label):
        if a.quiet or not sys.stdout.isatty():
            return
        if i > 0:
            msg = f"[{i}/{total}] {label}"
        else:
            msg = f"      {label}"
        pad = max(0, state["last"] - len(msg))
        print("\r" + msg[:100] + " " * pad, end="", flush=True)
        state["last"] = len(msg[:100])

    run = analyze(target, opt, progress=progress)
    if state["last"]:
        print("\r" + " " * (state["last"] + 2) + "\r", end="")

    report.print_console(run, color=not a.no_color and report.enable_color())

    if not a.no_html:
        if a.html:
            out = Path(a.html).expanduser()
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = run.root.name or "root"
            out = HERE / "reports" / f"{name}_{stamp}.html"
        report.write_html(run, out)
        print(f"리포트: {out}")
        if a.open:
            webbrowser.open(out.resolve().as_uri())

    if a.json:
        jout = Path(a.json).expanduser()
        report.write_json(run, jout)
        print(f"JSON:  {jout}")

    counts = run.counts()
    if a.fail_on == "error" and counts[ERROR]:
        return 1
    if a.fail_on == "warn" and (counts[ERROR] or counts[WARN]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `| head` 처럼 출력을 중간에 끊었을 때
        try:
            sys.stdout.close()
        except Exception:
            # 이미 끊긴 파이프라 닫기 실패도 무시
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n중단됨", file=sys.stderr)
        sys.exit(130)
