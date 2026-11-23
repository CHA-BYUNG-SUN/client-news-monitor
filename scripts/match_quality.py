# -*- coding: utf-8 -*-
"""
기사-고객사 매칭 품질 판정 (2026-11-23 추가)

배경: 고객명이 짧거나(온, 보고, ON) 일반 단어/다른 유명 실체와 겹치면(메이저리그, 법무법인 ○○)
     엉뚱한 기사가 대량으로 붙었다. 지금까지는 company_overrides.json 에 회사별 예외를
     사람이 넣어야만 막혔고, 새 거래처가 추가되면 바로 오탐이 쏟아졌다.

이 모듈은 예외 설정이 없어도 자동으로 동작하는 기본 판정을 제공한다.
  judge(title, description, company, override) -> (판정, 이유)
    판정: "ok"        화면/AI 요약에 사용
          "uncertain" 이름은 제대로 나오지만 이 회사 기사라는 근거가 약함 -> 보류함(uncertain_articles)
          "reject"    이름이 다른 단어의 일부로만 나옴 / 동음이의 실체 / 동사 활용 등 -> 버림

규칙 요약
 1) 단어 경계(모든 회사 기본 적용)
    - 앞: 한글/영문/숫자가 바로 붙으면 안 됨. 단 '주식회사', '(주)', '㈜' 바로 뒤는 허용.
    - 뒤: 영문/숫자가 붙으면 안 됨. 한글이 붙으면 조사 또는 회사 뒤에 붙는 말(그룹/측/공장 등)만 허용.
    - 긴 이름(한글 4자 이상)은 뒤에 다른 한글이 붙어도 허용하되, 다른 등록 고객명/알려진
      더 긴 이름의 일부(예: 라온테크 -> 라온테크놀로지)인 경우만 제외.
 2) 위험 이름 자동 판별: 한글 2자 이하, 영문/숫자 3자 이하, 또는 override name_risk="common".
    위험 이름은 아래 중 하나가 있어야 ok:
      a. 법인 표기와 붙어서 등장: (주)X, ㈜X, X(주), X㈜, 주식회사 X, X 주식회사, X社
      b. 회사 주체 표현: X 대표, X 대표이사, X 관계자, X 측은, X 회장, X 사장, X CEO
      c. 이름(경계 OK) 주변 window(기본 25자) 안에 업종 문맥 키워드
         (override context_keywords + 업종 기본 키워드)
    이름은 제대로 나오지만 a~c 가 없으면 uncertain.
 3) 동음이의 실체: 이름 바로 앞이 '법무법인/회계법인/특허법인/세무법인/재단법인/사단법인/
    학교법인/의료법인' 이면 그 등장은 무효.
 4) 동사 활용: 위험 이름 뒤에 ' 있다/있는/있어/싶/됐/했' 등이 오면 그 등장은 무효(예: '보고 있다').
 5) 대소문자: 영문 3자 이하 이름은 대소문자까지 같아야 인정(ON != on, Online).
"""

import re

_JOSA = ("으로부터", "으로서", "으로써", "에서는", "에서도", "에게서", "으로", "에서", "에게", "까지",
         "부터", "처럼", "보다", "마저", "조차", "이나", "이며", "이자", "이고", "이다", "와의", "과의",
         "로의", "에의", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "만",
         "측", "께", "랑", "엔", "선", "서", "야", "나", "든", "라도", "한테")
# 위험 이름(짧은 이름)에는 좁은 조사 목록만 허용 ('보고서'의 '서', '온라인'의 '라' 등 차단)
_JOSA_STRICT = ("으로", "에서", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "의", "에", "와",
                "과", "도", "로", "만", "측")
# 회사명 뒤에 붙어도 같은 회사를 가리키는 말
_SUFFIX_OK = ("그룹", "계열", "공장", "본사", "노조", "지회", "지부", "법인", "사업장", "사업부",
              "연구소", "연구원", "컨소시엄", "주식", "주가", "임직원", "직원", "대표", "회장", "사장",
              "社", "사는", "사가", "사의", "사와", "사에")
