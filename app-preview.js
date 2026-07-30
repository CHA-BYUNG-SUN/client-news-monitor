(function () {
  "use strict";

  var PAGE_SIZE = 30;
  var SCROLL_TOP_THRESHOLD = 500;
  var RECENT_SEARCH_KEY = "pv_recent_searches";
  var RECENT_SEARCH_MAX = 6;
  var FRESH_HOURS = 24;
  var STALE_WARN_HOURS = 24;
  var STALE_DANGER_HOURS = 48;

  var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var state = {
    articles: [],
    tagOrder: [],
    generatedAt: null,
    lookbackDays: null,
    totalArticles: 0,
    selectedTeam: "전체",
    selectedCell: "전체",
    selectedRep: "전체",
    selectedTag: "전체",
    majorOnly: false,
    searchText: "",
    visibleCount: PAGE_SIZE,
    prevIds: [],
    loadError: null,
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
    recentList: document.getElementById("pvRecentList"),
    bottomBar: document.getElementById("pvBottomBar"),
    openSheetBtn: document.getElementById("pvOpenSheetBtn"),
    filterCountBadge: document.getElementById("pvFilterCountBadge"),
    topBtn: document.getElementById("pvTopBtn"),
    tagChips: document.getElementById("pvTagChips"),
    majorOnlyCheckbox: document.getElementById("pvMajorOnly"),
    panel: document.getElementById("pvSecondaryPanel"),
    backdrop: document.getElementById("pvSheetBackdrop"),
    sheetCloseBtn: document.getElementById("pvSheetCloseBtn"),
    sheetApplyBtn: document.getElementById("pvSheetApplyBtn"),
    sheetResetBtn: document.getElementById("pvSheetResetBtn"),
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
      if (short) return mm + "." + dd + " " + hh + ":" + mi;
      return d.getFullYear() + "." + mm + "." + dd + " " + hh + ":" + mi;
    } catch (e) {
      return iso || "";
    }
  }

  function isMobileViewport() {
    return window.matchMedia("(max-width: 767px)").matches;
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
      el.statusText.textContent = "업데이트 정보 확인 중…";
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
    el.statusText.textContent = label + " · " + fmtDateTime(state.generatedAt, true);
  }

  function renderCounts(filteredCount) {
    var parts = [];
    if (state.lookbackDays) parts.push("최근 " + state.lookbackDays + "일");
    parts.push("전체 " + state.totalArticles.toLocaleString("ko-KR") + "건");
    var line = parts.join(" · ");
    if (filteredCount !== state.totalArticles) {
      line += " · 검색 결과 " + filteredCount.toLocaleString("ko-KR") + "건";
    }
    el.counts.textContent = line;
  }

  // ---------- 필터 옵션 (종속 필터) ----------
  function articleMatchesTeamCell(a, team, cell) {
    if (team !== "전체" && (a.team || []).indexOf(team) === -1) return false;
    if (cell !== "전체" && (a.cell || []).indexOf(cell) === -1) return false;
    return true;
  }

  function buildFilterOptions() {
    var teamPool = state.articles;
    var teams = uniqueSorted(teamPool.reduce(function (acc, a) { return acc.concat(a.team || []); }, []));

    var cellPool = state.articles.filter(function (a) {
      return state.selectedTeam === "전체" || (a.team || []).indexOf(state.selectedTeam) !== -1;
    });
    var cells = uniqueSorted(cellPool.reduce(function (acc, a) { return acc.concat(a.cell || []); }, []));

    var repPool = state.articles.filter(function (a) {
      return articleMatchesTeamCell(a, state.selectedTeam, state.selectedCell);
    });
    var reps = uniqueSorted(repPool.reduce(function (acc, a) { return acc.concat(a.reps || []); }, []));

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

  function buildTagChips() {
    var tags = ["전체"].concat(state.tagOrder);
    el.tagChips.innerHTML = "";
    tags.forEach(function (tag) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pv-chip" + (tag === state.selectedTag ? " is-active" : "");
      btn.textContent = tag;
      btn.addEventListener("click", function () {
        state.selectedTag = tag;
        state.visibleCount = PAGE_SIZE;
        buildTagChips();
        applyFiltersAndRender();
      });
      el.tagChips.appendChild(btn);
    });
  }

  // ---------- 검색 매칭 ----------
  function matchesSearch(a, text) {
    if (!text) return true;
    var t = text.toLowerCase();
    if ((a.company || "").toLowerCase().indexOf(t) !== -1) return true;
    if ((a.matched_sub_names || []).some(function (s) { return (s || "").toLowerCase().indexOf(t) !== -1; })) return true;
    if ((a.reps || []).some(function (r) { return (r || "").toLowerCase().indexOf(t) !== -1; })) return true;
    return false;
  }

  function getFiltered() {
    return state.articles.filter(function (a) {
      if (state.selectedTag !== "전체" && a.tag_label !== state.selectedTag) return false;
      if (state.selectedTeam !== "전체" && (a.team || []).indexOf(state.selectedTeam) === -1) return false;
      if (state.selectedCell !== "전체" && (a.cell || []).indexOf(state.selectedCell) === -1) return false;
      if (state.selectedRep !== "전체" && (a.reps || []).indexOf(state.selectedRep) === -1) return false;
      if (state.majorOnly && !a.major) return false;
      if (!matchesSearch(a, state.searchText)) return false;
      return true;
    });
  }

  function hasActiveFilters() {
    return state.selectedTeam !== "전체" || state.selectedCell !== "전체" || state.selectedRep !== "전체" ||
      state.selectedTag !== "전체" || state.majorOnly || !!state.searchText;
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

  function buildCard(a) {
    var href = a.originallink || a.link || "";
    var card = document.createElement(href ? "a" : "div");
    card.className = "pv-card";
    if (href) {
      card.href = href;
      card.target = "_blank";
      card.rel = "noopener noreferrer";
      card.addEventListener("click", function () {
        if (card.dataset.opened) return;
        card.dataset.opened = "1";
        setTimeout(function () { delete card.dataset.opened; }, 600);
      });
    }

    var body = document.createElement("div");
    body.className = "pv-card__body";

    var badges = document.createElement("div");
    badges.className = "pv-card__badges";
    if (isFreshArticle(a)) {
      var freshBadge = document.createElement("span");
      freshBadge.className = "pv-badge-pill pv-badge-pill--new";
      freshBadge.textContent = "오늘 새 기사";
      badges.appendChild(freshBadge);
    }
    if (a.tag_label) {
      var tagBadge = document.createElement("span");
      tagBadge.className = "pv-badge-pill pv-badge-pill--" + a.tag_label;
      tagBadge.textContent = a.tag_label;
      badges.appendChild(tagBadge);
    }
    if (a.major) {
      var majorBadge = document.createElement("span");
      majorBadge.className = "pv-badge-pill";
      majorBadge.style.background = "#f2c94c33";
      majorBadge.style.color = "#7a5b0a";
      majorBadge.textContent = "메이저 언론사";
      badges.appendChild(majorBadge);
    }
    var companySpan = document.createElement("span");
    companySpan.className = "pv-card__company";
    companySpan.textContent = a.company || "";
    badges.appendChild(companySpan);
    body.appendChild(badges);

    var title = document.createElement("p");
    title.className = "pv-card__title";
    title.textContent = a.title || "";
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
        b.className = "pv-badge-pill";
        b.style.background = "var(--border-soft)";
        b.style.color = "var(--text-secondary)";
        b.textContent = "관련: " + sub;
        relWrap.appendChild(b);
      });
      body.appendChild(relWrap);
    }

    var meta = document.createElement("div");
    meta.className = "pv-card__meta";
    var left = document.createElement("span");
    left.textContent = (a.press || "") + (a.pubDate_display ? " · " + a.pubDate_display : "");
    var right = document.createElement("span");
    var repInfo = [].concat(a.team || [], a.cell || [], a.reps || []).filter(Boolean).join(" · ");
    right.textContent = repInfo;
    meta.appendChild(left);
    meta.appendChild(right);
    body.appendChild(meta);

    card.appendChild(body);

    if (href) {
      var arrow = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      arrow.setAttribute("viewBox", "0 0 24 24");
      arrow.setAttribute("class", "pv-card__arrow");
      arrow.setAttribute("aria-hidden", "true");
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M9 6l6 6-6 6");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "currentColor");
      path.setAttribute("stroke-width", "2");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      arrow.appendChild(path);
      card.appendChild(arrow);
    }

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
    updateFeedTitle();
    renderSidePanel(filtered);
    updateResetState();
    updateFilterBadge();

    var visible = filtered.slice(0, state.visibleCount);
    el.errorState.hidden = true;

    if (filtered.length === 0) {
      el.articleList.innerHTML = "";
      el.emptyState.hidden = false;
      el.emptyState.innerHTML = "";
      var msg = document.createElement("p");
      msg.textContent = "조건에 맞는 고객 기사가 없습니다. 검색어 또는 필터를 다시 확인해 주세요.";
      el.emptyState.appendChild(msg);
      var actions = document.createElement("div");
      actions.className = "pv-state-actions";
      var resetBtn = document.createElement("button");
      resetBtn.type = "button";
      resetBtn.textContent = "필터 전체 해제";
      resetBtn.addEventListener("click", resetAllFilters);
      var clearSearchBtn = document.createElement("button");
      clearSearchBtn.type = "button";
      clearSearchBtn.textContent = "검색어 지우기";
      clearSearchBtn.addEventListener("click", function () {
        state.searchText = "";
        el.searchInput.value = "";
        applyFiltersAndRender();
      });
      actions.appendChild(resetBtn);
      actions.appendChild(clearSearchBtn);
      el.emptyState.appendChild(actions);
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
    el.loadMoreBtn.disabled = false;
    if (typeof syncHeaderHeight === "function") setTimeout(syncHeaderHeight, 0);
  }

  function updateResetState() {
    el.resetBtn.disabled = !hasActiveFilters();
  }

  function updateFilterBadge() {
    var count = 0;
    if (state.selectedTag !== "전체") count++;
    if (state.majorOnly) count++;
    if (count > 0) {
      el.filterCountBadge.hidden = false;
      el.filterCountBadge.textContent = String(count);
    } else {
      el.filterCountBadge.hidden = true;
    }
  }

  // ---------- 담당 고객 / 최근 검색 (사이드 패널) ----------
  function loadRecentSearches() {
    try {
      var raw = window.localStorage.getItem(RECENT_SEARCH_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function saveRecentSearch(text) {
    if (!text) return;
    try {
      var list = loadRecentSearches().filter(function (t) { return t !== text; });
      list.unshift(text);
      list = list.slice(0, RECENT_SEARCH_MAX);
      window.localStorage.setItem(RECENT_SEARCH_KEY, JSON.stringify(list));
    } catch (e) { /* 저장 실패 시 무시 */ }
  }

  function renderSidePanel(filtered) {
    el.recentList.innerHTML = "";

    var recents = loadRecentSearches();
    if (recents.length) {
      var recentHeader = document.createElement("p");
      recentHeader.className = "pv-side__title";
      recentHeader.style.marginTop = "0";
      recentHeader.textContent = "최근 검색";
      el.recentList.appendChild(recentHeader);
      recents.forEach(function (text) {
        el.recentList.appendChild(buildRecentItem(text, iconInitials(text)));
      });
    }

    var companies = uniqueSorted(filtered.map(function (a) { return a.company; })).slice(0, 8);
    if (companies.length) {
      var companyHeader = document.createElement("p");
      companyHeader.className = "pv-side__title";
      companyHeader.textContent = "담당 고객";
      el.recentList.appendChild(companyHeader);
      companies.forEach(function (name) {
        el.recentList.appendChild(buildRecentItem(name, iconInitials(name)));
      });
    }
  }

  function buildRecentItem(label, iconText) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pv-recent-item";
    var icon = document.createElement("span");
    icon.className = "pv-recent-item__icon";
    icon.textContent = iconText;
    var span = document.createElement("span");
    span.className = "pv-recent-item__label";
    span.textContent = label;
    btn.appendChild(icon);
    btn.appendChild(span);
    btn.addEventListener("click", function () {
      state.searchText = label;
      el.searchInput.value = label;
      state.visibleCount = PAGE_SIZE;
      applyFiltersAndRender();
    });
    return btn;
  }

  // ---------- 자동완성 ----------
  var autocompleteItems = [];
  var autocompleteActiveIndex = -1;

  function buildAutocompleteData(text) {
    if (!text) return [];
    var t = text.toLowerCase();
    var companyMap = {};
    var repMap = {};
    state.articles.forEach(function (a) {
      if (a.company && !companyMap[a.company]) {
        companyMap[a.company] = { team: a.team, cell: a.cell, reps: a.reps };
      }
      (a.reps || []).forEach(function (rep) {
        if (rep && !repMap[rep]) repMap[rep] = { team: a.team, cell: a.cell };
      });
    });

    var repResults = Object.keys(repMap)
      .filter(function (r) { return r.toLowerCase().indexOf(t) !== -1; })
      .slice(0, 5)
      .map(function (r) {
        var info = repMap[r];
        return { kind: "담당자", value: r, meta: [].concat(info.team || [], info.cell || []).join(" · ") };
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
    saveRecentSearch(item.value);
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
        saveRecentSearch(state.searchText);
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
    state.selectedTag = "전체";
    state.majorOnly = false;
    state.searchText = "";
    state.visibleCount = PAGE_SIZE;
    el.searchInput.value = "";
    el.majorOnlyCheckbox.checked = false;
    buildFilterOptions();
    buildTagChips();
    applyFiltersAndRender();
  }
  el.resetBtn.addEventListener("click", resetAllFilters);
  el.sheetResetBtn.addEventListener("click", function () {
    resetAllFilters();
    closeSheet();
  });

  el.loadMoreBtn.addEventListener("click", function () {
    el.loadMoreBtn.disabled = true;
    state.visibleCount += PAGE_SIZE;
    render();
  });

  // ---------- 이슈유형/메이저 패널 (모바일 바텀시트 / 태블릿·노트북 인라인) ----------
  function openSheet() {
    if (!isMobileViewport()) return;
    el.panel.classList.add("is-open");
    el.backdrop.hidden = false;
  }
  function closeSheet() {
    el.panel.classList.remove("is-open");
    el.backdrop.hidden = true;
  }
  el.openSheetBtn.addEventListener("click", openSheet);
  el.sheetCloseBtn.addEventListener("click", closeSheet);
  el.backdrop.addEventListener("click", closeSheet);
  el.sheetApplyBtn.addEventListener("click", function () {
    state.visibleCount = PAGE_SIZE;
    applyFiltersAndRender();
    closeSheet();
  });
  el.majorOnlyCheckbox.addEventListener("change", function (e) {
    state.majorOnly = e.target.checked;
    updateFilterBadge();
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
        state.articles = data.articles || [];
        state.tagOrder = uniqueSorted(state.articles.map(function (a) { return a.tag_label; }));
        state.generatedAt = data.generated_at || null;
        state.lookbackDays = data.lookback_days || null;
        state.totalArticles = data.total_articles || state.articles.length;
        state.loadError = null;

        buildFilterOptions();
        buildTagChips();
        renderStatus();
        render({ freshRender: true });
      })
      .catch(function (err) {
        state.loadError = err;
        renderStatus();
        el.errorState.hidden = false;
        el.errorState.innerHTML = "";
        var p = document.createElement("p");
        p.textContent = "뉴스 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
        var actions = document.createElement("div");
        actions.className = "pv-state-actions";
        var retry = document.createElement("button");
        retry.type = "button";
        retry.textContent = "다시 시도";
        retry.addEventListener("click", loadData);
        actions.appendChild(retry);
        el.errorState.appendChild(p);
        el.errorState.appendChild(actions);
        el.articleList.innerHTML = "";
        el.emptyState.hidden = true;
        el.loadMoreBtn.hidden = true;
        console.error(err);
      });
  }

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
