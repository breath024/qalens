"""CSS 정적 검사."""
from __future__ import annotations

import re

from ..core import ERROR, INFO, WARN, FileReport, line_of, line_text

KNOWN_PROPS = set("""
accent-color align-content align-items align-self all animation animation-delay
animation-direction animation-duration animation-fill-mode animation-iteration-count
animation-name animation-play-state animation-timing-function appearance aspect-ratio
backdrop-filter backface-visibility background background-attachment background-blend-mode
background-clip background-color background-image background-origin background-position
background-position-x background-position-y background-repeat background-size block-size
border border-block border-bottom border-bottom-color border-bottom-left-radius
border-bottom-right-radius border-bottom-style border-bottom-width border-collapse
border-color border-image border-image-slice border-image-source border-image-width
border-inline border-left border-left-color border-left-style border-left-width
border-radius border-right border-right-color border-right-style border-right-width
border-spacing border-style border-top border-top-color border-top-left-radius
border-top-right-radius border-top-style border-top-width border-width bottom
box-decoration-break box-shadow box-sizing break-after break-before break-inside
caption-side caret-color clear clip clip-path color color-scheme column-count column-fill
column-gap column-rule column-span column-width columns contain container container-name
container-type content counter-increment counter-reset cursor direction display empty-cells
field-sizing filter flex flex-basis flex-direction flex-flow flex-grow flex-shrink flex-wrap
float font font-display font-family font-feature-settings font-kerning font-optical-sizing
font-size font-size-adjust font-stretch font-style font-synthesis font-variant
font-variant-numeric font-variation-settings font-weight gap grid grid-area
grid-auto-columns grid-auto-flow grid-auto-rows grid-column grid-column-end grid-column-gap
grid-column-start grid-gap grid-row grid-row-end grid-row-gap grid-row-start grid-template
grid-template-areas grid-template-columns grid-template-rows hanging-punctuation height
hyphens image-rendering inline-size inset inset-block inset-inline isolation justify-content
justify-items justify-self left letter-spacing line-break line-height list-style
list-style-image list-style-position list-style-type margin margin-block margin-bottom
margin-inline margin-left margin-right margin-top mask mask-image mask-size max-block-size
max-height max-inline-size max-width min-block-size min-height min-inline-size min-width
mix-blend-mode object-fit object-position offset opacity order orphans outline
outline-color outline-offset outline-style outline-width overflow overflow-anchor
overflow-wrap overflow-x overflow-y overscroll-behavior overscroll-behavior-x
overscroll-behavior-y padding padding-block padding-bottom padding-inline padding-left
padding-right padding-top page-break-after page-break-before page-break-inside
paint-order perspective perspective-origin place-content place-items place-self
pointer-events position quotes resize right rotate row-gap scale scroll-behavior
scroll-margin scroll-padding scroll-snap-align scroll-snap-stop scroll-snap-type
scrollbar-color scrollbar-gutter scrollbar-width shape-outside src tab-size table-layout
text-align text-align-last text-combine-upright text-decoration text-decoration-color
text-decoration-line text-decoration-style text-decoration-thickness text-emphasis
text-indent text-justify text-orientation text-overflow text-rendering text-shadow
text-transform text-underline-offset text-wrap top touch-action transform transform-box
transform-origin transform-style transition transition-behavior transition-delay
transition-duration transition-property transition-timing-function translate
unicode-bidi unicode-range user-select vertical-align view-transition-name visibility
white-space widows width will-change word-break word-spacing word-wrap writing-mode
z-index zoom
""".split())

# CSS 로 지정할 수 있는 SVG 프레젠테이션 속성 — 오타가 아니다.
KNOWN_PROPS |= set("""
alignment-baseline baseline-shift clip-rule color-interpolation
color-interpolation-filters cx cy d dominant-baseline fill fill-opacity fill-rule
flood-color flood-opacity glyph-orientation-vertical lighting-color marker-end
marker-mid marker-start mask-type r rx ry shape-rendering stop-color stop-opacity
stroke stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin
stroke-miterlimit stroke-opacity stroke-width text-anchor vector-effect x y x1 x2 y1 y2
""".split())

_AT_RULE = re.compile(r"@[\w-]+")
_HEX = re.compile(r"#([0-9a-fA-F]+)\b")


