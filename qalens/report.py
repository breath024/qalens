"""결과 출력 — 터미널 요약과 자립형 HTML 리포트."""
from __future__ import annotations

import html as _html
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .core import ERROR, INFO, WARN
from .engine import Run

_C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    ERROR: "\033[91m", WARN: "\033[93m", INFO: "\033[96m",
    "ok": "\033[92m", "path": "\033[95m",
}
_LABEL = {ERROR: "오류", WARN: "경고", INFO: "참고"}


def enable_color() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


def print_console(run: Run, color: bool, show_files: int = 40) -> None:
    c = _C if color else {k: "" for k in _C}
    counts = run.counts()
    total = sum(counts.values())

    checked = [r for r in run.reports if not r.skipped]
    kinds = Counter(r.kind for r in checked)

    print()
    print(f"{c['bold']}QALens{c['reset']}  {run.root}")
    print(f"{c['dim']}{'─' * 64}{c['reset']}")
    print(f"파일 {len(checked)}개 검사 "
          f"({', '.join(f'{k} {v}' for k, v in kinds.most_common())})"
          f"{c['dim']} · {run.elapsed:.1f}초{c['reset']}")
    if run.runtime_note:
        print(f"{c[WARN]}런타임 검사 건너뜀:{c['reset']} {run.runtime_note}")

    if total == 0:
        print(f"\n{c['ok']}지적할 것 없음.{c['reset']}\n")
        return

    with_findings = [r for r in run.reports if r.findings]
    with_findings.sort(key=lambda r: (
        -sum(1 for f in r.findings if f.severity == ERROR),
        -sum(1 for f in r.findings if f.severity == WARN),
        r.rel,
    ))

    for rep in with_findings[:show_files]:
        e = sum(1 for f in rep.findings if f.severity == ERROR)
        w = sum(1 for f in rep.findings if f.severity == WARN)
        i = sum(1 for f in rep.findings if f.severity == INFO)
        badge = "  ".join(x for x in [
            f"{c[ERROR]}오류 {e}{c['reset']}" if e else "",
            f"{c[WARN]}경고 {w}{c['reset']}" if w else "",
            f"{c[INFO]}참고 {i}{c['reset']}" if i else "",
        ] if x)
        print(f"\n{c['path']}{rep.rel}{c['reset']}  {c['dim']}[{rep.kind}]{c['reset']}  {badge}")
        for f in rep.findings[:12]:
            loc = f"{f.line}" if f.line else "–"
            print(f"  {c[f.severity]}{_LABEL[f.severity]}{c['reset']} "
                  f"{c['dim']}{loc:>5}{c['reset']}  {f.message}")
            if f.snippet:
                print(f"        {c['dim']}│ {f.snippet[:110]}{c['reset']}")
            if f.hint:
                print(f"        {c['dim']}→ {f.hint}{c['reset']}")
        if len(rep.findings) > 12:
            print(f"  {c['dim']}… 이 파일에 {len(rep.findings) - 12}건 더{c['reset']}")

    if len(with_findings) > show_files:
        print(f"\n{c['dim']}… 지적 사항이 있는 파일 {len(with_findings) - show_files}개 더 "
              f"(HTML 리포트에서 전체 확인){c['reset']}")

    print(f"\n{c['dim']}{'─' * 64}{c['reset']}")
    print(f"{c[ERROR]}오류 {counts[ERROR]}{c['reset']}  "
          f"{c[WARN]}경고 {counts[WARN]}{c['reset']}  "
          f"{c[INFO]}참고 {counts[INFO]}{c['reset']}   합계 {total}건")

    top = Counter(f.rule for r in run.reports for f in r.findings
                  if f.severity in (ERROR, WARN)).most_common(5)
    if top:
        print(f"{c['dim']}가장 잦은 규칙: " + ", ".join(f"{k}×{v}" for k, v in top) + c["reset"])
    print()