_CORP_PREFIX = ("주식회사", "(주)", "㈜", "(株)", "株式会社")
_CORP_SUFFIX = ("(주)", "㈜", " 주식회사", "주식회사", "社", "(株)")
_SUBJECT_AFTER = (" 대표", "대표이사", " 관계자", " 측은", " 측이", " 측도", "측은", "측이", " 회장", " 사장",
                  " ceo", " 공장", " 본사", " 임직원")
_HOMONYM_PREFIX = ("법무법인(유한)", "법무법인 (유한)", "법무법인", "회계법인", "특허법인", "세무법인", "재단법인", "사단법인", "학교법인",
                   "의료법인", "법률사무소", "합동법률", "노무법인", "관세법인", "대학교", "고등학교",
                   "중학교", "초등학교")
_VERB_AFTER = re.compile(r"^\s?(있|싶|됐|했|된다|한다|하는|되는|돼|해|봐|와서|왔|오는|온다|간다|갔|"
                         r"주|준|줬|줄|줘|나서|난|낸|내|자|면|며|니|고서|도록|지만|아서|어서)")

_HANGUL = re.compile(r"[가-힣]")
_WORDCH = re.compile(r"[가-힣A-Za-z0-9]")
_LATIN_ONLY = re.compile(r"^[A-Za-z0-9.&\-]+$")

# 업종별 기본 문맥 키워드(소문자 비교). override 의 context_keywords 와 합쳐서 쓴다.
_COMMON_CTX = ["제조", "공장", "설비", "장비", "자동화", "부품", "생산", "양산", "수주", "납품", "공급계약",
               "협력사", "로봇", "스마트팩토리", "스마트공장", "소재", "모듈", "정밀", "가공", "기계",
               "산업단지", "산단", "강소기업", "신제품", "준공", "착공", "증설", "제조사", "제조업체",
               "대표이사"]
_INDUSTRY_CTX = {
    "반도체": ["반도체", "웨이퍼", "hbm", "파운드리", "메모리", "패키징", "후공정", "전공정", "칩", "팹",
             "식각", "증착", "검사장비", "테스트"],
    "2차전지": ["배터리", "이차전지", "2차전지", "양극재", "음극재", "전해질", "분리막", "셀", "전지",
             "ess", "전기차"],
    "이차전지": ["배터리", "이차전지", "2차전지", "양극재", "음극재", "전해질", "분리막", "셀", "전지"],
    "자동차": ["자동차", "차량", "완성차", "전기차", "모빌리티", "자율주행", "차체", "부품사", "현대차",
            "기아", "타이어"],
    "디스플레이": ["디스플레이", "oled", "lcd", "패널", "마이크로led", "유리기판"],
    "스마트폰": ["스마트폰", "카메라모듈", "모바일", "폴더블", "갤럭시", "아이폰", "커넥터", "인쇄회로",
             "pcb", "fpcb"],
    "단자/커넥터": ["커넥터", "단자", "전선", "케이블"],
    "가전": ["가전", "냉장고", "세탁기", "에어컨"],
    "의료": ["의료기기", "의료", "임플란트", "진단", "초음파", "헬스케어"],
    "기타기계": ["기계", "공작기계"],
}

RISK_HANGUL_MAX = 2
RISK_LATIN_MAX = 3
DEFAULT_WINDOW = 25


def _norm(s):
    return (s or "").strip()


def is_risky_name(name, override=None):
    """짧은 이름/일반 단어 이름 자동 판별"""
    if override and override.get("name_risk") in ("common", "high"):
        return True
    if override and override.get("name_risk") == "low":
        return False
    n = re.sub(r"\s+", "", _norm(name))
    if not n:
        return False
    if _LATIN_ONLY.match(n):
        return len(re.sub(r"[^A-Za-z0-9]", "", n)) <= RISK_LATIN_MAX
    hangul = len(_HANGUL.findall(n))
    if hangul and hangul <= RISK_HANGUL_MAX and len(n) <= RISK_HANGUL_MAX + 1:
        return True
    return False


