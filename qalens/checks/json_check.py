"""JSON / 설정 파일 검사."""
from __future__ import annotations

import json
import re

from ..core import ERROR, INFO, WARN, FileReport, line_text


def _pairs_hook(pairs):
    seen = {}
    dups = []
    for k, v in pairs:
        if k in seen:
            dups.append(k)
        seen[k] = v
    if dups:
        _pairs_hook.dups.extend(dups)
    return seen


def run(rep: FileReport, text: str) -> None:
    stripped = text.strip()
    if not stripped:
        rep.add(0, WARN, "json/empty", "파일이 비어 있음")
        return

    _pairs_hook.dups = []
    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as e:
        rep.add(e.lineno, ERROR, "json/syntax",
                f"JSON 문법 오류: {e.msg} ({e.lineno}행 {e.colno}열)",
                line_text(text, e.lineno).strip()[:120],
                _hint_for(e, text))
        return

    for k in dict.fromkeys(_pairs_hook.dups):
        m = re.search(rf'"{re.escape(k)}"\s*:', text)
        line = text.count("\n", 0, m.start()) + 1 if m else 0
        rep.add(line, ERROR, "json/dup-key",
                f'키 "{k}" 가 같은 객체 안에 두 번 — 파서는 뒤엣것만 남긴다', f'"{k}":')

    if isinstance(data, dict) and not data:
        rep.add(1, INFO, "json/empty-object", "최상위 객체가 비어 있음")

    # 흔한 실수: 주석을 넣어놨는데 JSON 은 주석을 모른다 (파싱은 됐지만 키로 들어간 경우)
    if isinstance(data, dict):
        for k in data:
            if k.startswith("//") or k.startswith("#") or k.startswith("_comment"):
                rep.add(0, INFO, "json/comment-key",
                        f'"{k}" — JSON 에 주석이 없어 키로 넣은 것으로 보임')


def run_lines(rep: FileReport, text: str) -> None:
    """JSON Lines(.jsonl/.ndjson) — 한 줄이 곧 하나의 JSON 문서다."""
    bad = 0
    for i, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            json.loads(raw)
        except json.JSONDecodeError as e:
            bad += 1
            if bad <= 3:
                rep.add(i, ERROR, "jsonl/syntax",
                        f"{i}행이 올바른 JSON 이 아님: {e.msg}",
                        raw.strip()[:120])
    if bad > 3:
        rep.add(0, ERROR, "jsonl/syntax",
                f"깨진 줄이 총 {bad}개 — 파일이 쓰이는 도중 잘렸을 수 있다")


_TRAILING = re.compile(r",\s*[}\]]")


def _hint_for(e, text) -> str:
    around = text[max(0, e.pos - 60):e.pos + 20]
    if _TRAILING.search(around):
        return "닫는 괄호 바로 앞 쉼표(trailing comma) — JSON 은 허용하지 않는다"
    if "'" in around:
        return "작은따옴표를 쓴 자리가 있는지 볼 것 — JSON 문자열은 큰따옴표만"
    if re.search(r"(?m)^\s*//", around) or "/*" in around:
        return "주석이 들어 있음 — JSON 은 주석을 지원하지 않는다"
    return "이 위치 직전에서 값이나 쉼표가 빠졌을 가능성이 높다"