# ----------------------------------------------------------------------
_HTML_TMPL = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QALens 리포트 — __ROOTNAME__</title>
<style>
:root{
  --bg:#fbfaf8; --panel:#ffffff; --line:#e6e2dc; --ink:#1c1a17; --muted:#6f6a62;
  --err:#c0392b; --warn:#b5811a; --info:#2b6cb0; --ok:#2f855a;
  --err-bg:#fdf0ee; --warn-bg:#fdf7e8; --info-bg:#eef4fb;
  --code:#f4f2ee;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#16151a; --panel:#1e1d23; --line:#33313a; --ink:#eceaf0; --muted:#9a95a3;
  --err:#ff8a7a; --warn:#f2c266; --info:#82b4f0; --ok:#7fd1a2;
  --err-bg:#2c1e1e; --warn-bg:#2b2418; --info-bg:#1b2430; --code:#26252c;
}}
:root[data-theme=dark]{
  --bg:#16151a; --panel:#1e1d23; --line:#33313a; --ink:#eceaf0; --muted:#9a95a3;
  --err:#ff8a7a; --warn:#f2c266; --info:#82b4f0; --ok:#7fd1a2;
  --err-bg:#2c1e1e; --warn-bg:#2b2418; --info-bg:#1b2430; --code:#26252c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,"Segoe UI","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:6px}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}
.stat{flex:1;min-width:120px;border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;background:var(--panel)}
.stat b{display:block;font-size:26px;line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{font-size:12px;color:var(--muted)}
.stat.err b{color:var(--err)} .stat.warn b{color:var(--warn)}
.stat.info b{color:var(--info)} .stat.ok b{color:var(--ok)}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:14px 0 22px;
  position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:5;border-bottom:1px solid var(--line)}
button.f{border:1px solid var(--line);background:var(--panel);color:var(--ink);
  border-radius:999px;padding:5px 13px;font-size:13px;cursor:pointer}
button.f[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
input#q{flex:1;min-width:180px;border:1px solid var(--line);background:var(--panel);
  color:var(--ink);border-radius:8px;padding:6px 11px;font-size:13px}
details.file{border:1px solid var(--line);border-radius:10px;background:var(--panel);
  margin-bottom:10px;overflow:hidden}
details.file>summary{cursor:pointer;padding:11px 14px;display:flex;gap:10px;
  align-items:center;flex-wrap:wrap;list-style:none}
details.file>summary::-webkit-details-marker{display:none}
.fname{font-weight:600;font-family:ui-monospace,Consolas,monospace;font-size:13.5px;
  word-break:break-all}
.kind{font-size:11px;color:var(--muted);border:1px solid var(--line);
  border-radius:4px;padding:1px 6px}
.pill{font-size:11.5px;border-radius:999px;padding:1px 8px;font-variant-numeric:tabular-nums}
.pill.err{background:var(--err-bg);color:var(--err)}
.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.info{background:var(--info-bg);color:var(--info)}
.spacer{flex:1}
ul.list{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}
li.item{padding:11px 14px;border-bottom:1px solid var(--line);display:grid;
  grid-template-columns:52px 60px 1fr;gap:12px;align-items:start}
li.item:last-child{border-bottom:none}
.sev{font-size:11.5px;font-weight:700;padding:2px 0;text-align:center;border-radius:5px}
.sev.err{background:var(--err-bg);color:var(--err)}
.sev.warn{background:var(--warn-bg);color:var(--warn)}
.sev.info{background:var(--info-bg);color:var(--info)}
.line{color:var(--muted);font-family:ui-monospace,Consolas,monospace;font-size:12.5px;
  text-align:right;font-variant-numeric:tabular-nums;padding-top:2px}
.msg{min-width:0}
.rule{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:var(--muted)}
pre.snip{margin:6px 0 0;background:var(--code);border-radius:6px;padding:7px 10px;
  font-family:ui-monospace,Consolas,monospace;font-size:12.5px;overflow-x:auto;
  white-space:pre;color:var(--ink)}
.hint{margin-top:5px;font-size:13px;color:var(--muted)}
.hint::before{content:"→ "}
.empty{text-align:center;padding:56px 20px;color:var(--muted)}
.rules{margin-top:34px;border-top:1px solid var(--line);padding-top:18px}
.rules h2{font-size:15px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:5px 8px;border-bottom:1px solid var(--line)}
td.n{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted);width:60px}
td.r{font-family:ui-monospace,Consolas,monospace}
footer{margin-top:36px;color:var(--muted);font-size:12px}
</style>
<div class="wrap">
<header>
  <h1>QALens 리포트</h1>
  <span class="sub">__ROOT__</span>
  <span class="spacer"></span>
  <span class="sub">__WHEN__</span>
