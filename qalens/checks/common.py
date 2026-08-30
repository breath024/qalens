"""확장자와 무관하게 모든 텍스트 파일에 적용하는 검사."""
from __future__ import annotations

import re

from ..core import ERROR, INFO, WARN, FileReport, scan_secrets

# UTF-8 을 CP949/latin-1 로 잘못 읽었을 때 나오는 선두 바이트열.
# 이 파일 자체가 이 규칙에 걸리지 않도록 문자를 직접 쓰지 않고 코드포인트로 만든다.
_MOJI_LEAD = "".join(chr(c) for c in (0xEC, 0xED, 0xEE, 0xEF, 0xC3, 0xC2))
_MOJIBAKE = re.compile(
    "[" + _MOJI_LEAD + "][" + chr(0x80) + "-" + chr(0xFF) + "]"
    + "|" + chr(0xFFFD)
)
_CONFLICT = re.compile(r"(?m)^(<{7} |={7}$|>{7} )")
_TODO = re.compile(r"(?i)\b(TODO|FIXME|XXX|HACK)\b[: ]?(.{0,80})")

LONG_LINE = 400


def run(rep: FileReport, text: str, encoding: str, is_data: bool = False) -> None:
    """is_data 면 자막·CSV 같은 데이터 파일 — 줄 길이 같은 스타일 검사는 걸지 않는다."""
    lines = text.splitlines()

    if encoding == "unknown":
        rep.add(0, ERROR, "encoding/undecodable",
                "UTF-8/CP949 어느 쪽으로도 온전히 읽히지 않음 (깨진 글자를 대체문자로 바꿔 검사함)",
                hint="파일을 UTF-8로 다시 저장할 것")
    elif encoding in ("cp949", "euc-kr"):
        rep.add(0, WARN, "encoding/not-utf8",
                f"{encoding} 로 저장된 파일 — 다른 도구에서 한글이 깨질 수 있음",
                hint="UTF-8로 변환할 것")

    for m in _MOJIBAKE.finditer(text):
        ln = text.count("\n", 0, m.start()) + 1
        rep.add(ln, ERROR, "encoding/mojibake",
                "한글이 깨진 흔적(모지바케)이 본문에 있음",
                lines[ln - 1] if ln <= len(lines) else "",
                "원본을 올바른 인코딩으로 다시 읽어 저장할 것")
        break  # 한 파일당 한 번만 알린다

    for m in _CONFLICT.finditer(text):
        ln = text.count("\n", 0, m.start()) + 1
        rep.add(ln, ERROR, "vcs/conflict-marker",
                "머지 충돌 마커가 남아 있음 — 파일이 그대로는 동작하지 않음",
                lines[ln - 1] if ln <= len(lines) else "")

    scan_secrets(rep, text)

    if is_data:
        # 자막·CSV·로그는 줄이 길고 TODO 같은 단어가 본문에 섞이는 게 정상이다.
        return

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        rep.add(0, INFO, "style/mixed-eol",
                f"줄바꿈이 섞여 있음 (CRLF {crlf}줄 / LF {lf}줄)")

    for i, ln_text in enumerate(lines, 1):
        if len(ln_text) > LONG_LINE:
            rep.add(i, INFO, "style/long-line",
                    f"{len(ln_text)}자짜리 줄 — 생성된 코드이거나 한 줄에 너무 많은 일을 함",
                    ln_text[:120] + "…")
            break

    for m in _TODO.finditer(text):
        ln = text.count("\n", 0, m.start()) + 1
        rep.add(ln, INFO, "todo/left",
                f"{m.group(1).upper()} 주석이 남아 있음",
                (lines[ln - 1] if ln <= len(lines) else "").strip())
