# 고객사 뉴스 모니터링

네이버 뉴스 검색 API로 담당 고객사별 최신 기사를 매일 자동 수집하고, 메이저 언론사 여부와 우선순위 태그(투자 > 수주 > 협업 > 자동화)를 붙여 모바일 친화적인 정적 페이지로 보여주는 프로젝트입니다. GitHub Actions가 매일 데이터를 갱신하고, GitHub Pages가 결과를 호스팅합니다.

## 폴더 구조

```
client-news-monitor/
├── index.html, style.css, app.js   # 정적 웹페이지 (모바일 최적화, 팀/셀/개인/고객명 필터)
├── data/news.json                  # 매일 자동 생성되는 뉴스 데이터
├── config/
│   ├── companies.json              # 고객 마스터(고객명 기준 그룹) - 팀/셀/담당자/SUB고객명 포함
│   ├── major_media.json            # 도메인 -> 언론사명 / 메이저 여부
│   └── priority_keywords.json      # 우선순위 태그 키워드 (투자>수주>협업>자동화)
├── scripts/
│   ├── fetch_news.py               # 뉴스 수집·중복제거·태깅·SUB고객명 매칭 스크립트
│   └── import_companies.py         # 엑셀 고객 마스터 -> companies.json 변환
└── .github/workflows/update-news.yml   # 매일 자동 실행 워크플로우
```

## 검색/표시 로직 요약

- **검색은 "고객명"(메인)으로만 수행**합니다. 같은 고객명 아래 여러 "SUB고객명"(사업부/지점 등)이 있어도 검색어로는 쓰지 않습니다.
- 수집된 기사의 제목·본문에 그룹 내 SUB고객명이 등장하면 카드에 **"관련: OO"** 뱃지로만 표시합니다 (검색에는 영향 없음).
- 화면 상단 필터 4종: **고객명 검색(텍스트)**, **팀**, **셀**, **개인(담당자)**. 담당자는 외근영업/내근영업 이름을 합쳐서 보여줍니다.
- 그 외 **우선순위 태그** 칩과 **메이저 언론사만 보기** 체크박스도 제공합니다.
- 기사 카드에는 매칭된 고객명뿐 아니라 팀/셀/담당자 정보도 함께 표시됩니다.

## 1. 네이버 뉴스 검색 API 키 발급

1. https://developers.naver.com 접속 후 로그인
2. 상단 메뉴 **Application > 애플리케이션 등록** 클릭
3. 애플리케이션 이름 입력 (예: 고객사뉴스모니터링), 사용 API에서 **검색** 선택
4. 등록 후 발급되는 **Client ID / Client Secret** 을 복사해 둡니다.
   - 검색 API는 하루 25,000회 호출까지 무료입니다.

## 2. GitHub 저장소 만들기

1. GitHub에서 새 저장소 생성 (예: `client-news-monitor`), Public 또는 Private 선택
2. 로컬에서 이 폴더 전체를 저장소에 push:
   ```bash
   cd client-news-monitor
   git init
   git add .
   git commit -m "init: 고객사 뉴스 모니터링 사이트"
   git branch -M main
   git remote add origin https://github.com/<GitHub계정>/<저장소이름>.git
   git push -u origin main
   ```

## 3. GitHub Secrets 등록 (API 키)

저장소 > **Settings > Secrets and variables > Actions > New repository secret** 에서 아래 2개를 등록합니다.

| Name | Value |
|---|---|
| `NAVER_CLIENT_ID` | 발급받은 Client ID |
| `NAVER_CLIENT_SECRET` | 발급받은 Client Secret |

## 4. GitHub Pages 활성화

저장소 > **Settings > Pages** 에서:
- Source: **Deploy from a branch**
- Branch: **main**, 폴더: **/(root)**
- 저장 후 몇 분 내로 `https://<GitHub계정>.github.io/<저장소이름>/` 주소로 접속 가능합니다.

## 5. 자동 갱신 확인 / 수동 실행

- `.github/workflows/update-news.yml` 은 매일 **한국시간 07:00**에 자동 실행되어 `data/news.json`을 갱신하고 커밋합니다.
- 즉시 테스트하려면 저장소 > **Actions > Update Client News > Run workflow** 로 수동 실행할 수 있습니다.
- 갱신 시각을 바꾸려면 워크플로우 파일의 `cron: "0 22 * * *"` 값을 수정하세요 (GitHub Actions는 UTC 기준, 한국시간은 UTC+9).

