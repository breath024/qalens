# QALens

파일을 훑어 **종류를 스스로 판별**하고, 그 종류에 맞는 QA 검사를 돌린다.
정적 분석에 더해 **실제 브라우저로 열어보는 런타임 검사**까지 한다.

```
python qa.py <파일이나폴더>
```

끝나면 터미널 요약 + 자립형 HTML 리포트(`reports/`)가 나온다.

---

## 요구사항

- Python 3.10+ (표준 라이브러리만 사용)
- playwright + chromium — **런타임 검사에만** 필요. 없으면 정적 분석만 돌고 그 사실을 알려준다.
  ```
  pip install playwright
  playwright install chromium
  ```

## 사용법

```bash
python qa.py .                       # 현재 폴더 전체
python qa.py mvp.html --open         # 파일 하나, 끝나면 리포트를 브라우저로 염
python qa.py . --no-runtime          # 브라우저 없이 정적 검사만 (빠름)
python qa.py . --min error           # 오류만 보기
python qa.py app.py --deep           # 파이썬을 실제로 import 까지 해봄
python qa.py . --ignore "_backup/*" --ignore "*.gen.js"
python qa.py . --json out.json       # 결과를 JSON 으로도 저장
python qa.py . --fail-on error       # 오류가 있으면 종료코드 1 (CI/훅 용)
```

`qa.cmd` 를 PATH 에 올려두면 어느 폴더에서든 `qa .` 로 쓸 수 있다.

주요 옵션

| 옵션 | 뜻 |
|---|---|
| `--no-runtime` | 브라우저 렌더링·의존모듈 확인을 건너뛴다 |
| `--deep` | 파이썬 파일을 별도 프로세스에서 실제 import (모듈 최상단 코드가 **실행된다**) |
| `--min error\|warn\|info` | 보고할 최저 심각도 (기본 info=전부) |
| `--max-per-rule N` | 한 파일에서 같은 규칙을 N건까지만 펼침 (기본 5, 0=제한없음) |
| `--ignore 패턴` | 제외할 glob. 여러 번 지정 가능 |
| `--fail-on` | 종료코드로 실패를 알림 |

## 심각도

| | 뜻 |
|---|---|
| **오류** | 지금 깨져 있다. 실행하면 예외가 나거나 그 코드가 아예 무시된다 |
| **경고** | 지금은 돌지만 곧 문제가 된다 |
| **참고** | 정리하면 좋은 것. 기본 필터에서 접혀 있다 |

---

## 무엇을 보는가

### HTML
태그 중첩 어긋남 · 닫히지 않은 태그 · **중복 id**(getElementById 가 앞엣것만 잡는다) ·
`href="#..."` 가 가리키는 id 없음 · 없는 로컬 파일 참조 · 한글인데 `<meta charset>` 없음 ·
`<title>`/viewport/lang 누락 · 중복 속성 · void 요소의 닫는 태그 ·
`target="_blank"` + `rel="noopener"` 누락 · img alt · 라벨 없는 입력칸 · 텍스트 없는 버튼

인라인 `<script>`/`<style>` 은 꺼내서 아래 JS/CSS 검사를 그대로 돌린다.
JS 가 찾는 `getElementById('x')` 의 `x` 가 문서에 없으면 짚어준다
(동적 생성 코드가 있으면 단정하지 않고 급을 낮춘다).

### JavaScript
괄호 짝 · **같은 이름 중복 선언**(뒤엣것이 앞 구현을 덮는다) · `if (x = y)` 조건 안 대입 ·
`==`/`!=` · 빈 catch · `setTimeout("문자열")` · `document.write` · `debugger` 잔존 ·
console 잔존 · await 없는 async · `var` 사용
런타임: 브라우저에 파싱시켜 **문법 오류**를 잡는다 (ES 모듈은 건너뜀).

### CSS
중괄호 짝 · **세미콜론 누락**(뒤 선언까지 같이 죽는다) · 오타난 속성명 ·
같은 블록 중복 속성 · 중복 선택자 · 잘못된 hex 색 · 빈 규칙 · `!important` 남용 ·
정의됐지만 안 쓰이는 클래스