def context_keywords_for(company, override=None):
    kws = []
    if override and override.get("context_keywords"):
        kws += override["context_keywords"]
    for ind in company.get("industry", []) or []:
        kws += _INDUSTRY_CTX.get(ind, [])
    kws += _COMMON_CTX
    if override and override.get("context_keywords_only"):
        kws = list(override["context_keywords"])
    seen, out = set(), []
    for k in kws:
        k = k.lower()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _occurrences(text, name, case_sensitive):
    t = text if case_sensitive else text.lower()
    q = name if case_sensitive else name.lower()
    start = 0
    while q:
        idx = t.find(q, start)
        if idx < 0:
            return
        yield idx
        start = idx + 1


# 스니펫에서 띄어쓰기가 빠져 '...됐지만삼성전기' 처럼 붙는 경우(긴 이름만 허용)
_GLUED_ENDINGS = ("지만", "으며", "했고", "하며", "는데", "었다", "했다", "됐다", "이다", "한다", "에서", "하고")


def _prev_ok(text, idx, long_name=False):
    if idx == 0:
        return True
    prev = text[idx - 1]
    if not _WORDCH.match(prev):
        return True
    before = text[max(0, idx - 6):idx]
    if any(before.endswith(p) for p in _CORP_PREFIX):
        return True
    return long_name and any(before.endswith(e) for e in _GLUED_ENDINGS)


def _next_ok(text, end, name, long_name, longer_names, risky=False):
    rest = text[end:]
    nxt = rest[:1]
    if not nxt or not _WORDCH.match(nxt):
        return True
    if not _HANGUL.match(nxt):
        return False  # 영문/숫자가 바로 붙음 (ON -> ONE, 테스 -> 테스2)
    if long_name:
        # 긴 이름은 뒤 한글 허용, 단 알려진 더 긴 이름의 일부면 제외
        frag = (name + rest[:12]).lower()
        return not any(frag.startswith(ln) for ln in longer_names)
    if risky:
        return rest.startswith(_JOSA_STRICT) or rest.startswith(_SUFFIX_OK)
    return rest.startswith(_JOSA) or rest.startswith(_SUFFIX_OK)


_HOMONYM_NEAR = ("법무법인", "회계법인", "특허법인", "세무법인", "로펌", "변호사", "법률사무소", "노무법인")


def _homonym(text, idx, end, risky=True):
    """이름 바로 앞이 다른 실체 유형(법무법인 등)이거나, 이름 앞뒤 12자 안에 로펌/변호사 류 단어가 있으면 무효"""
    before = text[max(0, idx - 8):idx].rstrip()
    if any(before.endswith(p) for p in _HOMONYM_PREFIX):
        return True
    if not risky:
        return False
    near = text[max(0, idx - 12):idx] + " " + text[end:end + 12]
    return any(w in near for w in _HOMONYM_NEAR)


def _corp_marked(text, idx, end):
    before = text[max(0, idx - 6):idx]
    after = text[end:end + 6]
    if any(before.rstrip().endswith(p) for p in _CORP_PREFIX):
        return True
    if any(after.startswith(s) for s in _CORP_SUFFIX):
        return True
    return False


def _subject_marked(text_l, end):
    after = text_l[end:end + 6]
    return any(after.startswith(s) for s in _SUBJECT_AFTER)


def find_valid(text, name, risky, long_name, longer_names):
    """text 안에서 '제대로 된' 이름 등장 위치 목록 [(idx, end, 법인표기, 주체표현)]"""
    if not text or not name:
        return []
    case_sensitive = bool(_LATIN_ONLY.match(name)) and risky
    out = []
    for idx in _occurrences(text, name, case_sensitive):
        end = idx + len(name)
        if not _prev_ok(text, idx, long_name):
            continue
        if not _next_ok(text, end, name, long_name, longer_names, risky):
            continue
        if _homonym(text, idx, end, risky):
            continue
        if risky and _VERB_AFTER.match(text[end:end + 4]) and not _corp_marked(text, idx, end):
            continue
        out.append((idx, end, _corp_marked(text, idx, end), _subject_marked(text.lower(), end)))
    return out


def _clean_company(n):
    n = re.sub(r"_ERP$", "", n or "", flags=re.IGNORECASE)
    for tk in ("(주)", "㈜", "(유)", "주식회사", "유한회사"):
        n = n.replace(tk, "")
    n = re.sub(r"[-_].*$", "", n)  # '삼성전자㈜-생산기술연구소' -> 앞부분
    return re.sub(r"[\s\[\]\(\)]", "", n).strip()