### 고객사 수가 많을 때 (수백~1만 개 이상) 유의할 점

- 고객사 1곳당 API 호출 1회이므로, 고객사가 많으면 워크플로우 실행 시간이 길어집니다 (예: 1만 곳 x 호출 간 0.2초 대기 ≈ 30분 이상 + 네트워크 시간).
- **Private 저장소는 GitHub Actions 무료 실행 시간(월 2,000분)이 제한**되어 있어, 매일 장시간 실행 시 한도를 초과할 수 있습니다. **Public 저장소는 Actions 실행 시간이 무제한**이므로, 사내 민감 정보가 없다면 Public으로 만드는 것을 권장합니다.
- 호출 간 대기 시간은 `NEWS_REQUEST_DELAY` 환경변수로 조정할 수 있습니다 (워크플로우 파일의 `env:` 항목에 추가).
- 실행 시간이 너무 길어지면 갱신 주기를 매일 대신 2~3일에 한 번으로 늘리는 것도 방법입니다 (`cron` 값 조정).

## 6. 담당 고객사 목록 반영 (엑셀 → companies.json)

`scripts/import_companies.py`는 두 가지 엑셀 형식을 자동으로 인식합니다.

**A) 고객 마스터 형식 (권장)** — 헤더에 아래 컬럼이 있으면 자동 인식됩니다.

| 고객번호 | 고객명 | SUB번호 | SUB고객명 | 팀구분 | 셀코드 | 외근영업 | 내근영업 | 업계 |
|---|---|---|---|---|---|---|---|---|

- (고객번호, 고객명) 기준으로 그룹핑하여, 같은 고객 아래 여러 SUB고객명/담당자를 하나로 묶습니다.
- 뉴스 검색어는 고객명에서 `(주)`, `㈜` 등 법인 표기와 `_ERP`, 지점/사업부 접미사를 자동으로 정리해 만듭니다 (예: "한미반도체(주)_ERP" → "한미반도체").

**B) 단순 회사명 목록** — 헤더에 `고객사`/`회사명`/`name` 등이 있으면 팀/셀 필터 없이 회사명 리스트로만 처리됩니다.

반영 절차:
```bash
pip install openpyxl
python scripts/import_companies.py 고객데이터.xlsx
```
`config/companies.json`이 자동 갱신됩니다. 커밋/푸시하면 다음 자동 갱신부터 반영됩니다. 필요하면 파일을 직접 열어 수동으로 편집해도 됩니다.

## 7. 우선순위 태그 / 메이저 언론사 기준 커스터마이징

- `config/priority_keywords.json`: 투자/수주/협업/자동화 각 태그에 매칭될 키워드를 자유롭게 추가·수정할 수 있습니다. 한 기사에 여러 태그 키워드가 있으면 `priority_order`에 명시된 순서 중 가장 앞선 태그가 채택됩니다.
- `config/major_media.json`: 기사 원문 도메인 기준 언론사명과 "메이저 언론사" 여부를 정의합니다. 목록에 없는 도메인은 비메이저로 처리되며, 필요한 언론사를 자유롭게 추가하세요.

## 8. 로컬에서 미리 확인하기

```bash
export NAVER_CLIENT_ID=발급받은_ID
export NAVER_CLIENT_SECRET=발급받은_SECRET
python scripts/fetch_news.py       # data/news.json 생성
python -m http.server 8000         # 로컬 서버 실행 후 http://localhost:8000 접속
```

## 중복 제거 로직 요약

- 동일한 원문 링크는 1건만 유지하고, 같은 날짜에 제목이 82% 이상 유사한 기사(동일 사건의 여러 매체 전재 등)는 하나만 남깁니다. 중복 판정 시 메이저 언론사 기사가 있으면 그 기사를 우선 채택합니다.
- 화면에는 우선순위 태그 순 → 그 안에서는 최신순으로 기사가 표시됩니다.
- 기본적으로 최근 7일 이내 기사만 노출합니다 (`NEWS_LOOKBACK_DAYS` 환경변수로 조정 가능).
