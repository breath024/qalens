"""HTML 정적 검사. 단일 HTML 산출물(덱/대시보드/mvp)이 주 대상."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..core import ERROR, INFO, WARN, FileReport, strip_js_noise

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# 닫는 태그를 생략해도 되는 요소들 — 안 닫혔다고 에러 내면 안 된다.
OPTIONAL_CLOSE = {
    "p", "li", "dt", "dd", "td", "th", "tr", "thead", "tbody", "tfoot",
    "option", "optgroup", "colgroup", "rt", "rp", "html", "head", "body",
}

INTERACTIVE_NO_TEXT = {"button", "a"}


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[tuple[str, int]] = []
        self.problems: list[tuple[int, str, str, str, str]] = []  # line, sev, rule, msg, snippet
        self.ids: dict[str, int] = {}
        self.classes_used: set[str] = set()
        self.anchor_targets: list[tuple[str, int]] = []
        self.local_refs: list[tuple[str, int, str]] = []   # url, line, 속성명
        self.scripts: list[tuple[str, int]] = []           # 인라인 JS 본문, 시작줄
        self.styles: list[tuple[str, int]] = []            # 인라인 CSS 본문, 시작줄
        self.has_charset = False
        self.has_viewport = False
        self.has_title = False
        self.has_lang = False
        self._capture: str | None = None
        self._buf: list[str] = []
        self._buf_line = 0
        self._text_stack: list[list[str]] = []
        self._pending_labels: set[str] = set()
        self._labelled_ids: set[str] = set()
        self._form_fields: list[tuple[str, str, int]] = []  # tag, id, line
        self._svg_depth = 0     # SVG/MathML 안은 XML 규칙이라 self-closing 이 유효하다

    # -- 유틸 -------------------------------------------------------
    def _p(self, sev, rule, msg, snippet=""):
        self.problems.append((self.getpos()[0], sev, rule, msg, snippet))

    def _attrmap(self, attrs, line):
        d: dict[str, str | None] = {}
        for k, v in attrs:
            k = k.lower()
            if k in d:
                self.problems.append(
                    (line, WARN, "html/dup-attr",
                     f"`{k}` 속성이 한 태그에 두 번 — 뒤엣것은 무시됨", k)
                )
            d[k] = v
        return d

    # -- 파서 콜백 ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        tag = tag.lower()
        a = self._attrmap(attrs, line)

        if tag in ("svg", "math"):
            self._svg_depth += 1

        if tag == "html":
            self.has_lang = bool(a.get("lang"))
        if tag == "meta":
            if "charset" in a or (a.get("http-equiv", "") or "").lower() == "content-type":
                self.has_charset = True
            if (a.get("name", "") or "").lower() == "viewport":
                self.has_viewport = True
        if tag == "title":
            self.has_title = True

        el_id = a.get("id")
        if el_id:
            if el_id in self.ids:
                self.problems.append(
                    (line, ERROR, "html/dup-id",
                     f"id=\"{el_id}\" 가 {self.ids[el_id]}번째 줄에 이미 있음 — "
                     "getElementById/querySelector 는 앞엣것만 잡는다", f'id="{el_id}"')
                )
            else:
                self.ids[el_id] = line
            if el_id.strip() != el_id or " " in el_id:
                self.problems.append(
                    (line, ERROR, "html/bad-id", f"id 에 공백이 들어 있음: \"{el_id}\"", el_id))

        for cls in (a.get("class") or "").split():
            self.classes_used.add(cls)

        for attr in ("href", "src", "poster"):
            v = a.get(attr)
            if not v:
                continue
            v = v.strip()
            if v.startswith("#"):
                if len(v) > 1:
                    self.anchor_targets.append((v[1:], line))
                continue
            if urlparse(v).scheme or v.startswith("//") or v.startswith("data:"):
                continue
            self.local_refs.append((v, line, attr))

        if tag == "a":
            href = (a.get("href") or "").strip()
            if not href:
                self._p(WARN, "html/empty-href", "<a> 에 href 가 비어 있음 — 링크로 동작하지 않음")
            if (a.get("target") or "").lower() == "_blank":
                rel = (a.get("rel") or "").lower()
                if "noopener" not in rel and "noreferrer" not in rel:
                    self._p(WARN, "a11y/blank-noopener",
                            'target="_blank" 인데 rel="noopener" 가 없음 — 새 탭이 원본 창을 조작할 수 있음',
                            'target="_blank"')

        if tag == "img" and a.get("alt") is None:
            self._p(INFO, "a11y/img-alt", "<img> 에 alt 가 없음",
                    f'src="{(a.get("src") or "")[:60]}"')

        if tag == "input":
            itype = (a.get("type") or "text").lower()
            if itype not in ("hidden", "submit", "button", "reset", "image"):
                if not (a.get("aria-label") or a.get("placeholder") or a.get("title")):
                    self._form_fields.append((tag, el_id or "", line))
        if tag in ("select", "textarea"):
            if not (a.get("aria-label") or a.get("title")):
                self._form_fields.append((tag, el_id or "", line))

        if tag == "label":
            f = a.get("for")
            if f:
                self._labelled_ids.add(f)

        if tag == "script":
            src = a.get("src")
            self._capture = "script" if not src else None
            self._buf, self._buf_line = [], line
        elif tag == "style":
            self._capture = "style"
            self._buf, self._buf_line = [], line

        if tag in INTERACTIVE_NO_TEXT:
            self._text_stack.append([])

        if tag not in VOID:
            self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):
        # <div /> 같은 self-closing 은 HTML 에서 void 가 아니면 무시된다.
        self.handle_starttag(tag, attrs)
        tag = tag.lower()
        if tag in ("svg", "math"):
            self._svg_depth = max(0, self._svg_depth - 1)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
            if tag in INTERACTIVE_NO_TEXT and self._text_stack:
                self._text_stack.pop()
            if self._svg_depth == 0:
                self._p(WARN, "html/self-closing",
                        f"<{tag} /> — HTML 에서 self-closing 은 무시된다. 여는 태그로 취급됨",
                        f"<{tag} />")

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)
        if self._text_stack:
            for buf in self._text_stack:
                buf.append(data)

    def handle_entityref(self, name):
        if self._text_stack:
            for buf in self._text_stack:
                buf.append("&")

    def handle_charref(self, name):
        self.handle_entityref(name)

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        tag = tag.lower()

        if tag in ("svg", "math"):
            self._svg_depth = max(0, self._svg_depth - 1)

        if self._capture == "script" and tag == "script":
            self.scripts.append(("".join(self._buf), self._buf_line))
            self._capture = None
        elif self._capture == "style" and tag == "style":
            self.styles.append(("".join(self._buf), self._buf_line))
            self._capture = None
        elif tag in ("script", "style"):
            self._capture = None

        if tag in INTERACTIVE_NO_TEXT and self._text_stack:
            txt = "".join(self._text_stack.pop()).strip()
            if not txt:
                self.problems.append(
                    (line, INFO, "a11y/empty-control",
                     f"<{tag}> 안에 텍스트가 없음 — 스크린리더가 읽을 게 없다", f"</{tag}>"))

        if tag in VOID:
            self.problems.append(
                (line, WARN, "html/void-endtag", f"</{tag}> — 닫는 태그가 없는 요소다", f"</{tag}>"))
            return

        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for unclosed, uline in self.stack[i + 1:]:
                    if unclosed not in OPTIONAL_CLOSE:
                        self.problems.append(
                            (uline, ERROR, "html/unclosed",
                             f"<{unclosed}> 가 닫히기 전에 </{tag}> 가 나옴 — 중첩이 어긋났다",
                             f"<{unclosed}>"))
                del self.stack[i:]
                return

        self.problems.append(
            (line, ERROR, "html/stray-endtag",
             f"</{tag}> 에 짝이 되는 여는 태그가 없음", f"</{tag}>"))

    def close(self):
        super().close()
        for tag, line in self.stack:
            if tag not in OPTIONAL_CLOSE:
                self.problems.append(
                    (line, ERROR, "html/unclosed",
                     f"<{tag}> 가 끝까지 닫히지 않음", f"<{tag}>"))
        for tag, el_id, line in self._form_fields:
            if el_id and el_id in self._labelled_ids:
                continue
            self.problems.append(
                (line, INFO, "a11y/unlabelled-field",
                 f"<{tag}> 에 label/aria-label/placeholder 가 하나도 없음",
                 f'<{tag} id="{el_id}">' if el_id else f"<{tag}>"))


_DOM_LOOKUP = re.compile(
    r"""getElementById\(\s*['"]([^'"]+)['"]|querySelector(?:All)?\(\s*['"]#([A-Za-z_][\w\-]*)['"]"""
)
_DYNAMIC = re.compile(r"\binnerHTML\b|\bcreateElement\b|insertAdjacentHTML|\.append\(|outerHTML")
_CSS_CLASS_DEF = re.compile(r"\.(-?[_A-Za-z][\w\-]*)")


def run(rep: FileReport, text: str, path: Path) -> dict:
    """반환: {'scripts': [(src, line)], 'styles': [...], 'ids': {...}}"""
    parser = _Parser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as e:  # HTMLParser 는 어지간해선 안 죽지만 방어
        rep.add(0, ERROR, "html/parse-crash", f"HTML 파싱 중 예외: {e}")
        return {"scripts": [], "styles": [], "ids": {}}

    for line, sev, rule, msg, snippet in parser.problems:
        rep.add(line, sev, rule, msg, snippet)

    if not parser.has_charset and any(ord(c) > 127 for c in text[:20000]):
        rep.add(1, ERROR, "html/no-charset",
                "비ASCII(한글) 문자가 있는데 <meta charset> 선언이 없음 — 브라우저에서 깨질 수 있음",
                hint='<head> 맨 위에 <meta charset="utf-8"> 추가')
    if not parser.has_title:
        rep.add(1, WARN, "html/no-title", "<title> 이 없음 — 탭 이름이 파일 경로로 나온다")
    if not parser.has_viewport:
        rep.add(1, INFO, "html/no-viewport",
                "viewport meta 가 없음 — 모바일에서 데스크톱 폭으로 렌더된다")
    if not parser.has_lang:
        rep.add(1, INFO, "a11y/no-lang", '<html> 에 lang 속성이 없음 (한국어면 lang="ko")')

    # 앵커 링크가 실제 id 를 가리키는지
    for target, line in parser.anchor_targets:
        t = unquote(target)
        if t == "top" or t in parser.ids:
            continue
        if re.search(rf'\bname\s*=\s*["\']{re.escape(t)}["\']', text):
            continue
        rep.add(line, WARN, "html/dead-anchor",
                f'href="#{target}" 가 가리키는 id 가 문서에 없음 — 눌러도 아무 데도 안 감',
                f'href="#{target}"')

    # 로컬 파일 참조가 실제로 있는지
    base = path.parent
    for url, line, attr in parser.local_refs:
        clean = unquote(url.split("?")[0].split("#")[0])
        if not clean or clean.startswith("{{") or "${" in clean:
            continue
        # `/asset.png` 는 서버 루트 기준이라 파일 시스템에서 확인할 수 없다.
        if clean.startswith("/"):
            candidate = base / clean.lstrip("/")
            if not candidate.exists():
                rep.add(line, INFO, "html/absolute-path",
                        f"{attr}=\"{url}\" 는 서버 루트 기준 경로 — "
                        "파일로 직접 열면 항상 깨진다 (배포 서버에서는 정상일 수 있음)",
                        f'{attr}="{url}"')
            continue
        try:
            if not (base / clean).exists():
                rep.add(line, ERROR, "html/missing-file",
                        f"{attr}=\"{url}\" 가 가리키는 파일이 없음",
                        f'{attr}="{url}"',
                        "경로 오타이거나 파일이 다른 폴더로 옮겨진 것")
        except (OSError, ValueError):
            # 윈도우에서 쓸 수 없는 문자가 든 경로 — 참조 확인만 건너뛴다
            pass

    # 인라인 JS 가 참조하는 id 가 문서에 있는지
    all_js = "\n".join(s for s, _ in parser.scripts)
    dynamic = bool(_DYNAMIC.search(all_js))
    for src, start_line in parser.scripts:
        clean = strip_js_noise(src)
        # strip_js_noise 는 문자열 속을 지우므로, 원본에서 찾되 주석 여부만 대조한다.
        for m in _DOM_LOOKUP.finditer(src):
            el_id = m.group(1) or m.group(2)
            if el_id in parser.ids:
                continue
            if clean[m.start():m.start() + 3].strip() == "" and src[m.start():m.start() + 3].strip() != "":
                continue  # 주석 안이었다
            line = start_line + src.count("\n", 0, m.start())
            rep.add(line, INFO if dynamic else WARN, "js/missing-element",
                    f'"{el_id}" 를 찾는데 그런 id 를 가진 요소가 HTML 에 없음'
                    + (" (동적 생성 코드가 있어 확정은 아님)" if dynamic else " — null 이 반환된다"),
                    m.group(0),
                    "id 오타이거나 요소를 지우고 스크립트를 안 고친 경우")

    # CSS 에 정의됐는데 마크업에서 안 쓰이는 클래스
    all_css = "\n".join(s for s, _ in parser.styles)
    if all_css and parser.classes_used:
        defined = set(_CSS_CLASS_DEF.findall(all_css))
        dyn_class = "classList" in all_js or "className" in all_js
        unused = sorted(defined - parser.classes_used)
        if unused and not dyn_class:
            rep.add(0, INFO, "css/unused-class",
                    f"스타일은 있는데 마크업에서 안 쓰이는 클래스 {len(unused)}개",
                    ", ".join("." + c for c in unused[:8]))
        undefined = sorted(c for c in parser.classes_used if c not in defined)
        if undefined and len(defined) > 3:
            rep.add(0, INFO, "css/undefined-class",
                    f"마크업에서 쓰는데 스타일 정의가 없는 클래스 {len(undefined)}개 (외부 CSS 사용 시 무시)",
                    ", ".join("." + c for c in undefined[:8]))

    return {"scripts": parser.scripts, "styles": parser.styles, "ids": parser.ids}