</header>
<div class="sub">__SUMMARYLINE__</div>

<div class="stats">
  <div class="stat err"><b>__NERR__</b><span>오류 · 지금 깨져 있음</span></div>
  <div class="stat warn"><b>__NWARN__</b><span>경고 · 곧 깨질 것</span></div>
  <div class="stat info"><b>__NINFO__</b><span>참고 · 정리할 것</span></div>
  <div class="stat ok"><b>__NCLEAN__</b><span>깨끗한 파일</span></div>
</div>

<div class="bar">
  <button class="f" data-sev="error" aria-pressed="true">오류</button>
  <button class="f" data-sev="warn" aria-pressed="true">경고</button>
  <button class="f" data-sev="info" aria-pressed="false">참고</button>
  <input id="q" placeholder="파일명 · 규칙 · 메시지로 거르기">
  <button class="f" id="expand">모두 펼치기</button>
</div>

<div id="files"></div>
<div class="empty" id="empty" hidden>조건에 맞는 지적 사항이 없습니다.</div>

<div class="rules">
  <h2>규칙별 집계</h2>
  <table id="ruletbl"></table>
</div>
<footer>QALens v__VER__ · 정적 분석__RUNTIMENOTE__</footer>
</div>
<script>
const DATA = __DATA__;
const SEVCLS = {error:'err', warn:'warn', info:'info'};
const SEVKO  = {error:'오류', warn:'경고', info:'참고'};
const active = new Set(['error','warn']);
const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function render(){
  const q = document.getElementById('q').value.trim().toLowerCase();
  const host = document.getElementById('files');
  host.innerHTML = '';
  let shown = 0;
  for(const f of DATA.files){
    const items = f.findings.filter(x =>
      active.has(x.severity) &&
      (!q || (f.rel + ' ' + x.rule + ' ' + x.message + ' ' + x.snippet).toLowerCase().includes(q)));
    if(!items.length) continue;
    shown += items.length;
    const n = {error:0, warn:0, info:0};
    items.forEach(x => n[x.severity]++);
    const d = document.createElement('details');
    d.className = 'file';
    d.open = n.error > 0;
    d.innerHTML =
      '<summary><span class="fname">' + esc(f.rel) + '</span>' +
      '<span class="kind">' + esc(f.kind) + '</span><span class="spacer"></span>' +
      (n.error ? '<span class="pill err">오류 ' + n.error + '</span>' : '') +
      (n.warn  ? '<span class="pill warn">경고 ' + n.warn + '</span>' : '') +
      (n.info  ? '<span class="pill info">참고 ' + n.info + '</span>' : '') +
      '</summary><ul class="list">' +
      items.map(x =>
        '<li class="item"><span class="sev ' + SEVCLS[x.severity] + '">' + SEVKO[x.severity] + '</span>' +
        '<span class="line">' + (x.line ? x.line : '–') + '</span><div class="msg">' +
        esc(x.message) + ' <span class="rule">' + esc(x.rule) + '</span>' +
        (x.snippet ? '<pre class="snip">' + esc(x.snippet) + '</pre>' : '') +
        (x.hint ? '<div class="hint">' + esc(x.hint) + '</div>' : '') +
        '</div></li>').join('') +
      '</ul>';
    host.appendChild(d);
  }
  document.getElementById('empty').hidden = shown > 0;
  renderRules();
}