### Python (ast 기반이라 가장 정확하다)
문법 오류 · **정의 안 된 이름**(NameError 예고) · 미사용 import ·
**같은 이름 중복 def**(앞 정의가 통째로 죽는다) · 잘못된 인자 개수로 호출 ·
가변 기본값 · bare except · 조용히 삼키는 except · `== None` · 도달 불가 코드 ·
`assert (조건, "메시지")` 튜플 함정 · placeholder 없는 f-string ·
**`open()` 에 encoding 없음**(윈도우에서 CP949 로 읽혀 한글이 깨진다)
런타임: 설치 안 된 모듈 임포트 감지, `--deep` 이면 실제 import 시도

### JSON / JSONL
문법 오류(줄·열 + 원인 추정: trailing comma / 작은따옴표 / 주석) · **중복 키**
`.jsonl`/`.ndjson` 은 줄 단위로 검사한다 — 쓰다 만 파일의 잘린 줄을 잡는다.

### Kotlin / C#
파서를 내장하지 않아 **표층 검사**만 한다 — 괄호 짝 · 같은 이름 함수 ·
미사용 import · 빈 catch · Kotlin `!!` · 너무 긴 함수. 리포트에 표층이라고 표시된다.

### 모든 텍스트 파일 공통
인코딩(UTF-8 아님 / **한글 깨짐**) · 머지 충돌 마커 · CRLF 혼용 · 아주 긴 줄 ·
TODO/FIXME · **하드코딩된 API 키·토큰·비밀번호**

### 브라우저 런타임 (HTML)
로드 중 던져진 **예외** · 콘솔 error/warning · **404 나는 리소스** · 깨진 이미지 ·
가로 스크롤이 생기는 요소 · 로드는 됐는데 글자가 하나도 없는 빈 화면

`file://` 로 열어서 생기는 CORS·절대경로 실패는 따로 분류해 **참고**로 낮춘다
(서버에 올리면 사라지는 것들이라 오류로 치면 소음이 된다).

---

## 검사하지 않는 것

- `.min.js`·`.bundle.js` 같은 **남의 빌드 결과물**, 그리고 미니파이로 보이는 파일
  (한 줄이 2000자를 넘거나 평균 줄 길이가 비정상적으로 길면 생성 코드로 본다)
- `.bak` `.orig` `.tmp` `.rej` `~` 로 끝나는 **백업본**
- 3MB 넘는 텍스트, 바이너리
- 무시 폴더: `.git` `__pycache__` `node_modules` `venv` `dist` `build` `vendor`
  `third_party` `bin` `obj` `logs` 등

**데이터 파일**(`.srt` `.vtt` `.csv` `.tsv` `.log` `.jsonl`, 그리고 `.json`)은
줄이 길고 형식이 특이한 게 정상이라 스타일 검사를 걸지 않는다. 대신 인코딩 깨짐·
머지 충돌 마커·하드코딩된 키는 그대로 본다.

산출물 폴더(`dataset/`, `output/` 등)는 프로젝트마다 이름이 달라 자동으로는 못 거른다.
`--ignore "dataset/*"` 처럼 직접 빼는 편이 결과가 훨씬 읽기 좋다.

## 오탐이 나면

1. 특정 파일이면 `--ignore "경로패턴"`
2. 같은 지적이 반복되면 `--max-per-rule 2` 로 접거나 `--min error` 로 올린다
3. 규칙 자체가 틀렸으면 `qalens/checks/` 의 해당 파일을 고친다.
   규칙 ID(`css/unknown-prop` 등)로 검색하면 바로 나온다.

## 구조

```
qa.py                    CLI 진입점
qalens/
  core.py                Finding/FileReport, JS 스캐너(주석·문자열·정규식·템플릿 제거), 시크릿 탐지
  detect.py              확장자 + 내용 스니핑으로 파일 종류 판별, 인코딩 감지
  walker.py              대상 수집, 무시 규칙, 미니파이 판정
  engine.py              오케스트레이션, 같은 규칙 접기
  report.py              터미널 출력 + 자립형 HTML 리포트
  checks/                common · html_check · js_check · css_check · py_check · json_check · brace_check
  runtime/               browser(playwright) · pyrun(import 검사)
```

`core.strip_js_noise()` 가 이 도구의 핵심이다. 주석·문자열·정규식 리터럴을
길이를 보존한 채 공백으로 바꾸고, **템플릿 리터럴의 `${...}` 안은 코드로 남긴다**.
중첩 백틱을 문자열 종료로 오인하면 그 뒤 파싱이 통째로 어긋나므로 재귀로 훑는다.