def _split_blocks(src: str):
    """(선택자, 선택자시작오프셋, 본문, 본문시작오프셋) 목록. @media 등 중첩도 훑는다."""
    out = []
    depth = 0
    sel_start = 0
    i = 0
    n = len(src)
    stack: list[int] = []
    while i < n:
        c = src[i]
        if c == "{":
            sel = src[sel_start:i]
            stack.append(i)
            depth += 1
            body_start = i + 1
            # 중첩 블록(@media)의 본문은 재귀적으로 다시 훑히므로 여기선 선택자만 기록
            out.append([sel.strip(), sel_start, None, body_start])
            sel_start = i + 1
            i += 1
            continue
        if c == "}":
            if stack:
                open_i = stack.pop()
                depth -= 1
                for entry in reversed(out):
                    if entry[3] == open_i + 1 and entry[2] is None:
                        entry[2] = src[open_i + 1:i]
                        break
            sel_start = i + 1
            i += 1
            continue
        i += 1
    return [(s, so, b or "", bo) for s, so, b, bo in out]


def run(rep: FileReport, src: str, base_line: int = 1, origin: str = "") -> None:
    tag = f" ({origin})" if origin else ""
    # 주석 제거(길이 보존)
    clean = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.S)

    opens = clean.count("{")
    closes = clean.count("}")
    if opens != closes:
        rep.add(base_line, ERROR, "css/unbalanced",
                f"중괄호 개수가 안 맞음 (여는 {opens}개 / 닫는 {closes}개) — "
                "그 뒤 스타일이 통째로 무시된다" + tag)

    seen_selectors: dict[str, int] = {}
    for sel, sel_off, body, body_off in _split_blocks(clean):
        sel_line = base_line + line_of(clean, sel_off) - 1
        if not sel:
            continue
        if sel.startswith("@"):
            continue

        norm = re.sub(r"\s+", " ", sel).strip()
        if norm in seen_selectors:
            rep.add(sel_line, INFO, "css/dup-selector",
                    f"선택자 `{norm[:60]}` 가 {seen_selectors[norm]}번째 줄에 이미 있음 — "
                    "규칙이 흩어져 있으면 나중에 어느 쪽이 이기는지 헷갈린다" + tag)
        else:
            seen_selectors[norm] = sel_line

        if body.strip() == "":
            rep.add(sel_line, INFO, "css/empty-rule", f"`{norm[:60]}` 규칙이 비어 있음" + tag)
            continue

        props_here: dict[str, int] = {}
        for decl in body.split(";"):
            if not decl.strip() or "{" in decl or "}" in decl:
                continue
            m = re.match(r"\s*([\w-]+)\s*:", decl)
            if not m:
                continue
            prop = m.group(1).lower()
            off = body_off + decl.index(m.group(1)) + body.index(decl)
            line = base_line + line_of(clean, off) - 1

            # 한 declaration 안에 property: 가 두 번 = 세미콜론 누락
            rest = decl[m.end():]
            extra = re.search(r"\n\s*([\w-]+)\s*:", rest)
            if extra and extra.group(1).lower() in KNOWN_PROPS:
                rep.add(line, ERROR, "css/missing-semicolon",
                        f"`{prop}` 선언 끝에 세미콜론이 없음 — "
                        f"뒤따르는 `{extra.group(1)}` 까지 같이 무효가 된다" + tag,
                        decl.strip()[:80])

            if prop in props_here:
                rep.add(line, WARN, "css/dup-prop",
                        f"같은 블록에서 `{prop}` 를 두 번 지정 "
                        f"({props_here[prop]}번째 줄 것이 덮인다)" + tag,
                        decl.strip()[:80])
            else:
                props_here[prop] = line

            if (prop not in KNOWN_PROPS and not prop.startswith("--")
                    and not prop.startswith("-webkit-") and not prop.startswith("-moz-")
                    and not prop.startswith("-ms-") and not prop.startswith("-o-")):
                rep.add(line, WARN, "css/unknown-prop",
                        f"`{prop}` 는 알려진 CSS 속성이 아님 — 오타면 이 줄은 통째로 무시된다" + tag,
                        decl.strip()[:80])

    for m in _HEX.finditer(clean):
        h = m.group(1)
        if len(h) not in (3, 4, 6, 8):
            line = base_line + line_of(clean, m.start()) - 1
            rep.add(line, ERROR, "css/bad-hex",
                    f"`#{h}` 는 유효한 hex 색이 아님 (3/4/6/8자리만 가능)" + tag,
                    line_text(clean, line_of(clean, m.start())).strip())

    n_imp = len(re.findall(r"!\s*important", clean))
    if n_imp >= 8:
        rep.add(base_line, INFO, "css/important-overuse",
                f"!important 가 {n_imp}번 — 우선순위 싸움이 시작된 신호" + tag)