function renderRules(){
  const c = {};
  for(const f of DATA.files)
    for(const x of f.findings)
      if(active.has(x.severity)) c[x.rule] = (c[x.rule] || 0) + 1;
  const rows = Object.entries(c).sort((a,b) => b[1] - a[1]).slice(0, 25);
  document.getElementById('ruletbl').innerHTML =
    rows.map(([r,n]) => '<tr><td class="r">' + esc(r) + '</td><td class="n">' + n + '</td></tr>').join('')
    || '<tr><td class="r">해당 없음</td><td class="n">0</td></tr>';
}

document.querySelectorAll('button.f[data-sev]').forEach(b => {
  b.addEventListener('click', () => {
    const s = b.dataset.sev;
    if(active.has(s)){ active.delete(s); b.setAttribute('aria-pressed','false'); }
    else { active.add(s); b.setAttribute('aria-pressed','true'); }
    render();
  });
});
document.getElementById('q').addEventListener('input', render);
document.getElementById('expand').addEventListener('click', e => {
  const all = [...document.querySelectorAll('details.file')];
  const open = all.some(d => !d.open);
  all.forEach(d => d.open = open);
  e.target.textContent = open ? '모두 접기' : '모두 펼치기';
});
render();
</script>
"""


def write_html(run: Run, out: Path) -> Path:
    from . import __version__

    counts = run.counts()
    checked = [r for r in run.reports if not r.skipped]
    clean = sum(1 for r in checked if not r.findings)

    files = [
        {
            "rel": r.rel,
            "kind": r.kind,
            "findings": [
                {"line": f.line, "severity": f.severity, "rule": f.rule,
                 "message": f.message, "snippet": f.snippet, "hint": f.hint}
                for f in r.findings
            ],
        }
        for r in run.reports if r.findings
    ]
    files.sort(key=lambda f: (
        -sum(1 for x in f["findings"] if x["severity"] == ERROR),
        -sum(1 for x in f["findings"] if x["severity"] == WARN),
        f["rel"],
    ))

    from collections import Counter as _Ctr
    kinds = _Ctr(r.kind for r in checked)
    summary = (f"파일 {len(checked)}개 검사 · "
               + ", ".join(f"{k} {v}" for k, v in kinds.most_common())
               + f" · {run.elapsed:.1f}초")

    note = " + 브라우저 런타임 검사" if run.runtime_used else ""
    if run.runtime_note:
        note = " · 런타임 검사 건너뜀: " + run.runtime_note

    body = (_HTML_TMPL
            .replace("__DATA__", json.dumps({"files": files}, ensure_ascii=False))
            .replace("__ROOTNAME__", _html.escape(run.root.name))
            .replace("__ROOT__", _html.escape(str(run.root)))
            .replace("__WHEN__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__SUMMARYLINE__", _html.escape(summary))
            .replace("__NERR__", str(counts[ERROR]))
            .replace("__NWARN__", str(counts[WARN]))
            .replace("__NINFO__", str(counts[INFO]))
            .replace("__NCLEAN__", str(clean))
            .replace("__VER__", __version__)
            .replace("__RUNTIMENOTE__", _html.escape(note)))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


def write_json(run: Run, out: Path) -> Path:
    payload = {
        "root": str(run.root),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(run.elapsed, 2),
        "counts": run.counts(),
        "files": [
            {
                "path": r.rel,
                "kind": r.kind,
                "skipped": r.skipped,
                "findings": [
                    {"line": f.line, "severity": f.severity, "rule": f.rule,
                     "message": f.message, "snippet": f.snippet, "hint": f.hint}
                    for f in r.findings
                ],
            }
            for r in run.reports if r.findings or r.skipped
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
