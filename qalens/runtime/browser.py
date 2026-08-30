"""실제 브라우저로 HTML 을 열어보는 런타임 검사.

정적 분석이 못 잡는 것 — 실행하다 터지는 예외, 404 나는 리소스,
로드 후에야 드러나는 레이아웃 넘침 — 만 본다.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import ERROR, INFO, WARN, FileReport

# file:// 로 열었기 때문에 생기는 제약. 서버에 올리면 사라지므로 따로 분류한다.
_FILE_PROTOCOL_NOISE = re.compile(
    r"(?i)cross[- ]origin|CORS|Access-Control-Allow-Origin|"
    r"blocked by CORS|from origin 'null'|file:// URLs are treated as opaque|"
    r'URL scheme "file" is not supported|scheme is not supported|'
    r"Not allowed to load local resource"
)
# file:// 로 열었을 때 나는 로컬 리소스 실패는 서버 경로 문제와 구분해서 보고한다.
_FILE_PROTOCOL_FAIL = re.compile(r"(?i)ERR_FAILED|ERR_ACCESS_DENIED|ERR_UNKNOWN_URL_SCHEME")


class BrowserRunner:
    def __init__(self, timeout_ms: int = 8000):
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self.error: str = ""

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.error = "playwright 가 설치되어 있지 않음 (pip install playwright && playwright install chromium)"
            return self
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        except Exception as e:
            self.error = f"브라우저를 띄우지 못함: {e}"
            self._cleanup()
        return self

    def __exit__(self, *exc):
        self._cleanup()
        return False

    def _cleanup(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            # 이미 죽은 브라우저를 닫는 경우 — 정리 실패는 결과에 영향 없다
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            # 위와 같음 — 정리 단계라 실패해도 보고할 것이 없다
            pass
        self._browser = self._pw = None

    @property
    def ok(self) -> bool:
        return self._browser is not None

    # ------------------------------------------------------------------
    def check_html(self, rep: FileReport, path: Path) -> None:
        if not self.ok:
            return
        console: list[tuple[str, str, int]] = []   # type, text, line
        page_errors: list[str] = []
        bad_requests: list[tuple[str, str]] = []   # url, 사유

        page = self._browser.new_page(viewport={"width": 1280, "height": 800})

        def on_console(msg):
            loc = msg.location or {}
            console.append((msg.type, msg.text, loc.get("lineNumber", 0) or 0))

        def on_pageerror(err):
            page_errors.append(str(err))

        def on_response(resp):
            if resp.status >= 400:
                bad_requests.append((resp.url, f"HTTP {resp.status}"))

        def on_requestfailed(req):
            failure = req.failure or "요청 실패"
            bad_requests.append((req.url, failure))

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)

        try:
            page.goto(path.resolve().as_uri(), wait_until="load", timeout=self.timeout_ms)
            page.wait_for_timeout(600)   # 지연 스크립트가 터질 시간을 준다
        except Exception as e:
            rep.add(0, WARN, "runtime/load-failed",
                    f"브라우저에서 열지 못했거나 {self.timeout_ms}ms 안에 로드가 끝나지 않음: "
                    f"{str(e).splitlines()[0][:160]}")
            page.close()
            return

        # 실행 중 던져진 예외 — 정적 분석이 절대 못 잡는 종류
        for err in page_errors:
            first = err.strip().splitlines()[0][:200]
            rep.add(0, ERROR, "runtime/exception",
                    f"페이지 로드 중 예외가 던져짐: {first}",
                    hint="이 시점 이후의 스크립트는 실행되지 않았을 수 있다")

        for ctype, text, line in console:
            if ctype not in ("error", "warning"):
                continue
            if _FILE_PROTOCOL_NOISE.search(text):
                rep.add(line, INFO, "runtime/file-protocol",
                        f"file:// 로 열어서 막힌 요청 (서버에 올리면 사라짐): {text[:160]}")
                continue
            if "favicon" in text.lower():
                continue
            # 리소스 로드 실패는 아래 bad_requests 쪽이 URL 까지 알려주므로 중복을 피한다.
            if text.startswith("Failed to load resource"):
                continue
            rep.add(line, ERROR if ctype == "error" else WARN,
                    f"runtime/console-{ctype}",
                    f"콘솔 {ctype}: {text[:200]}")

        page_dir = path.resolve().parent.as_uri().rstrip("/") + "/"
        seen_urls = set()
        for url, reason in bad_requests:
            if url in seen_urls or "favicon" in url.lower():
                continue
            seen_urls.add(url)
            short = url if len(url) < 90 else url[:60] + "…" + url[-25:]
            # 페이지 폴더 밖의 file:// 요청 = 서버 루트 기준 경로. 정적 검사가 이미 짚었다.
            outside = url.startswith("file://") and not url.startswith(page_dir)
            if outside or _FILE_PROTOCOL_FAIL.search(reason):
                rep.add(0, INFO, "runtime/file-protocol",
                        f"file:// 로 열어서 못 불러온 리소스 ({reason}): {short}",
                        hint="서버 루트 기준 경로이면 배포 환경에서는 정상일 수 있다")
                continue
            rep.add(0, ERROR, "runtime/bad-request",
                    f"리소스를 못 불러옴 ({reason}): {short}",
                    hint="경로 오타이거나 파일이 없는 것")

        # 로드 후에야 알 수 있는 것들
        try:
            metrics = page.evaluate("""() => {
                const d = document.documentElement;
                const overflowing = [...document.querySelectorAll('body *')]
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.right > d.clientWidth + 2;
                    })
                    .slice(0, 5)
                    .map(el => (el.tagName.toLowerCase()
                        + (el.id ? '#' + el.id : '')
                        + (el.className && typeof el.className === 'string'
                            ? '.' + el.className.trim().split(/\\s+/).join('.') : '')));
                return {
                    scrollW: d.scrollWidth,
                    clientW: d.clientWidth,
                    overflowing,
                    bodyText: (document.body ? document.body.innerText : '').trim().length,
                    imgsBroken: [...document.images]
                        .filter(i => i.complete && i.naturalWidth === 0)
                        .map(i => i.getAttribute('src') || '(src 없음)').slice(0, 5),
                };
            }""")
        except Exception:
            metrics = None

        if metrics:
            if metrics["scrollW"] > metrics["clientW"] + 2:
                rep.add(0, WARN, "runtime/h-overflow",
                        f"가로 스크롤이 생김 (내용 {metrics['scrollW']}px > 화면 {metrics['clientW']}px)",
                        ", ".join(metrics["overflowing"][:3]) or "",
                        "위 요소들이 화면 밖으로 나가 있다")
            if metrics["bodyText"] == 0:
                rep.add(0, ERROR, "runtime/blank-page",
                        "로드는 됐는데 화면에 보이는 글자가 하나도 없음 — 스크립트가 중간에 죽었을 가능성")
            already = " ".join(f.message for f in rep.findings)
            for src in metrics["imgsBroken"]:
                if src and src in already:      # 정적 검사가 이미 짚은 파일
                    continue
                rep.add(0, ERROR, "runtime/broken-image",
                        f"이미지가 깨져서 표시되지 않음: {src[:120]}")

        page.close()

    # ------------------------------------------------------------------
    def check_js_syntax(self, rep: FileReport, src: str, label: str = "") -> None:
        """JS 파일을 실행하지 않고 파싱만 시켜서 문법 오류를 잡는다."""
        if not self.ok:
            return
        page = self._browser.new_page()
        try:
            # AsyncFunction 으로 파싱해야 최상위 await 를 쓰는 스크립트가 오탐이 안 난다.
            res = page.evaluate(
                """(code) => {
                    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
                    try { new AsyncFunction(code); return null; }
                    catch (e) { return String(e && e.message || e); }
                }""",
                src,
            )
        except Exception as e:
            res = None
            rep.add(0, INFO, "runtime/js-check-failed", f"JS 문법 검사를 못 돌림: {e}")
        finally:
            page.close()

        if res:
            rep.add(0, ERROR, "js/syntax",
                    f"JavaScript 문법 오류: {res[:200]}" + (f" [{label}]" if label else ""),
                    hint="이 스크립트는 통째로 실행되지 않는다")