def build_longer_names(companies, overrides=None, extra=()):
    """'라온테크' 가 '라온테크놀로지' 의 일부로 잡히는 것을 막기 위한 '알려진 이름' 목록(소문자).
    등록 고객명/SUB고객명 + override 의 known_other_names + extra."""
    names = set()
    for c in companies:
        for n in [c.get("name"), c.get("query")] + [s.get("clean") or s.get("raw") for s in c.get("sub_names", [])]:
            cn = _clean_company(n)
            if len(cn) >= 3:
                names.add(cn.lower())
    for o in (overrides or {}).values():
        if isinstance(o, dict):
            for n in o.get("known_other_names", []):
                names.add(n.lower())
    for n in extra:
        names.add(n.lower())
    return sorted(names)


def industry_keywords_for(company, override=None):
    """업종 특화 키워드만(공통 키워드 제외) - 일반명사 이름 회사에 쓰는 더 엄격한 기준"""
    kws = list((override or {}).get("context_keywords") or [])
    for ind in company.get("industry", []) or []:
        kws += _INDUSTRY_CTX.get(ind, [])
    return [k.lower() for k in kws if k]


def judge(title, description, names, company, override=None, longer_names=(), window=None, common=False):
    """기사 1건 x 회사 1곳 판정. names: 기사에 나와야 하는 이름 후보(match_names 등).
    common=True: 이 회사 이름이 이번 수집분에서 대부분 일반 단어/다른 실체로 쓰인 것으로 측정된 경우
                 (company_noise_ratio 참고) -> 법인표기 또는 '제목+업종 특화어'만 인정."""
    title = title or ""
    desc = description or ""
    names = [n for n in (_norm(x) for x in names) if n]
    if not names:
        return "ok", "no-name-config"
    if override and override.get("name_risk") == "common":
        common = True
    win = window or (int(override.get("context_window") or 0) if override else 0) or DEFAULT_WINDOW
    kws = context_keywords_for(company, override)
    ind_kws = industry_keywords_for(company, override)
    best = "reject"
    full_l = (title + " " + desc).lower()
    if not any(n.lower() in full_l for n in names):
        reason = "name-absent"
    else:
        reason = "name-only-inside-other-word"
    for nm in names:
        risky = is_risky_name(nm, override) or common
        long_name = (not risky) and len(re.sub(r"\s+", "", nm)) >= 4
        lnames = [ln for ln in longer_names if ln != nm.lower() and ln.startswith(nm.lower())]
        t_hits = find_valid(title, nm, risky, long_name, lnames)
        d_hits = find_valid(desc, nm, risky, long_name, lnames)
        if not t_hits and not d_hits:
            continue
        if not risky:
            return "ok", "name-boundary"
        hits = t_hits + d_hits
        if any(h[2] for h in hits):
            return "ok", "risky-corp-marked"
        if common:
            if t_hits and any(k in full_l for k in ind_kws):
                return "ok", "common-title+industry"
            best, reason = "uncertain", "common-name-weak"
            continue
        if any(h[3] for h in hits):
            return "ok", "risky-subject-marked"
        if t_hits and any(k in full_l for k in kws):
            return "ok", "risky-title+context"
        d_l = desc.lower()
        for h in d_hits:
            seg = d_l[max(0, h[0] - win): h[1] + win]
            if any(k in seg for k in kws):
                return "ok", "risky-desc+near-context"
        best, reason = "uncertain", "risky-no-context"
    return best, reason


def company_noise_ratio(texts, names, company, override=None, longer_names=()):
    """이번 수집분(texts=[(제목, 요약)])에서 이름이 등장한 기사 중 '다른 단어/다른 실체'로만 쓰인 비율.
    위험 이름 회사에서 이 값이 높으면(기본 0.5 이상) 일반명사/동음이의 이름으로 보고 common 모드로 판정한다."""
    seen = bad = 0
    for t, d in texts:
        v, why = judge(t, d, names, company, override, longer_names)
        if why == "name-absent":
            continue
        seen += 1
        if v == "reject":
            bad += 1
    return (bad / seen) if seen else 0.0, seen
