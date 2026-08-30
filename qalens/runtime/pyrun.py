"""Python 런타임 검사.

기본은 '실행하지 않는' 검사만 한다 — 임포트하는 모듈이 실제로 설치돼 있는지
find_spec 으로 확인. --deep 을 주면 별도 프로세스에서 실제 import 까지 해본다.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

from ..core import ERROR, INFO, WARN, FileReport

_STDLIB = set(sys.stdlib_module_names)


def _top_level_imports(tree: ast.AST) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # 상대 임포트는 프로젝트 내부라 여기서 판단 못 한다
                continue
            if node.module:
                out.append((node.module.split(".")[0], node.lineno))
    return out


def uses_own_environment(root: Path) -> bool:
    """프로젝트가 자체 파이썬 환경을 쓰는 신호가 있는지.

    있으면 '지금 이 인터프리터에 없다'는 사실의 무게가 훨씬 가벼워진다.
    """
    marks = ("venv", ".venv", "env", "requirements.txt", "pyproject.toml",
             "Pipfile", "environment.yml", "poetry.lock")
    try:
        names = {p.name for p in root.iterdir()}
    except OSError:
        return False
    return bool(names & set(marks))


def check_imports(rep: FileReport, tree: ast.AST, path: Path,
                  seen: set | None = None, soft: bool = False) -> None:
    """설치되지 않은 서드파티 모듈을 임포트하고 있는지 — 실행하면 ImportError 로 죽는다.

    seen: 프로젝트 전체에서 이미 보고한 모듈. 같은 모듈을 파일마다 반복하지 않는다.
    soft: 프로젝트가 자체 환경을 쓰는 것으로 보이면 참고 급으로 낮춘다.
    """
    mods = {}
    for name, line in _top_level_imports(tree):
        if name in _STDLIB or name.startswith("_"):
            continue
        mods.setdefault(name, line)
    if not mods:
        return

    # 같은 폴더/상위 폴더의 로컬 모듈은 설치 여부와 무관하다
    local = set()
    for base in (path.parent, path.parent.parent):
        try:
            for p in base.iterdir():
                if p.suffix == ".py":
                    local.add(p.stem)
                elif p.is_dir() and (p / "__init__.py").exists():
                    local.add(p.name)
        except OSError:
            # 상위 폴더를 읽을 권한이 없는 경우 — 로컬 모듈 목록이 조금 부실해질 뿐이다
            pass

    seen = seen if seen is not None else set()
    todo = [m for m in mods if m not in local and m not in seen]
    if not todo:
        return

    code = textwrap.dedent("""
        import importlib.util, sys, json
        missing = []
        for m in json.loads(sys.argv[1]):
            try:
                if importlib.util.find_spec(m) is None:
                    missing.append(m)
            except (ImportError, ValueError, ModuleNotFoundError):
                missing.append(m)
        print(json.dumps(missing))
    """)
    try:
        import json as _json
        r = subprocess.run(
            [sys.executable, "-c", code, _json.dumps(todo)],
            capture_output=True, text=True, timeout=30,
            cwd=str(path.parent),
        )
        missing = _json.loads(r.stdout.strip() or "[]")
    except Exception:
        # 프로브 실행 자체가 실패하면 '설치 여부를 모른다'가 맞다 — 없는 것으로 단정하지 않는다
        return

    seen.update(todo)      # 있든 없든, 이 프로젝트에서는 다시 묻지 않는다
    for m in missing:
        if soft:
            rep.add(mods[m], INFO, "py/missing-module",
                    f"`{m}` 가 지금 이 파이썬에는 없음 "
                    "(프로젝트가 자체 가상환경을 쓰는 것으로 보여 참고로만 알림)",
                    f"import {m}")
        else:
            rep.add(mods[m], WARN, "py/missing-module",
                    f"`{m}` 를 임포트하는데 이 환경에 설치되어 있지 않음 — 실행하면 ModuleNotFoundError",
                    f"import {m}",
                    f"pip install {m} 하거나, 선택적 의존이면 try/except ImportError 로 감쌀 것")


def deep_import(rep: FileReport, path: Path, timeout: int = 20) -> None:
    """실제로 모듈을 import 해본다. 모듈 최상단 코드가 실행되므로 부작용에 주의."""
    code = textwrap.dedent("""
        import importlib.util, sys, traceback
        spec = importlib.util.spec_from_file_location("_qalens_probe", sys.argv[1])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_qalens_probe"] = mod
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except BaseException:
            traceback.print_exc()
            sys.exit(3)
    """)
    try:
        r = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(path.parent),
        )
    except subprocess.TimeoutExpired:
        rep.add(0, WARN, "py/import-hangs",
                f"import 하는 것만으로 {timeout}초 안에 끝나지 않음 — "
                "모듈 최상단에서 오래 걸리는 일을 하고 있다")
        return
    except Exception:
        return

    if r.returncode == 3:
        lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
        last = lines[-1] if lines else "알 수 없는 예외"
        where = ""
        for l in reversed(lines):
            if str(path.name) in l:
                where = l.strip()
                break
        rep.add(0, ERROR, "py/import-error",
                f"import 하는 것만으로 예외가 발생: {last[:180]}",
                where[:200],
                "모듈 최상단 코드를 함수 안이나 `if __name__ == \"__main__\"` 아래로 옮길 것")
    elif r.returncode not in (0, 3):
        err = (r.stderr or "").strip().splitlines()
        if err:
            rep.add(0, INFO, "py/import-exit",
                    f"import 프로브가 코드 {r.returncode} 로 종료: {err[-1][:160]}")
