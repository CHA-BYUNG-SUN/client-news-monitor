(function () {
  "use strict";

  var PAGE_SIZE = 30;
  var SCROLL_TOP_THRESHOLD = 500;
  var COMPANY_MODAL_LIMIT = 6;
  var FRESH_HOURS = 24;
  var STALE_WARN_HOURS = 24;
  var STALE_DANGER_HOURS = 48;

  var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var state = {
    articles: [],
    nameCodeCount: {},
    currentCode: null,
    generatedAt: null,
    lookbackDays: null,
    totalArticles: 0,
    companySummaries: null,
    summaryLookbackDays: null,
    selectedTeam: "전체",
    selectedCell: "전체",
    selectedRep: "전체",
    majorOnly: false,
    searchText: "",
    visibleCount: PAGE_SIZE,
    prevIds: [],
    loadError: null,
    // 2026-11-24 사건 분류(ev)가 있는 데이터인지, 곁가지/주가 보기, 사업 소식 있는 고객만
    hasEv: false,
    bizDays: 90,
    showFold: false,
    bizOnly: false,
    groupIndex: {},
  };

  var el = {
    status: document.getElementById("pvStatus"),
    statusDot: document.getElementById("pvStatusDot"),
    statusText: document.getElementById("pvStatusText"),
    counts: document.getElementById("pvCounts"),
    searchInput: document.getElementById("pvSearchInput"),
    autocomplete: document.getElementById("pvAutocomplete"),
    teamSelect: document.getElementById("pvTeamSelect"),
    cellSelect: document.getElementById("pvCellSelect"),
    repSelect: document.getElementById("pvRepSelect"),
    resetBtn: document.getElementById("pvResetBtn"),
    feedTitle: document.getElementById("pvFeedTitle"),
    articleList: document.getElementById("pvArticleList"),
    emptyState: document.getElementById("pvEmptyState"),
    errorState: document.getElementById("pvErrorState"),
    loadMoreBtn: document.getElementById("pvLoadMoreBtn"),
    side: document.getElementById("pvSide"),
    sideToggleBtn: document.getElementById("pvSideToggleBtn"),
    sideCloseBtn: document.getElementById("pvSideCloseBtn"),
    sideBackdrop: document.getElementById("pvSideBackdrop"),
    companyList: document.getElementById("pvCompanyList"),
    sideCount: document.getElementById("pvSideCount"),
    topBtn: document.getElementById("pvTopBtn"),
    majorOnlyCheckbox: document.getElementById("pvMajorOnly"),
    companyModalBackdrop: document.getElementById("pvCompanyModalBackdrop"),
    companyModal: document.getElementById("pvCompanyModal"),
    companyModalTitle: document.getElementById("pvCompanyModalTitle"),
    companyModalMeta: document.getElementById("pvCompanyModalMeta"),
    companyModalBody: document.getElementById("pvCompanyModalBody"),
    companyModalCloseBtn: document.getElementById("pvCompanyModalCloseBtn"),
    companyModalViewAllBtn: document.getElementById("pvCompanyModalViewAllBtn"),
  };

  function uniqueSorted(values) {
    return Array.from(new Set(values.filter(Boolean))).sort(function (a, b) {
      return a.localeCompare(b, "ko");
    });
  }

  // 아이콘 배지에 "(주", "(사" 같은 법인 표기 조각만 남는 것을 방지하기 위해
  // 앞의 법인 표기를 제거한 뒤 앞 두 글자를 사용한다.
  function iconInitials(name) {
    var cleaned = (name || "").replace(/^(\(주\)|㈜|\(사\)|\(유\)|\(재\)|\(합\))\s*/, "");
    cleaned = cleaned.trim() || (name || "");
    return cleaned.slice(0, 2);
  }

  function pad2(n) { return String(n).length < 2 ? "0" + n : String(n); }

  // 브라우저마다 Intl 로케일 포맷(오전/오후, 구두점 위치 등)이 미묘하게 달라질 수 있어
  // "YYYY.MM.DD HH:mm" / "MM.DD HH:mm" 형식을 직접 조립해 모든 브라우저에서 동일하게 표시한다.
  function fmtDateTime(iso, short) {
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso || "";
      var mm = pad2(d.getMonth() + 1);
      var dd = pad2(d.getDate());
      var hh = pad2(d.getHours());
      var mi = pad2(d.getMinutes());
      if (short) return mm + "/" + dd + " " + hh + ":" + mi;
      return d.getFullYear() + "." + mm + "." + dd + " " + hh + ":" + mi;
    } catch (e) {
      return iso || "";
    }
  }

  // 2026-11-22 디자인 통일: 화면 아이콘은 index.html 맨 위 <svg> 묶음(symbol)을 <use>로 쓴다.
  function svgIcon(id, size, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    if (cls) svg.setAttribute("class", cls);
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#" + id);
    svg.appendChild(use);
    return svg;
  }

  // "2026-11-22 01:43" -> "11/22 01:43" (형식이 다르면 그대로 둔다)
  function shortDisplay(s) {
    var m = /^\d{4}-(\d{2})-(\d{2})\s+(.*)$/.exec(s || "");
    return m ? m[1] + "/" + m[2] + " " + m[3] : (s || "");
  }

  function pressAndDate(a) {
    return (a.press || "") + (a.pubDate_display ? " / " + shortDisplay(a.pubDate_display) : "");
  }

  // 빈 결과/오류 칸: 굵은 한 줄 + 보조 한 줄 + 버튼(첫 버튼이 녹색 실행색)
  function fillStateBox(box, title, sub, buttons) {
    box.innerHTML = "";
    var t = document.createElement("p");
    t.className = "nw-state-title";
    t.textContent = title;
    box.appendChild(t);
    var sp = document.createElement("p");
    sp.className = "nw-state-sub";
    sp.textContent = sub;
    box.appendChild(sp);
    var actions = document.createElement("div");
    actions.className = "pv-state-actions";
    buttons.forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = b[0];
      btn.addEventListener("click", b[1]);
      actions.appendChild(btn);
    });
    box.appendChild(actions);
  }

  // ---------- (2026-11-24) 사건 분류: 사업 소식 / 일반 / 곁가지/주가, 같은 소식 묶음 ----------
  // fetch_news.py 가 회사별 연결(company_links)마다 ev = {tier, type, date, quote, g, n, lead} 를 붙여 준다.
  // ev 가 없는 예전 데이터는 모두 "일반"으로 보고 예전 화면과 똑같이 동작한다.
  function evOf(l) { return (l && l.ev) || { tier: "gen" }; }
  function isLeadEv(ev) { return !ev.g || !!ev.lead; }
  function groupKey(code, ev) { return code + "|" + ev.g; }

  function buildGroupIndex() {
    var idx = {};
    state.articles.forEach(function (a) {
      (a.company_links || []).forEach(function (l) {
        var ev = l.ev;
        if (!ev || !ev.g) return;
        var k = groupKey(l.code || l.name, ev);
        (idx[k] = idx[k] || []).push(a);
      });
    });
    state.groupIndex = idx;
  }

  // 같은 소식(대표 기사 제외) 목록
  function sameNewsOthers(a, l) {
    var ev = evOf(l);
    if (!ev.g || (ev.n || 1) < 2) return [];
    return (state.groupIndex[groupKey(l.code || l.name, ev)] || []).filter(function (x) { return x !== a; });
  }

  // 근거 문구가 제목을 되풀이하면 숨긴다(제목에 없는 단서가 있을 때만 보임)
  function normTxt(s) { return (s || "").replace(/[\s'"‘’“”.,…]/g, "").toLowerCase(); }
  function quoteAddsInfo(ev, a) {
    if (!ev.quote) return false;
    return normTxt(a.title).indexOf(normTxt(ev.quote)) === -1;
  }

  function daysAgo(iso) {
    var t = new Date(iso.length === 10 ? iso + "T12:00:00+09:00" : iso).getTime();
    if (isNaN(t)) return null;
    return Math.max(0, Math.floor((Date.now() - t) / 86400000));
  }

  // "11/20 / 4일 전" (사건 날짜) 또는 "보도 11/20 / 4일 전" (사건 날짜를 모를 때)
  function eventDateLabel(a, ev) {
    var iso = ev.date || a.pubDate_iso || "";
    var m = /^\d{4}-(\d{2})-(\d{2})/.exec(iso);
    if (!m) return { text: "", old: false };
    var dd = daysAgo(iso);
    var ago = dd === null ? "" : (dd === 0 ? "오늘" : dd + "일 전");
    return { text: (ev.date ? "" : "보도 ") + m[1] + "/" + m[2] + (ago ? " / " + ago : ""), old: dd !== null && dd > 60 };
  }

  function evPill(ev) {
    var p = document.createElement("span");
    if (ev.tier === "biz") {
      p.className = "nw-ev-pill";
      p.textContent = ev.type || "사업 소식";
    } else {
      p.className = "nw-ev-pill nw-ev-pill--quiet";
      p.textContent = ev.tier === "fold" ? (ev.type === "실적/주가" ? "주가/실적" : "곁가지 언급") : (ev.type || "일반");
    }
    return p;
  }

  // (2026-11-24 형 결정) DART 전자공시 출처 스티커: 기사 자체가 공시이거나, 같은 소식 묶음에 공시가 있으면 붙인다
  function hasDart(a, l) {
    if (a.source === "DART") return true;
    return !!l && sameNewsOthers(a, l).some(function (x) { return x.source === "DART"; });
  }
  function dartPill(small) {
    var p = document.createElement("span");
    p.className = "nw-src-dart" + (small ? " nw-src-dart--sm" : "");
    p.textContent = "DART";
    p.title = "전자공시(DART) 원문";
    return p;
  }

  // 같은 소식 펼치기 버튼 + 목록 (카드/서랍 공용)
  function sameNewsBlock(others) {
    var wrap = document.createElement("div");
    wrap.className = "nw-same";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "nw-same__btn";
    btn.setAttribute("aria-expanded", "false");
    var label = "같은 소식 +" + others.length;
    btn.textContent = label;
    var list = document.createElement("div");
    list.className = "nw-same__list";
    list.hidden = true;
    others.forEach(function (x) {
      var href = x.originallink || x.link || "";
      var it = document.createElement(href ? "a" : "span");
      it.className = "nw-same__item";
      if (href) { it.href = href; it.target = "_blank"; it.rel = "noopener noreferrer"; }
      var t = document.createElement("span");
      t.className = "nw-same__title";
      t.textContent = x.title || "";
      var m = document.createElement("span");
      m.className = "nw-same__meta";
      if (x.source === "DART") m.appendChild(dartPill(true));
      m.appendChild(document.createTextNode(pressAndDate(x)));
      it.appendChild(t);
      it.appendChild(m);
      list.appendChild(it);
    });
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      list.hidden = !list.hidden;
      btn.setAttribute("aria-expanded", list.hidden ? "false" : "true");
      btn.textContent = list.hidden ? label : "같은 소식 접기";
    });
    wrap.appendChild(btn);
    wrap.appendChild(list);
    return wrap;
  }

  // 피드에서 이 기사를 보일지: 곁가지/주가만 걸린 기사는 체크 전까지 빼고, 같은 소식은 대표만
  function feedLinks(a) {
    return hasLinkLevelFilters() ? getMatchingLinks(a) : (a.company_links || []);
  }
  function feedVisible(a) {
    if (!state.hasEv) return true;
    var ls = feedLinks(a);
    if (!ls.length) return true;
    if (!ls.some(function (l) { return isLeadEv(evOf(l)); })) return false;
    if (!state.showFold && ls.every(function (l) { return evOf(l).tier === "fold"; })) return false;
    return true;
  }
  function isFoldOnly(a) {
    var ls = feedLinks(a);
    return ls.length > 0 && ls.every(function (l) { return evOf(l).tier === "fold"; }) &&
      ls.some(function (l) { return isLeadEv(evOf(l)); });
  }

  // 회사별 사건 수(같은 소식은 1건으로)
  function buildEvCounts() {
    var all = {};
    state.articles.forEach(function (a) {
      (a.company_links || []).forEach(function (l) {
        var code = l.code || l.name;
        var c = all[code] = all[code] || { biz: 0, gen: 0, fold: 0 };
        var ev = evOf(l);
        if (isLeadEv(ev) && c[ev.tier] !== undefined) c[ev.tier] += 1;
      });
    });
    state.evCounts = all;
  }
  function companyEvCounts(code) {
    return (state.evCounts || {})[code] || { biz: 0, gen: 0, fold: 0 };
  }

  // 필터 줄의 "곁가지/주가 기사도 보기" 와 담당 고객 머리의 "사업 소식 있는 고객만"
  // (index.html 은 그대로 두고 여기서 만든다 - 허브 조립 스크립트와 겹치지 않게)
  var foldToggle = null;
  var bizOnlyBtn = null;
  function ensureEvControls() {
    if (!state.hasEv) return;
    if (!foldToggle && el.majorOnlyCheckbox) {
      foldToggle = document.createElement("label");
      foldToggle.className = "pv-major-toggle nw-fold-toggle";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = "nwShowFold";
      cb.addEventListener("change", function (e) {
        state.showFold = e.target.checked;
        state.visibleCount = PAGE_SIZE;
        applyFiltersAndRender();
      });
      foldToggle.appendChild(cb);
      foldToggle.appendChild(document.createTextNode(" "));
      foldToggle.appendChild(document.createElement("span"));
      var majorLabel = el.majorOnlyCheckbox.closest("label");
      majorLabel.parentNode.insertBefore(foldToggle, majorLabel.nextSibling);
    }
    if (!bizOnlyBtn && el.sideCount) {
      bizOnlyBtn = document.createElement("button");
      bizOnlyBtn.type = "button";
      bizOnlyBtn.className = "nw-bizonly";
      bizOnlyBtn.setAttribute("aria-pressed", "false");
      bizOnlyBtn.textContent = "사업 소식 있는 고객만";
      bizOnlyBtn.addEventListener("click", function () {
        state.bizOnly = !state.bizOnly;
        bizOnlyBtn.setAttribute("aria-pressed", state.bizOnly ? "true" : "false");
        applyFiltersAndRender();
      });
      var head = el.sideCount.closest(".pv-side__head");
      head.parentNode.insertBefore(bizOnlyBtn, head.nextSibling);
    }
  }
  function updateFoldToggle(foldCount) {
    if (!foldToggle) return;
    foldToggle.querySelector("span").textContent = "곁가지/주가 기사도 보기 (" + foldCount.toLocaleString("ko-KR") + ")";
  }

  // ---------- 업데이트 상태 ----------
  function renderStatus() {
    if (state.loadError) {
      el.statusDot.className = "pv-status__dot is-error";
      el.statusText.textContent = "뉴스 데이터를 불러오지 못했습니다";
      el.counts.textContent = "마지막 확인: " + fmtDateTime(new Date().toISOString());
      return;
    }
    if (!state.generatedAt) {
      el.statusDot.className = "pv-status__dot";
      el.statusText.textContent = "업데이트 정보 확인 중";
      el.counts.textContent = "";
      return;
    }
    var genDate = new Date(state.generatedAt);
    var hoursSince = (Date.now() - genDate.getTime()) / 3600000;
    var label = "정상 업데이트";
    var cls = "is-ok";
    if (hoursSince > STALE_DANGER_HOURS) {
      label = "업데이트 확인 필요";
      cls = "is-error";
    } else if (hoursSince > STALE_WARN_HOURS) {
      label = "업데이트 지연";
      cls = "is-delayed";
    }
    el.statusDot.className = "pv-status__dot " + cls;
    el.statusText.textContent = label + " ";
    var when = document.createElement("b");
    when.textContent = fmtDateTime(state.generatedAt, true);
    el.statusText.appendChild(when);
  }

  function renderCounts(filteredCount) {
    var parts = [];
    if (state.lookbackDays) parts.push("최근 " + state.lookbackDays + "일" + (state.hasEv ? " (사업 소식 " + state.bizDays + "일)" : ""));
    parts.push("전체 " + state.totalArticles.toLocaleString("ko-KR") + "건");
    var line = parts.join(" / ");
    if (state.hasEv) {
      // 같은 소식은 1건으로, 곁가지/주가는 접어서 세므로 '보이는 소식' 수를 따로 적는다
      el.counts.textContent = line + " / " + (hasActiveFilters() ? "검색 결과 " : "보이는 소식 ") + filteredCount.toLocaleString("ko-KR") + "건";
      return;
    }
    if (filteredCount !== state.totalArticles) {
      line += " / 검색 결과 " + filteredCount.toLocaleString("ko-KR") + "건";
    }
    el.counts.textContent = line;
  }

  // ---------- 필터 옵션 (종속 필터) ----------
  // 2026-08-25 수정: 예전에는 기사 하나에 붙어있는 team/cell/reps를 그냥 "배열"로 보고
  // 필터링했는데, 여러 회사가 한 기사에 같이 걸리는 경우(예: 배터리 장비업체 6곳을 함께
  // 언급하는 산업 동향 기사) 그 기사의 team/cell/reps 배열에는 관련된 모든 회사의 정보가
  // 전부 섞여 들어간다. 그 상태로 "팀 -> 셀 -> 영업명" 종속 필터를 걸면, 예를 들어 TSC4를
  // 선택해도 그 기사에 같이 걸린 TSC3 소속 담당자까지 영업명 목록에 나오는 문제가 있었다.
  // 이제는 회사별 연결 정보(company_links)를 기준으로, "이 회사가 선택된 팀/셀/영업명과
  // 실제로 일치하는지"를 회사 단위로 정확히 따진다.
  function linkMatchesActiveFilters(l) {
    if (state.selectedTeam !== "전체" && (l.team || []).indexOf(state.selectedTeam) === -1) return false;
    if (state.selectedCell !== "전체" && (l.cell || []).indexOf(state.selectedCell) === -1) return false;
    if (state.selectedRep !== "전체" && (l.reps || []).indexOf(state.selectedRep) === -1) return false;
    if (state.searchText) {
      var t = state.searchText.toLowerCase();
      var hit = (l.name || "").toLowerCase().indexOf(t) !== -1;
      if (!hit) hit = (l.matched_sub_names || []).some(function (s) { return (s || "").toLowerCase().indexOf(t) !== -1; });
      if (!hit) hit = (l.reps || []).some(function (r) { return (r || "").toLowerCase().indexOf(t) !== -1; });
      if (!hit) return false;
    }
    return true;
  }

  // 팀/셀/영업명 중 하나라도 선택돼 있거나 검색어가 있으면 "회사 단위" 정밀 필터가 필요하다.
  function hasLinkLevelFilters() {
    return state.selectedTeam !== "전체" || state.selectedCell !== "전체" || state.selectedRep !== "전체" || !!state.searchText;
  }

  // 현재 필터 조건에 실제로 부합하는 회사 연결(company_links)만 골라낸다.
  function getMatchingLinks(a) {
    return (a.company_links || []).filter(linkMatchesActiveFilters);
  }

  function articleMatchesTeamCell(a, team, cell) {
    return (a.company_links || []).some(function (l) {
      if (team !== "전체" && (l.team || []).indexOf(team) === -1) return false;
      if (cell !== "전체" && (l.cell || []).indexOf(cell) === -1) return false;
      return true;
    });
  }

  // 2026-11-25 툴 개수 의뢰 #14: 팀/셀/영업명 목록은 기사뿐 아니라 news.json 의 전체 고객 명단(roster)에서도 만든다
  // (담당 고객 기사가 0건인 영업도 목록에 나오게). roster 가 없는 예전 데이터는 기사 기준 그대로.
  function rosterCountFor() {
    return (state.roster || []).filter(function (l) {
      if (state.selectedTeam !== "전체" && (l.team || []).indexOf(state.selectedTeam) === -1) return false;
      if (state.selectedCell !== "전체" && (l.cell || []).indexOf(state.selectedCell) === -1) return false;
      if (state.selectedRep !== "전체" && (l.reps || []).indexOf(state.selectedRep) === -1) return false;
      if (state.searchText) {
        var t = state.searchText.toLowerCase();
        var hit = (l.name || "").toLowerCase().indexOf(t) !== -1 ||
          (l.reps || []).some(function (r) { return (r || "").toLowerCase().indexOf(t) !== -1; });
        if (!hit) return false;
      }
      return true;
    }).length;
  }

  function buildFilterOptions() {
    var allLinks = state.articles.reduce(function (acc, a) { return acc.concat(a.company_links || []); }, []).concat(state.roster || []);
    var teams = uniqueSorted(allLinks.reduce(function (acc, l) { return acc.concat(l.team || []); }, []));

    var cellLinks = allLinks.filter(function (l) {
      return state.selectedTeam === "전체" || (l.team || []).indexOf(state.selectedTeam) !== -1;
    });
    var cells = uniqueSorted(cellLinks.reduce(function (acc, l) { return acc.concat(l.cell || []); }, []));

    var repLinks = cellLinks.filter(function (l) {
      return state.selectedCell === "전체" || (l.cell || []).indexOf(state.selectedCell) !== -1;
    });
    var reps = uniqueSorted(repLinks.reduce(function (acc, l) { return acc.concat(l.reps || []); }, []));

    if (state.selectedCell !== "전체" && cells.indexOf(state.selectedCell) === -1) state.selectedCell = "전체";
    if (state.selectedRep !== "전체" && reps.indexOf(state.selectedRep) === -1) state.selectedRep = "전체";

    fillSelect(el.teamSelect, "팀 전체", teams, state.selectedTeam);
    fillSelect(el.cellSelect, "셀 전체", cells, state.selectedCell);
    fillSelect(el.repSelect, "영업명 전체", reps, state.selectedRep);
  }

  function fillSelect(selectEl, allLabel, options, selected) {
    selectEl.innerHTML = "";
    var allOpt = document.createElement("option");
    allOpt.value = "전체";
    allOpt.textContent = allLabel;
    selectEl.appendChild(allOpt);
    options.forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt;
      o.textContent = opt;
      if (opt === selected) o.selected = true;
      selectEl.appendChild(o);
    });
    if (selected === "전체") allOpt.selected = true;
    selectEl.classList.toggle("is-active", selected !== "전체");
  }

  // ---------- 검색 매칭 ----------
  // 2026-08-25 수정: 팀/셀/영업명/검색어 조건을 예전에는 기사 전체 배열(team/cell/reps) 기준으로
  // 따로따로 검사했는데, 여러 회사가 함께 걸린 기사에서는 이 방식이 "이 기사에 걸린 회사 중
  // 아무나 한 명이라도 조건과 맞으면 통과"가 되어버려서 다른 회사 소속 담당자를 검색해도
  // 관련 없는 회사가 같이 나오는 문제가 있었다. 이제는 getMatchingLinks(a)로 회사 단위에서
  // 팀/셀/영업명/검색어를 모두 만족하는 회사가 실제로 있는지를 확인한다.
  function getFiltered() {
    var linkFilterActive = hasLinkLevelFilters();
    return state.articles.filter(function (a) {
      if (state.majorOnly && !a.major) return false;
      if (linkFilterActive && getMatchingLinks(a).length === 0) return false;
      return feedVisible(a);
    });
  }

  // 곁가지/주가만 걸려 피드에서 빠진 기사 수 (체크 상자 옆 숫자)
  function countFoldHidden() {
    var linkFilterActive = hasLinkLevelFilters();
    return state.articles.filter(function (a) {
      if (state.majorOnly && !a.major) return false;
      if (linkFilterActive && getMatchingLinks(a).length === 0) return false;
      return isFoldOnly(a);
    }).length;
  }

  function hasActiveFilters() {
    return state.selectedTeam !== "전체" || state.selectedCell !== "전체" || state.selectedRep !== "전체" ||
      state.majorOnly || !!state.searchText;
  }

  // ---------- 피드 제목 ----------
  function updateFeedTitle() {
    if (state.selectedRep !== "전체") {
      el.feedTitle.textContent = state.selectedRep + "님의 고객 실시간 이슈";
    } else if (state.searchText) {
      el.feedTitle.textContent = state.searchText + "의 실시간 이슈";
    } else {
      el.feedTitle.textContent = "고객 실시간 이슈";
    }
  }

  // ---------- 카드 렌더 ----------
  function isFreshArticle(a) {
    if (!a.pubDate_iso) return false;
    var hours = (Date.now() - new Date(a.pubDate_iso).getTime()) / 3600000;
    return hours >= 0 && hours <= FRESH_HOURS;
  }

  // 기사 카드에 대표로 보여줄 고객 연결: 필터/검색이 걸려 있으면 그 조건에 맞는 회사, 아니면 첫 회사.
  function primaryLink(a) {
    var links = a.company_links || [];
    if (hasLinkLevelFilters()) {
      var m = getMatchingLinks(a);
      if (m.length) return m[0];
    }
    return links[0] || null;
  }

  // 이름이 같은 다른 고객(code 다름)이 있으면 "이름 (팀/담당자)"로 구분한다(renderSidePanel과 같은 표기).
  function linkLabel(l) {
    if (!l) return "";
    if ((state.nameCodeCount[l.name] || 0) > 1) {
      var meta = [].concat(l.team || [], (l.reps || []).slice(0, 1)).filter(Boolean).join("/");
      if (meta) return l.name + " (" + meta + ")";
    }
    return l.name;
  }

  // 2026-11-22 카드 구조 변경: 예전에는 카드 전체가 <a>여서 안에 버튼을 넣을 수 없었다.
  // 이제 카드는 <article>, 원문 링크는 제목 <a>(::after로 카드 전체를 누름 영역으로 덮음),
  // "AI 요약" 버튼은 링크 밖의 별도 버튼(z-index로 덮개 위)이라 서로 중첩되지 않는다.
  function buildCard(a) {
    var href = a.originallink || a.link || "";
    var card = document.createElement("article");
    card.className = "pv-card";

    var body = document.createElement("div");
    body.className = "pv-card__body";

    var lead = primaryLink(a);
    var companyNames = a.companies || [];
    var leadEv = evOf(lead);

    var badges = document.createElement("div");
    badges.className = "pv-card__badges";
    if (state.hasEv && (leadEv.tier === "biz" || leadEv.tier === "fold")) {
      badges.appendChild(evPill(leadEv));
    }
    if (state.hasEv && hasDart(a, lead)) badges.appendChild(dartPill());
    if (isFreshArticle(a)) {
      var freshBadge = document.createElement("span");
      freshBadge.className = "pv-badge-pill pv-badge-pill--new";
      freshBadge.textContent = "오늘 새 기사";
      badges.appendChild(freshBadge);
    }
    if (a.major) {
      var majorBadge = document.createElement("span");
      majorBadge.className = "pv-badge-pill pv-badge-pill--major";
      majorBadge.textContent = "메이저 언론사";
      badges.appendChild(majorBadge);
    }
    var companySpan = document.createElement("span");
    companySpan.className = "pv-card__company";
    // 같은 브랜드의 여러 사업장에 동시에 관련된 기사는 대표 회사명만 보여주고
    // 나머지는 "외 N곳"으로 축약한다(배지가 너무 길어지는 것 방지).
    companySpan.textContent = lead ? lead.name : (companyNames[0] || "");
    if (companyNames.length > 1) {
      var more = document.createElement("small");
      more.textContent = "외 " + (companyNames.length - 1) + "곳";
      companySpan.appendChild(more);
      companySpan.title = companyNames.join(", ");
    }
    badges.appendChild(companySpan);

    var leadCode = lead ? (lead.code || lead.name) : "";
    if (leadCode) {
      var aiBtn = document.createElement("button");
      aiBtn.type = "button";
      aiBtn.className = "nw-ai-btn";
      aiBtn.setAttribute("aria-label", lead.name + " AI 요약 열기");
      aiBtn.appendChild(svgIcon("i-spark", 12));
      aiBtn.appendChild(document.createTextNode("AI 요약"));
      aiBtn.addEventListener("click", function () {
        openCompanyModal(leadCode, lead.name, linkLabel(lead));
      });
      badges.appendChild(aiBtn);
    }
    body.appendChild(badges);

    var title = document.createElement("p");
    title.className = "pv-card__title";
    if (href) {
      var titleLink = document.createElement("a");
      titleLink.href = href;
      titleLink.target = "_blank";
      titleLink.rel = "noopener noreferrer";
      titleLink.textContent = a.title || "";
      title.appendChild(titleLink);
    } else {
      title.textContent = a.title || "";
    }
    body.appendChild(title);

    if (a.description) {
      var desc = document.createElement("p");
      desc.className = "pv-card__desc";
      desc.textContent = a.description;
      body.appendChild(desc);
    }

    if ((a.matched_sub_names || []).length) {
      var relWrap = document.createElement("div");
      relWrap.className = "pv-related-badges";
      a.matched_sub_names.forEach(function (sub) {
        var b = document.createElement("span");
        b.className = "pv-badge-pill pv-badge-pill--rel";
        b.textContent = "관련: " + sub;
        relWrap.appendChild(b);
      });
      body.appendChild(relWrap);
    }

    if (state.hasEv && lead) {
      var others = sameNewsOthers(a, lead);
      if (others.length) body.appendChild(sameNewsBlock(others));
    }

    var meta = document.createElement("div");
    meta.className = "pv-card__meta";
    var left = document.createElement("span");
    left.textContent = pressAndDate(a);
    var right = document.createElement("span");
    // 대표 회사의 팀/셀/담당만 보여준다(여러 회사가 걸린 기사에서 다른 회사 담당자가 섞이지 않게).
    var src = lead || a;
    right.textContent = [].concat(src.team || [], src.cell || [], src.reps || []).filter(Boolean).join(" / ");
    meta.appendChild(left);
    meta.appendChild(right);
    body.appendChild(meta);

    card.appendChild(body);

    if (href) card.appendChild(svgIcon("i-out", 16, "pv-card__arrow"));

    return card;
  }

  function animateIn(cardEls, staggerStartIndex) {
    if (prefersReducedMotion) {
      cardEls.forEach(function (c) { c.classList.add("is-visible"); });
      return;
    }
    cardEls.forEach(function (c, i) {
      var delay = ((staggerStartIndex || 0) + i) * 80;
      setTimeout(function () {
        requestAnimationFrame(function () { c.classList.add("is-visible"); });
      }, delay);
    });
  }

  function render(opts) {
    opts = opts || {};
    var filtered = getFiltered();
    renderCounts(filtered.length);
    if (state.hasEv) updateFoldToggle(countFoldHidden());
    updateFeedTitle();
    // 담당 고객 목록은 곁가지/주가만 있는 고객도 남기도록 피드 접기 전 목록으로 만든다
    renderSidePanel(state.hasEv ? state.articles.filter(function (a) {
      if (state.majorOnly && !a.major) return false;
      return !hasLinkLevelFilters() || getMatchingLinks(a).length > 0;
    }) : filtered);
    updateResetState();

    var visible = filtered.slice(0, state.visibleCount);
    el.errorState.hidden = true;

    if (filtered.length === 0) {
      el.articleList.innerHTML = "";
      el.emptyState.hidden = false;
      var rosterN = (state.roster && !state.majorOnly &&
        (state.selectedTeam !== "전체" || state.selectedCell !== "전체" || state.selectedRep !== "전체" || state.searchText)) ? rosterCountFor() : 0;
      if (rosterN > 0) {
        var foldN = state.hasEv && !state.showFold ? countFoldHidden() : 0;
        var btns = [];
        if (foldN > 0) {
          btns.push(["곁가지/주가 기사 " + foldN + "건 보기", function () {
            state.showFold = true;
            var cb = document.getElementById("nwShowFold");
            if (cb) cb.checked = true;
            applyFiltersAndRender();
          }]);
        }
        btns.push(["필터 전체 해제", resetAllFilters]);
        fillStateBox(el.emptyState, "최근 기사 없음 (담당 고객 " + rosterN.toLocaleString("ko-KR") + "곳)",
          "담당 고객의 새 기사가 들어오면 여기에 보입니다. 기사는 매일 밤사이 새로 모읍니다.", btns);
        el.loadMoreBtn.hidden = true;
        state.prevIds = [];
        return;
      }
      fillStateBox(el.emptyState, "조건에 맞는 고객 기사가 없습니다", "검색어 또는 필터를 다시 확인해 주세요.", [
        ["필터 전체 해제", resetAllFilters],
        ["검색어 지우기", function () {
          state.searchText = "";
          el.searchInput.value = "";
          applyFiltersAndRender();
        }],
      ]);
      el.loadMoreBtn.hidden = true;
      state.prevIds = [];
      return;
    }
    el.emptyState.hidden = true;

    var currentIds = visible.map(function (a) { return a.link || a.title; });
    var isFreshRender = opts.freshRender;
    var prevIdSet = {};
    state.prevIds.forEach(function (id) { prevIdSet[id] = true; });

    el.articleList.innerHTML = "";
    var newCardEls = [];
    var frag = document.createDocumentFragment();
    visible.forEach(function (a, idx) {
      var id = a.link || a.title;
      var card = buildCard(a);
      frag.appendChild(card);
      var isNew = isFreshRender || !prevIdSet[id];
      if (isNew) newCardEls.push(card);
      else card.classList.add("is-visible");
    });
    el.articleList.appendChild(frag);

    var capped = isFreshRender ? newCardEls.slice(0, 8) : newCardEls;
    capped.forEach(function (c) { if (newCardEls.indexOf(c) === -1) return; });
    animateIn(newCardEls.slice(0, isFreshRender ? 8 : newCardEls.length));
    newCardEls.slice(isFreshRender ? 8 : newCardEls.length).forEach(function (c) { c.classList.add("is-visible"); });

    state.prevIds = currentIds;
    el.loadMoreBtn.hidden = filtered.length <= visible.length;
    el.loadMoreBtn.textContent = "기사 더 보기 (" + Math.min(PAGE_SIZE, filtered.length - visible.length).toLocaleString("ko-KR") + "건)";
    el.loadMoreBtn.disabled = false;
    if (typeof syncHeaderHeight === "function") setTimeout(syncHeaderHeight, 0);
  }

  function updateResetState() {
    el.resetBtn.disabled = !hasActiveFilters();
  }

  // ---------- 담당 고객 (사이드 패널) ----------
  // 현재 필터(팀/셀/영업명/이슈유형/메이저/검색)에 해당하는 고객사를 전부 보여준다(개수 제한 없음).
  // 2026-08-25 수정: 필터가 걸려 있을 때는 기사에 걸린 회사 전체가 아니라, 그 필터 조건에
  // "실제로 부합하는" 회사만 골라서 보여준다(예: "김훈" 검색 시 그 기사에 같이 언급된
  // 다른 담당자의 회사까지 나오지 않도록).
  //
  // 2026-09-07 수정: 목록을 회사 "이름"이 아니라 company_links의 "code"(고유 고객번호)
  // 기준으로 구분한다. companies.json에는 이름은 완전히 같은데 실제로는 서로 다른 회사가
  // 5쌍 있어(예: 제닉스(주) - TSC2·ME03·김혁 / TSC3·ME06·이인규), 이름만으로 목록을 만들면
  // 이 둘이 한 줄로 합쳐져 보여서 팀/셀 필터와 무관한 담당자가 같이 노출되는 문제가 있었다.
  // 같은 이름을 쓰는 서로 다른 코드가 있을 때만 "이름 (팀·담당자)" 형태로 구분 표시한다.
  function renderSidePanel(filtered) {
    el.companyList.innerHTML = "";
    var linkFilterActive = hasLinkLevelFilters();
    var linksByCode = {};
    var countByCode = {};
    filtered.forEach(function (a) {
      var links = linkFilterActive ? getMatchingLinks(a) : (a.company_links || []);
      links.forEach(function (l) {
        // 구버전 news.json(코드 없음) 대비: code가 없으면 이름을 임시 키로 쓴다.
        var code = l.code || l.name;
        if (code && !linksByCode[code]) linksByCode[code] = l;
        if (code) countByCode[code] = (countByCode[code] || 0) + 1;
      });
    });

    var codes = Object.keys(linksByCode);
    var nameCounts = {};
    codes.forEach(function (code) {
      var n = linksByCode[code].name;
      nameCounts[n] = (nameCounts[n] || 0) + 1;
    });

    var items = codes.map(function (code) {
      var l = linksByCode[code];
      var label = l.name;
      if (nameCounts[l.name] > 1) {
        var meta = [].concat(l.team || [], (l.reps || []).slice(0, 1)).filter(Boolean).join("/");
        if (meta) label = l.name + " (" + meta + ")";
      }
      var item = { code: code, name: l.name, label: label, count: countByCode[code] || 0 };
      if (state.hasEv) {
        // 2026-11-24: 숫자는 기사 수 대신 사건 수(같은 소식은 1건). 사업 소식이 있으면 사업 소식 수를 민트로
        var ec = companyEvCounts(code);
        item.biz = ec.biz;
        item.count = ec.biz || (ec.gen + ec.fold);
      }
      return item;
    }).filter(function (it) {
      return !state.bizOnly || it.biz > 0;
    }).sort(function (a, b) { return a.label.localeCompare(b.label, "ko"); });

    el.sideCount.textContent = items.length ? " " + items.length.toLocaleString("ko-KR") : "";

    if (!items.length) {
      var empty = document.createElement("p");
      empty.className = "pv-company-modal__empty";
      empty.textContent = "조건에 맞는 고객사가 없습니다.";
      el.companyList.appendChild(empty);
      return;
    }

    items.forEach(function (item) {
      el.companyList.appendChild(buildCompanyItem(item.code, item.name, item.label, iconInitials(item.name), item.count, item.biz));
    });
  }

  function buildCompanyItem(code, name, label, iconText, count, biz) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pv-company-item" + (code === state.currentCode ? " is-current" : "");
    btn.dataset.code = code;
    var icon = document.createElement("span");
    icon.className = "pv-company-item__icon";
    icon.textContent = iconText;
    var span = document.createElement("span");
    span.className = "pv-company-item__label";
    span.textContent = label;
    btn.appendChild(icon);
    btn.appendChild(span);
    if (count) {
      var n = document.createElement("span");
      n.className = "nw-item-n" + (biz ? " nw-item-n--biz" : "");
      n.textContent = count.toLocaleString("ko-KR");
      if (biz) n.title = "사업 소식 " + biz + "건";
      btn.appendChild(n);
    }
    btn.addEventListener("click", function () {
      closeSide();
      openCompanyModal(code, name, label);
    });
    return btn;
  }

  // ---------- 고객사 핵심 기사 요약 팝업 ----------
  // 2026-09-07 수정: 이름이 아니라 code(고유 고객번호)로 기사를 찾는다 - 이유는
  // renderSidePanel 주석 참고.
  function getCompanyArticlesByCode(code) {
    return state.articles.filter(function (a) {
      return (a.company_links || []).some(function (l) { return (l.code || l.name) === code; });
    });
  }

  function openCompanyModal(code, name, label) {
    renderCompanyModal(code, name, label, getCompanyArticlesByCode(code));
    el.companyModalBackdrop.hidden = false;
    el.companyModal.hidden = false;
    markCurrentCompany(code);
  }

  function closeCompanyModal() {
    el.companyModalBackdrop.hidden = true;
    el.companyModal.hidden = true;
    markCurrentCompany(null);
  }

  // 서랍에 연 고객을 좌측 목록에서 강조한다.
  function markCurrentCompany(code) {
    state.currentCode = code;
    var items = el.companyList.querySelectorAll(".pv-company-item");
    Array.prototype.forEach.call(items, function (b) {
      b.classList.toggle("is-current", !!code && b.dataset.code === code);
    });
  }

  function articleAgeDays(a) {
    if (!a.pubDate_iso) return Infinity;
    var ms = Date.now() - new Date(a.pubDate_iso).getTime();
    if (isNaN(ms)) return Infinity;
    return ms / 86400000;
  }

  // 2026-09-07 수정: companyName(이름) 대신 code(고유 고객번호)로 이 고객사 자신의
  // company_links 항목과 AI 요약을 찾는다 - 이유는 renderSidePanel 주석 참고. label은
  // 화면에 보여줄 제목(동명이사인 경우 "이름 (팀·담당자)" 형태), name은 검색/프롬프트용
  // 원본 이름이다.
  function renderCompanyModal(code, name, label, articles) {
    el.companyModalTitle.textContent = label;
    el.companyModalTitle.dataset.code = code;
    el.companyModalTitle.dataset.name = name;
    el.companyModalBody.innerHTML = "";

    var first = articles[0];
    // 2026-08-25 수정: 여러 회사가 함께 걸린 기사(first)의 team/cell/reps를 그냥 통째로
    // 쓰면 이 고객사와 무관한 다른 회사의 팀/담당자가 같이 표시될 수 있다. 이 고객사
    // (code) 자신의 company_links 항목만 골라서 메타 정보를 만든다.
    var firstLink = first ? (first.company_links || []).filter(function (l) { return (l.code || l.name) === code; })[0] : null;
    var baseMetaParts = firstLink ? [].concat(firstLink.team || [], firstLink.cell || [], firstLink.reps || []).filter(Boolean) : [];

    if (state.hasEv) {
      renderCompanyModalEv(code, articles, baseMetaParts);
      return;
    }

    // company_summaries가 news.json에 아예 없으면(구버전 데이터 / 이번 회차에 AI 요약을 안 돌린 날)
    // 예전처럼 기사 카드 목록을 그대로 보여준다(항상 동작하는 기본 방식으로 자동 전환).
    if (!state.companySummaries) {
      renderCompanyModalLegacyList(articles, baseMetaParts);
      return;
    }

    var entry = state.companySummaries[code];
    var days = state.summaryLookbackDays || 20;
    // fetch_news.py가 "고객명은 겹쳤지만 실제로는 무관한 기사뿐"이라고 AI가 스스로 판단했을 때
    // 돌려주는 안내 문장. 이 경우엔 AI 요약 박스나 참고 기사 목록 없이, 새 소식 없음과 같은
    // 형태로 깔끔하게 보여준다(무관한 원문 기사를 참고 기사로 나열하면 오히려 혼란스러움).
    var NO_RELEVANT_NEWS_TEXT = "최근 이 고객사와 직접 관련된 뚜렷한 소식은 없습니다.";

    if (!entry || entry.summary === NO_RELEVANT_NEWS_TEXT) {
      // AI 요약 기능은 켜져 있지만, 이 고객사는 최근 N일 이내 새 기사가 없었거나
      // (또는 검색은 걸렸지만 실제로 무관한 기사뿐이라고 AI가 판단한) 경우.
      el.companyModalMeta.textContent = baseMetaParts.concat("최근 " + days + "일 새 소식 없음").join(" / ");
      var empty = document.createElement("p");
      empty.className = "pv-company-modal__empty";
      empty.textContent = "최근 " + days + "일간 이 고객사와 직접 관련된 기사가 없습니다. 아래 \"이 고객사 기사 전체 보기\"에서 이전 기사를 확인하실 수 있습니다.";
      el.companyModalBody.appendChild(empty);
      el.companyModalViewAllBtn.hidden = articles.length === 0;
      return;
    }

    el.companyModalMeta.textContent = baseMetaParts.concat(
      "최근 " + days + "일 기사 " + entry.based_on.toLocaleString("ko-KR") + "건 기준 요약"
    ).join(" / ");

    var summaryBox = document.createElement("div");
    summaryBox.className = "pv-company-modal__summary-box";
    var badge = document.createElement("span");
    badge.className = "pv-company-modal__summary-badge";
    badge.appendChild(svgIcon("i-spark", 13));
    badge.appendChild(document.createTextNode("AI 요약"));
    var summaryText = document.createElement("p");
    summaryText.className = "pv-company-modal__summary-text";
    summaryText.textContent = entry.summary;
    var summaryMeta = document.createElement("p");
    summaryMeta.className = "pv-company-modal__summary-meta";
    summaryMeta.textContent = "생성 " + fmtDateTime(entry.generated_at, true);
    summaryBox.appendChild(badge);
    summaryBox.appendChild(summaryText);
    summaryBox.appendChild(summaryMeta);
    el.companyModalBody.appendChild(summaryBox);

    // 요약의 근거가 된 최근 N일 기사를 원문으로 바로 확인할 수 있도록 제목만 간단히 링크로 나열한다.
    var recentRefs = articles.filter(function (a) { return articleAgeDays(a) <= days; }).slice(0, entry.based_on);
    if (recentRefs.length) {
      var refsTitle = document.createElement("p");
      refsTitle.className = "pv-company-modal__refs-title";
      refsTitle.textContent = "참고 기사";
      el.companyModalBody.appendChild(refsTitle);
      var refsWrap = document.createElement("div");
      refsWrap.className = "pv-company-modal__refs";
      recentRefs.forEach(function (a) {
        refsWrap.appendChild(buildCompanyModalRefItem(a));
      });
      el.companyModalBody.appendChild(refsWrap);
    }

    el.companyModalViewAllBtn.hidden = false;
  }

  // ---------- (2026-11-24) 서랍: 사업 소식(90일) -> AI 요약 -> 일반 소식(20일) -> 접힌 곁가지/주가 ----------
  // 화면 질문: "방문 전에 이 고객에게 팔 만한 새 일이 있나" -> 폰 첫 화면에 첫 사업 소식이 보이도록 맨 위.
  function renderCompanyModalEv(code, articles, baseMetaParts) {
    var rows = { biz: [], gen: [], fold: [] };
    articles.forEach(function (a) {
      var l = (a.company_links || []).filter(function (x) { return (x.code || x.name) === code; })[0];
      if (!l) return;
      var ev = evOf(l);
      if (!isLeadEv(ev) || !rows[ev.tier]) return;
      rows[ev.tier].push({ a: a, l: l, ev: ev, when: ev.date || (a.pubDate_iso || "").slice(0, 10) });
    });
    rows.biz.sort(function (x, y) { return x.when < y.when ? 1 : x.when > y.when ? -1 : 0; });
    ["gen", "fold"].forEach(function (k) {
      rows[k].sort(function (x, y) { return (y.a.pubDate_iso || "").localeCompare(x.a.pubDate_iso || ""); });
    });

    el.companyModalMeta.textContent = baseMetaParts.join(" / ");

    // 건수 줄(누르면 그 칸으로 이동)
    var jump = document.createElement("div");
    jump.className = "nw-jump";
    [["biz", "사업 소식"], ["gen", "일반 소식"], ["fold", "곁가지/주가"]].forEach(function (p, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "nw-jump__btn" + (p[0] === "biz" && rows.biz.length ? " is-biz" : "");
      b.textContent = p[1] + " " + rows[p[0]].length;
      b.disabled = !rows[p[0]].length;
      b.addEventListener("click", function () {
        var t = document.getElementById("nwSec-" + p[0]);
        if (!t) return;
        if (t.tagName === "DETAILS") t.open = true;
        t.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "start" });
      });
      jump.appendChild(b);
    });
    el.companyModalBody.appendChild(jump);

    // 1) 사업 소식
    var bizSec = sectionBox("biz", "사업 소식", "최근 " + state.bizDays + "일 / 수주, 증설, 설비, 신제품, 자동화");
    if (rows.biz.length) {
      rows.biz.forEach(function (r) { bizSec.appendChild(buildEventItem(r)); });
    } else {
      var none = document.createElement("p");
      none.className = "nw-none";
      none.textContent = "최근 " + state.bizDays + "일 동안 확인된 사업 소식이 없습니다.";
      bizSec.appendChild(none);
    }
    el.companyModalBody.appendChild(bizSec);

    // 2) AI 요약 (있을 때만)
    var entry = state.companySummaries ? state.companySummaries[code] : null;
    var NO_RELEVANT_NEWS_TEXT = "최근 이 고객사와 직접 관련된 뚜렷한 소식은 없습니다.";
    if (entry && entry.summary && entry.summary !== NO_RELEVANT_NEWS_TEXT) {
      var summaryBox = document.createElement("div");
      summaryBox.className = "pv-company-modal__summary-box";
      var badge = document.createElement("span");
      badge.className = "pv-company-modal__summary-badge";
      badge.appendChild(svgIcon("i-spark", 13));
      badge.appendChild(document.createTextNode("AI 요약"));
      var summaryText = document.createElement("p");
      summaryText.className = "pv-company-modal__summary-text";
      summaryText.textContent = entry.summary;
      var summaryMeta = document.createElement("p");
      summaryMeta.className = "pv-company-modal__summary-meta";
      summaryMeta.textContent = "기사 " + (entry.based_on || 0) + "건 기준 / 생성 " + fmtDateTime(entry.generated_at, true);
      summaryBox.appendChild(badge);
      summaryBox.appendChild(summaryText);
      summaryBox.appendChild(summaryMeta);
      el.companyModalBody.appendChild(summaryBox);
    }

    // 3) 일반 소식
    if (rows.gen.length) {
      var genSec = sectionBox("gen", "일반 소식", "최근 " + (state.lookbackDays || 20) + "일 / 인사, 행사, 협력 등");
      var refs = document.createElement("div");
      refs.className = "pv-company-modal__refs";
      rows.gen.forEach(function (r) { refs.appendChild(buildCompanyModalRefItem(r.a)); });
      genSec.appendChild(refs);
      el.companyModalBody.appendChild(genSec);
    }

    // 4) 곁가지/주가 (접어서)
    if (rows.fold.length) {
      var det = document.createElement("details");
      det.className = "nw-fold";
      det.id = "nwSec-fold";
      var sum = document.createElement("summary");
      var st = document.createElement("span");
      st.className = "nw-fold__t";
      st.textContent = "곁가지/주가 기사 " + rows.fold.length + "건";
      var ss = document.createElement("span");
      ss.className = "nw-fold__s";
      ss.textContent = "다른 회사 기사에 잠깐 언급되거나 주가/실적 소식";
      sum.appendChild(st);
      sum.appendChild(ss);
      det.appendChild(sum);
      var frefs = document.createElement("div");
      frefs.className = "pv-company-modal__refs";
      rows.fold.forEach(function (r) {
        var it = buildCompanyModalRefItem(r.a);
        var meta = it.querySelector(".pv-company-modal__ref-item-meta");
        if (meta) meta.textContent = (r.ev.type === "실적/주가" ? "주가/실적" : "곁가지 언급") + " / " + meta.textContent;
        frefs.appendChild(it);
      });
      det.appendChild(frefs);
      el.companyModalBody.appendChild(det);
    }

    if (!articles.length) {
      var empty = document.createElement("p");
      empty.className = "pv-company-modal__empty";
      empty.textContent = "최근 수집된 기사가 없습니다.";
      el.companyModalBody.appendChild(empty);
    }
    el.companyModalViewAllBtn.hidden = articles.length === 0;
  }

  function sectionBox(key, title, sub) {
    var sec = document.createElement("section");
    sec.className = "nw-sec";
    sec.id = "nwSec-" + key;
    var h = document.createElement("div");
    h.className = "nw-sec__h";
    var t = document.createElement("h4");
    t.textContent = title;
    var s = document.createElement("span");
    s.textContent = sub;
    h.appendChild(t);
    h.appendChild(s);
    sec.appendChild(h);
    return sec;
  }

  function buildEventItem(r) {
    var a = r.a, ev = r.ev;
    var item = document.createElement("div");
    item.className = "nw-evt";
    var top = document.createElement("div");
    top.className = "nw-evt__top";
    top.appendChild(evPill(ev));
    if (hasDart(a, r.l)) top.appendChild(dartPill());
    var dl = eventDateLabel(a, ev);
    var d = document.createElement("span");
    d.className = "nw-evt__date" + (dl.old ? " is-old" : "");
    d.textContent = dl.text;
    top.appendChild(d);
    item.appendChild(top);

    var href = a.originallink || a.link || "";
    var title = document.createElement(href ? "a" : "p");
    title.className = "nw-evt__title";
    title.textContent = a.title || "";
    if (href) { title.href = href; title.target = "_blank"; title.rel = "noopener noreferrer"; }
    item.appendChild(title);

    if (quoteAddsInfo(ev, a)) {
      var q = document.createElement("button");
      q.type = "button";
      q.className = "nw-evt__quote";
      q.textContent = "기사 속 근거: " + ev.quote;
      q.title = ev.quote;
      q.addEventListener("click", function () { q.classList.toggle("is-open"); });
      item.appendChild(q);
    }

    var foot = document.createElement("div");
    foot.className = "nw-evt__foot";
    var m = document.createElement("span");
    m.className = "nw-evt__meta";
    m.textContent = pressAndDate(a);
    foot.appendChild(m);
    item.appendChild(foot);
    var others = sameNewsOthers(a, r.l);
    if (others.length) item.appendChild(sameNewsBlock(others));
    return item;
  }

  // AI 요약 데이터 자체가 없는(구버전) news.json을 만났을 때만 쓰는 예전 방식 렌더러.
  function renderCompanyModalLegacyList(articles, baseMetaParts) {
    var metaParts = baseMetaParts.concat("기사 " + articles.length.toLocaleString("ko-KR") + "건");
    el.companyModalMeta.textContent = metaParts.join(" / ");

    if (!articles.length) {
      var empty = document.createElement("p");
      empty.className = "pv-company-modal__empty";
      empty.textContent = "최근 수집된 기사가 없습니다.";
      el.companyModalBody.appendChild(empty);
      el.companyModalViewAllBtn.hidden = true;
      return;
    }

    var shown = articles.slice(0, COMPANY_MODAL_LIMIT);
    shown.forEach(function (a) {
      el.companyModalBody.appendChild(buildCompanyModalItem(a));
    });

    if (articles.length > shown.length) {
      var more = document.createElement("p");
      more.className = "pv-company-modal__empty";
      more.textContent = "외 " + (articles.length - shown.length).toLocaleString("ko-KR") + "건 더 있습니다. 아래 \"이 고객사 기사 전체 보기\"에서 확인하세요.";
      el.companyModalBody.appendChild(more);
    }

    el.companyModalViewAllBtn.hidden = false;
  }

  function buildCompanyModalRefItem(a) {
    var href = a.originallink || a.link || "";
    var item = document.createElement(href ? "a" : "div");
    item.className = "pv-company-modal__ref-item";
    if (href) {
      item.href = href;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    }
    var title = document.createElement("span");
    title.className = "pv-company-modal__ref-item-title";
    title.textContent = a.title || "";
    var meta = document.createElement("span");
    meta.className = "pv-company-modal__ref-item-meta";
    meta.textContent = pressAndDate(a);
    item.appendChild(title);
    item.appendChild(meta);
    return item;
  }

  function buildCompanyModalItem(a) {
    var href = a.originallink || a.link || "";
    var item = document.createElement(href ? "a" : "div");
    item.className = "pv-company-modal__item";
    if (href) {
      item.href = href;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    }

    var badges = document.createElement("div");
    badges.className = "pv-company-modal__item-badges";
    if (isFreshArticle(a)) {
      var freshBadge = document.createElement("span");
      freshBadge.className = "pv-badge-pill pv-badge-pill--new";
      freshBadge.textContent = "오늘 새 기사";
      badges.appendChild(freshBadge);
    }
    item.appendChild(badges);

    var title = document.createElement("p");
    title.className = "pv-company-modal__item-title";
    title.textContent = a.title || "";
    item.appendChild(title);

    if (a.description) {
      var desc = document.createElement("p");
      desc.className = "pv-company-modal__item-desc";
      desc.textContent = a.description;
      item.appendChild(desc);
    }

    if ((a.matched_sub_names || []).length) {
      var relWrap = document.createElement("div");
      relWrap.className = "pv-related-badges";
      a.matched_sub_names.forEach(function (sub) {
        var b = document.createElement("span");
        b.className = "pv-badge-pill pv-badge-pill--rel";
        b.textContent = "관련: " + sub;
        relWrap.appendChild(b);
      });
      item.appendChild(relWrap);
    }

    var meta = document.createElement("p");
    meta.className = "pv-company-modal__item-meta";
    meta.textContent = pressAndDate(a);
    item.appendChild(meta);

    return item;
  }

  // ---------- 자동완성 ----------
  var autocompleteItems = [];
  var autocompleteActiveIndex = -1;

  function buildAutocompleteData(text) {
    if (!text) return [];
    var t = text.toLowerCase();
    // 2026-08-25 수정: 여러 회사가 함께 걸린 기사의 team/cell/reps를 그대로 쓰면 자동완성
    // 미리보기(예: "OO 검색 -> 담당: 김훈")에 실제로는 무관한 담당자가 나올 수 있어서,
    // 회사별/담당자별 연결 정보(company_links)를 기준으로 만든다.
    var companyMap = {};
    var repMap = {};
    state.articles.forEach(function (a) {
      (a.company_links || []).forEach(function (l) {
        if (l.name && !companyMap[l.name]) {
          companyMap[l.name] = { team: l.team, cell: l.cell, reps: l.reps };
        }
        (l.reps || []).forEach(function (rep) {
          if (rep && !repMap[rep]) repMap[rep] = { team: l.team, cell: l.cell };
        });
      });
    });

    (state.roster || []).forEach(function (l) {
      (l.reps || []).forEach(function (rep) {
        if (rep && !repMap[rep]) repMap[rep] = { team: l.team, cell: l.cell };
      });
    });
    var repResults = Object.keys(repMap)
      .filter(function (r) { return r.toLowerCase().indexOf(t) !== -1; })
      .slice(0, 5)
      .map(function (r) {
        var info = repMap[r];
        return { kind: "담당자", value: r, meta: [].concat(info.team || [], info.cell || []).join(" / ") };
      });

    var companyResults = Object.keys(companyMap)
      .filter(function (c) { return c.toLowerCase().indexOf(t) !== -1; })
      .slice(0, 6)
      .map(function (c) {
        var info = companyMap[c];
        var rep = (info.reps || [])[0];
        return { kind: "고객사", value: c, meta: rep ? "담당: " + rep : "" };
      });

    return repResults.concat(companyResults).slice(0, 8);
  }

  function renderAutocomplete(items) {
    autocompleteItems = items;
    autocompleteActiveIndex = -1;
    el.autocomplete.innerHTML = "";
    if (!items.length) {
      el.autocomplete.hidden = true;
      return;
    }
    items.forEach(function (item, idx) {
      var row = document.createElement("div");
      row.className = "pv-autocomplete__item";
      row.setAttribute("role", "option");
      row.dataset.index = String(idx);
      var kind = document.createElement("div");
      kind.className = "pv-autocomplete__kind";
      kind.textContent = item.kind;
      var label = document.createElement("div");
      label.className = "pv-autocomplete__label";
      label.textContent = item.value;
      row.appendChild(kind);
      row.appendChild(label);
      if (item.meta) {
        var meta = document.createElement("div");
        meta.className = "pv-autocomplete__meta";
        meta.textContent = item.meta;
        row.appendChild(meta);
      }
      row.addEventListener("mousedown", function (e) {
        e.preventDefault();
        selectAutocompleteItem(item);
      });
      el.autocomplete.appendChild(row);
    });
    el.autocomplete.hidden = false;
  }

  function selectAutocompleteItem(item) {
    state.searchText = item.value;
    el.searchInput.value = item.value;
    el.autocomplete.hidden = true;
    state.visibleCount = PAGE_SIZE;
    applyFiltersAndRender();
  }

  function updateAutocompleteActive() {
    var rows = el.autocomplete.querySelectorAll(".pv-autocomplete__item");
    rows.forEach(function (r, i) {
      r.classList.toggle("is-active", i === autocompleteActiveIndex);
    });
  }

  // ---------- 이벤트 바인딩 ----------
  var autocompleteDebounce = null;
  var filterDebounce = null;
  el.searchInput.addEventListener("input", function (e) {
    var value = e.target.value;
    clearTimeout(autocompleteDebounce);
    clearTimeout(filterDebounce);
    autocompleteDebounce = setTimeout(function () {
      renderAutocomplete(buildAutocompleteData(value.trim()));
    }, 220);
    filterDebounce = setTimeout(function () {
      state.searchText = value.trim();
      state.visibleCount = PAGE_SIZE;
      applyFiltersAndRender();
    }, 260);
  });

  el.searchInput.addEventListener("keydown", function (e) {
    if (el.autocomplete.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      autocompleteActiveIndex = Math.min(autocompleteActiveIndex + 1, autocompleteItems.length - 1);
      updateAutocompleteActive();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      autocompleteActiveIndex = Math.max(autocompleteActiveIndex - 1, 0);
      updateAutocompleteActive();
    } else if (e.key === "Enter") {
      if (autocompleteActiveIndex >= 0 && autocompleteItems[autocompleteActiveIndex]) {
        e.preventDefault();
        selectAutocompleteItem(autocompleteItems[autocompleteActiveIndex]);
      } else if (state.searchText) {
        el.autocomplete.hidden = true;
      }
    } else if (e.key === "Escape") {
      el.autocomplete.hidden = true;
    }
  });

  document.addEventListener("click", function (e) {
    if (!el.autocomplete.contains(e.target) && e.target !== el.searchInput) {
      el.autocomplete.hidden = true;
    }
  });

  el.teamSelect.addEventListener("change", function (e) {
    state.selectedTeam = e.target.value;
    state.visibleCount = PAGE_SIZE;
    buildFilterOptions();
    applyFiltersAndRender();
  });
  el.cellSelect.addEventListener("change", function (e) {
    state.selectedCell = e.target.value;
    state.visibleCount = PAGE_SIZE;
    buildFilterOptions();
    applyFiltersAndRender();
  });
  el.repSelect.addEventListener("change", function (e) {
    state.selectedRep = e.target.value;
    state.visibleCount = PAGE_SIZE;
    buildFilterOptions();
    applyFiltersAndRender();
  });

  function resetAllFilters() {
    state.selectedTeam = "전체";
    state.selectedCell = "전체";
    state.selectedRep = "전체";
    state.majorOnly = false;
    state.searchText = "";
    state.visibleCount = PAGE_SIZE;
    el.searchInput.value = "";
    el.majorOnlyCheckbox.checked = false;
    buildFilterOptions();
    applyFiltersAndRender();
  }
  el.resetBtn.addEventListener("click", resetAllFilters);

  el.loadMoreBtn.addEventListener("click", function () {
    el.loadMoreBtn.disabled = true;
    state.visibleCount += PAGE_SIZE;
    render();
  });

  // ---------- 담당 고객 목록 드로어 (1024px 미만: 햄버거 버튼으로 열고 닫기) ----------
  function openSide() {
    el.side.classList.add("is-open");
    el.sideBackdrop.hidden = false;
    el.sideToggleBtn.setAttribute("aria-expanded", "true");
    document.body.classList.add("pv-no-scroll");
  }
  function closeSide() {
    el.side.classList.remove("is-open");
    el.sideBackdrop.hidden = true;
    el.sideToggleBtn.setAttribute("aria-expanded", "false");
    document.body.classList.remove("pv-no-scroll");
  }
  el.sideToggleBtn.addEventListener("click", openSide);
  el.sideCloseBtn.addEventListener("click", closeSide);
  el.sideBackdrop.addEventListener("click", closeSide);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && el.side.classList.contains("is-open")) closeSide();
  });
  // 데스크톱 폭으로 넘어가면(사이드바가 항상 보이는 레이아웃으로 전환) 드로어 상태를 정리
  window.addEventListener("resize", function () {
    if (window.matchMedia("(min-width: 1024px)").matches) closeSide();
  });
  el.majorOnlyCheckbox.addEventListener("change", function (e) {
    state.majorOnly = e.target.checked;
    state.visibleCount = PAGE_SIZE;
    applyFiltersAndRender();
  });

  // ---------- 고객사 핵심 기사 요약 팝업 이벤트 ----------
  el.companyModalCloseBtn.addEventListener("click", closeCompanyModal);
  el.companyModalBackdrop.addEventListener("click", closeCompanyModal);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !el.companyModal.hidden) closeCompanyModal();
  });
  el.companyModalViewAllBtn.addEventListener("click", function () {
    // 2026-09-07 수정: 제목(label)은 동명이사인 경우 "이름 (팀·담당자)"가 붙어 있어 그대로
    // 검색하면 실제 기사와 매칭이 안 된다. 검색에는 항상 원본 회사명(dataset.name)을 쓴다.
    var name = el.companyModalTitle.dataset.name || el.companyModalTitle.textContent;
    closeCompanyModal();
    state.searchText = name;
    el.searchInput.value = name;
    state.visibleCount = PAGE_SIZE;
    applyFiltersAndRender();
    if (prefersReducedMotion) {
      window.scrollTo(0, 0);
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });

  // ---------- TOP 버튼 ----------
  window.addEventListener("scroll", function () {
    el.topBtn.hidden = window.scrollY <= SCROLL_TOP_THRESHOLD;
  });
  el.topBtn.addEventListener("click", function () {
    var target = document.querySelector(".pv-search-wrap") || document.body;
    if (prefersReducedMotion) {
      window.scrollTo(0, 0);
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    target.querySelector && target.querySelector("input") && null;
  });

  function applyFiltersAndRender() {
    render({ freshRender: false });
  }

  // ---------- 데이터 로드 ----------
  function loadData() {
    fetch("data/news.json", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("news.json 로드 실패: " + res.status);
        return res.json();
      })
      .then(function (data) {
        state.articles = (data.articles || []).map(function (a) {
          // 2026-08-24 변경: fetch_news.py가 기사당 회사를 "company"(단일 문자열) 대신
          // "companies"(배열)로 내려주도록 바뀜(같은 브랜드의 여러 사업장이 같은 기사에
          // 동시에 걸리는 경우, 먼저 처리된 사업장만 기사를 독점하지 않고 관련된 모든
          // 사업장에 표시하기 위함).
          //
          // 2026-08-25 추가 수정: fetch_news.py가 회사별 팀/셀/담당자 연결 정보를
          // "company_links" 배열(회사마다 자기 team/cell/reps를 따로 보존)로 내려주도록
          // 한 단계 더 바뀜. 예전 news.json에는 company_links가 없을 수 있으므로,
          // 아래에서 예전 스키마들을 모두 company_links 형태로 맞춰준다.
          // - companies(배열) + team/cell/reps(전체 통합 배열)만 있는 경우: 회사별 정확한
          //   연결 정보 자체가 데이터에 없으므로 임시로 통합 배열을 그대로 쓴다. 이 경우는
          //   fetch_news.py가 다음 번 실행되어 news.json이 새로 만들어지면 정확한 값으로
          //   자동 교체된다.
          // - company(단일 문자열)만 있는 가장 오래된 스키마: 회사가 하나뿐이므로 그대로
          //   정확하게 옮길 수 있다.
          if (!a.company_links) {
            if (a.companies && a.companies.length) {
              a.company_links = a.companies.map(function (name) {
                return {
                  name: name,
                  team: a.team || [],
                  cell: a.cell || [],
                  reps: a.reps || [],
                  matched_sub_names: a.matched_sub_names || [],
                };
              });
            } else if (a.company) {
              a.company_links = [{
                name: a.company,
                team: a.team || [],
                cell: a.cell || [],
                reps: a.reps || [],
                matched_sub_names: a.matched_sub_names || [],
              }];
            } else {
              a.company_links = [];
            }
          }
          if (!a.companies) {
            a.companies = a.company_links.map(function (l) { return l.name; });
          }
          return a;
        });
        // 이름이 같은 서로 다른 고객(code) 수: 카드 "AI 요약" 서랍 제목 구분용
        var seenCode = {};
        state.nameCodeCount = {};
        state.articles.forEach(function (a) {
          a.company_links.forEach(function (l) {
            var c = l.code || l.name;
            if (!c || seenCode[c]) return;
            seenCode[c] = true;
            state.nameCodeCount[l.name] = (state.nameCodeCount[l.name] || 0) + 1;
          });
        });
        state.generatedAt = data.generated_at || null;
        state.lookbackDays = data.lookback_days || null;
        state.totalArticles = data.total_articles || state.articles.length;
        // company_summaries가 아예 없으면(구버전 데이터거나 AI 요약 기능이 꺼진 날) null로 두어
        // 팝업이 기존 방식(기사 목록)으로 자동 전환되도록 한다. 빈 객체({})는 "기능은 켜져 있지만
        // 이 고객사는 최근 기사가 없다"는 뜻이므로 null과 구분해야 한다.
        state.companySummaries = data.company_summaries || null;
        state.roster = Array.isArray(data.roster) ? data.roster : null;
        state.summaryLookbackDays = data.summary_lookback_days || null;
        state.loadError = null;

        // 2026-11-24 사건 분류가 있는 데이터면: 피드는 최신순, 같은 소식 묶음/회사별 사건 수 준비
        state.hasEv = state.articles.some(function (a) {
          return (a.company_links || []).some(function (l) { return !!l.ev; });
        });
        state.bizDays = data.biz_days || 90;
        if (state.hasEv) {
          state.articles.sort(function (x, y) { return (y.pubDate_iso || "").localeCompare(x.pubDate_iso || ""); });
          buildGroupIndex();
          buildEvCounts();
          ensureEvControls();
        }

        buildFilterOptions();
        renderStatus();
        render({ freshRender: true });
        openFromHash();
      })
      .catch(function (err) {
        state.loadError = err;
        renderStatus();
        el.errorState.hidden = false;
        fillStateBox(el.errorState, "뉴스 데이터를 불러오지 못했습니다", "잠시 후 다시 시도해 주세요.", [
          ["다시 시도", loadData],
        ]);
        el.articleList.innerHTML = "";
        el.emptyState.hidden = true;
        el.loadMoreBtn.hidden = true;
        console.error(err);
      });
  }

  // ---------- (2026-11-23) 영업 허브 고객대시보드에서 #c=<고객코드> 로 열면 그 고객 AI 요약 서랍을 연다 ----------
  function openFromHash() {
    var m = /(?:^#|&)c=([^&]+)/.exec(location.hash);
    if (!m || !state.articles) return;
    var code = m[1];
    try { code = decodeURIComponent(code); } catch (e) { /* 그대로 */ }
    var link = null;
    state.articles.some(function (a) {
      return (a.company_links || []).some(function (l) { if ((l.code || l.name) === code) { link = l; return true; } return false; });
    });
    if (!link && !(state.companySummaries && state.companySummaries[code])) return;
    var name = link ? link.name : code;
    openCompanyModal(code, name, link ? linkLabel(link) : name);
  }
  window.addEventListener("hashchange", openFromHash);

  // ---------- 헤더 실제 높이를 CSS 변수로 반영 (태블릿/노트북에서 좌측 패널이 고정 헤더 밑에 오도록) ----------
  var headerEl = document.querySelector(".pv-header");
  function syncHeaderHeight() {
    try {
      if (!headerEl || !document.documentElement || !document.documentElement.style.setProperty) return;
      document.documentElement.style.setProperty("--header-h", headerEl.offsetHeight + "px");
    } catch (e) { /* 구형 브라우저 등에서 실패해도 레이아웃 자체는 정상 동작 */ }
  }
  syncHeaderHeight();
  window.addEventListener("resize", function () {
    clearTimeout(syncHeaderHeight._t);
    syncHeaderHeight._t = setTimeout(syncHeaderHeight, 150);
  });
  window.addEventListener("load", syncHeaderHeight);

  loadData();
})();
