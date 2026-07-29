(function () {
  "use strict";

  const PAGE_SIZE = 24;
  const ALL = "전체";

  const state = {
    articles: [],
    tagOrder: [],
    selectedTag: ALL,
    selectedTeam: ALL,
    selectedCell: ALL,
    selectedRep: ALL,
    searchText: "",
    majorOnly: false,
    visibleCount: PAGE_SIZE,
  };

  const el = {
    updateStatus: document.getElementById("updateStatus"),
    updatedAt: document.getElementById("updatedAt"),
    tagFilters: document.getElementById("tagFilters"),
    teamSelect: document.getElementById("teamSelect"),
    cellSelect: document.getElementById("cellSelect"),
    repSelect: document.getElementById("repSelect"),
    searchInput: document.getElementById("searchInput"),
    clearSearchBtn: document.getElementById("clearSearchBtn"),
    majorOnly: document.getElementById("majorOnly"),
    activeFilterChips: document.getElementById("activeFilterChips"),
    activeFilterCount: document.getElementById("activeFilterCount"),
    issueTitle: document.getElementById("issueTitle"),
    resultCount: document.getElementById("resultCount"),
    articleList: document.getElementById("articleList"),
    customerList: document.getElementById("customerList"),
    emptyState: document.getElementById("emptyState"),
    loadMoreBtn: document.getElementById("loadMoreBtn"),
    resetFiltersBtn: document.getElementById("resetFiltersBtn"),
    emptyResetBtn: document.getElementById("emptyResetBtn"),
    clearCustomerBtn: document.getElementById("clearCustomerBtn"),
    scrollTopBtn: document.getElementById("scrollTopBtn"),
    filterPanel: document.getElementById("filterPanel"),
    filterOverlay: document.getElementById("filterOverlay"),
    openFiltersBtn: document.getElementById("openFiltersBtn"),
    closeFiltersBtn: document.getElementById("closeFiltersBtn"),
    applyFiltersBtn: document.getElementById("applyFiltersBtn"),
  };

  function uniqueSorted(values) {
    return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "ko"));
  }

  function formatUpdateDate(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "업데이트 일시 확인 불가";
    const dateText = new Intl.DateTimeFormat("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
    const elapsedHours = (Date.now() - date.getTime()) / 3600000;
    el.updateStatus.classList.toggle("is-delayed", elapsedHours > 24 && elapsedHours <= 48);
    el.updateStatus.classList.toggle("is-old", elapsedHours > 48);
    return `최신 업데이트 ${dateText}`;
  }

  function normalizeColor(value) {
    if (typeof value !== "string") return "#1768e5";
    return /^#[0-9a-f]{3,8}$/i.test(value.trim()) ? value.trim() : "#1768e5";
  }

  function getOrganizationPool() {
    return state.articles.filter((article) => {
      if (state.selectedTeam !== ALL && !(article.team || []).includes(state.selectedTeam)) return false;
      if (state.selectedCell !== ALL && !(article.cell || []).includes(state.selectedCell)) return false;
      return true;
    });
  }

  function fillSelect(select, values, selected, allLabel) {
    const fragment = document.createDocumentFragment();
    const allOption = document.createElement("option");
    allOption.value = ALL;
    allOption.textContent = allLabel;
    fragment.appendChild(allOption);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      fragment.appendChild(option);
    });
    select.replaceChildren(fragment);
    select.value = values.includes(selected) ? selected : ALL;
  }

  function buildSelectFilters() {
    const teams = uniqueSorted(state.articles.flatMap((article) => article.team || []));
    const teamPool = state.selectedTeam === ALL
      ? state.articles
      : state.articles.filter((article) => (article.team || []).includes(state.selectedTeam));
    const cells = uniqueSorted(teamPool.flatMap((article) => article.cell || []));

    if (state.selectedCell !== ALL && !cells.includes(state.selectedCell)) {
      state.selectedCell = ALL;
    }

    const repPool = teamPool.filter((article) =>
      state.selectedCell === ALL || (article.cell || []).includes(state.selectedCell)
    );
    const reps = uniqueSorted(repPool.flatMap((article) => article.reps || []));

    if (state.selectedRep !== ALL && !reps.includes(state.selectedRep)) {
      state.selectedRep = ALL;
    }

    fillSelect(el.teamSelect, teams, state.selectedTeam, "전체 팀");
    fillSelect(el.cellSelect, cells, state.selectedCell, "전체 셀");
    fillSelect(el.repSelect, reps, state.selectedRep, "전체 담당자");
  }

  function buildTagFilters() {
    const tags = [ALL, ...state.tagOrder];
    const fragment = document.createDocumentFragment();
    tags.forEach((tag) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `tag-filter-button${tag === state.selectedTag ? " is-active" : ""}`;
      button.textContent = tag === ALL ? "전체 이슈" : tag;
      button.addEventListener("click", () => {
        state.selectedTag = tag;
        state.visibleCount = PAGE_SIZE;
        buildTagFilters();
        render();
      });
      fragment.appendChild(button);
    });
    el.tagFilters.replaceChildren(fragment);
  }

  function articleMatchesSearch(article, searchText) {
    if (!searchText) return true;
    const target = searchText.toLocaleLowerCase("ko");
    const searchable = [
      article.company,
      article.title,
      ...(article.matched_sub_names || []),
      ...(article.reps || []),
    ];
    return searchable.some((value) => String(value || "").toLocaleLowerCase("ko").includes(target));
  }

  function getFilteredArticles() {
    return state.articles.filter((article) => {
      if (state.selectedTag !== ALL && article.tag_label !== state.selectedTag) return false;
      if (state.selectedTeam !== ALL && !(article.team || []).includes(state.selectedTeam)) return false;
      if (state.selectedCell !== ALL && !(article.cell || []).includes(state.selectedCell)) return false;
      if (state.selectedRep !== ALL && !(article.reps || []).includes(state.selectedRep)) return false;
      if (state.majorOnly && !article.major) return false;
      return articleMatchesSearch(article, state.searchText);
    });
  }

  function createBadge(className, text) {
    const badge = document.createElement("span");
    badge.className = className;
    badge.textContent = text;
    return badge;
  }

  function createArticleCard(article, index) {
    const card = document.createElement("a");
    card.className = "article-card issue-enter";
    card.href = article.originallink || article.link || "#";
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    card.style.setProperty("--enter-index", Math.min(index, 7));
    card.style.setProperty("--card-accent", normalizeColor(article.tag_color));

    const top = document.createElement("div");
    top.className = "card-top";

    const tagBadge = createBadge("tag-badge", article.tag_label || "주요 이슈");
    tagBadge.style.setProperty("--badge-color", normalizeColor(article.tag_color));
    top.appendChild(tagBadge);
    top.appendChild(createBadge("company-badge", article.company || "고객사"));
    if (article.major) top.appendChild(createBadge("major-badge", "주요 언론"));
    card.appendChild(top);

    const title = document.createElement("h3");
    title.className = "article-title";
    title.textContent = article.title || "기사 제목 없음";
    card.appendChild(title);

    if (article.description) {
      const description = document.createElement("p");
      description.className = "article-desc";
      description.textContent = article.description;
      card.appendChild(description);
    }

    const meta = document.createElement("div");
    meta.className = "card-meta";
    if (article.press) meta.appendChild(createBadge("", article.press));
    if (article.pubDate_display) meta.appendChild(createBadge("", article.pubDate_display));
    card.appendChild(meta);

    const ownership = [
      ...(article.team || []),
      ...(article.cell || []),
      ...(article.reps || []),
    ];
    if (ownership.length) {
      const repInfo = document.createElement("div");
      repInfo.className = "rep-info";
      repInfo.textContent = ownership.join(" · ");
      card.appendChild(repInfo);
    }

    const arrow = document.createElement("span");
    arrow.className = "card-arrow";
    arrow.setAttribute("aria-hidden", "true");
    arrow.innerHTML = '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"></path></svg>';
    card.appendChild(arrow);
    return card;
  }

  function getTitle() {
    if (state.searchText) return `${state.searchText}의 실시간 이슈`;
    if (state.selectedRep !== ALL) return `${state.selectedRep}님의 고객 이슈`;
    if (state.selectedCell !== ALL) return `${state.selectedCell} 고객 실시간 이슈`;
    if (state.selectedTeam !== ALL) return `${state.selectedTeam} 고객 실시간 이슈`;
    return "내 고객의 실시간 이슈";
  }

  function getActiveFilters() {
    const filters = [];
    if (state.selectedTeam !== ALL) filters.push(state.selectedTeam);
    if (state.selectedCell !== ALL) filters.push(state.selectedCell);
    if (state.selectedRep !== ALL) filters.push(state.selectedRep);
    if (state.selectedTag !== ALL) filters.push(state.selectedTag);
    if (state.majorOnly) filters.push("주요 언론");
    if (state.searchText) filters.push(`검색: ${state.searchText}`);
    return filters;
  }

  function renderFilterSummary() {
    const filters = getActiveFilters();
    const labels = filters.length ? filters : ["전체 고객"];
    const fragment = document.createDocumentFragment();
    labels.forEach((label) => {
      const chip = document.createElement("span");
      chip.className = `active-filter-chip${filters.length ? "" : " is-default"}`;
      chip.textContent = label;
      fragment.appendChild(chip);
    });
    el.activeFilterChips.replaceChildren(fragment);
    el.activeFilterCount.textContent = String(filters.length);
    el.activeFilterCount.hidden = filters.length === 0;
  }

  function getCustomerRows() {
    const rows = new Map();
    getOrganizationPool().forEach((article) => {
      const company = article.company;
      if (!company) return;
      const current = rows.get(company) || { company, count: 0, latest: "" };
      current.count += 1;
      if ((article.pubDate_iso || "") > current.latest) current.latest = article.pubDate_iso || "";
      rows.set(company, current);
    });
    return [...rows.values()]
      .sort((a, b) => b.latest.localeCompare(a.latest) || b.count - a.count)
      .slice(0, 10);
  }

  function renderCustomerList() {
    const fragment = document.createDocumentFragment();
    getCustomerRows().forEach((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `customer-button${state.searchText === row.company ? " is-active" : ""}`;
      const initial = document.createElement("span");
      initial.className = "customer-initial";
      initial.textContent = row.company.slice(0, 1);
      const name = document.createElement("span");
      name.className = "customer-name";
      name.textContent = row.company;
      const count = document.createElement("span");
      count.className = "customer-count";
      count.textContent = row.count;
      button.append(initial, name, count);
      button.addEventListener("click", () => {
        state.searchText = row.company;
        state.visibleCount = PAGE_SIZE;
        el.searchInput.value = row.company;
        el.clearSearchBtn.hidden = false;
        render();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      fragment.appendChild(button);
    });
    el.customerList.replaceChildren(fragment);
  }

  function render() {
    const filtered = getFilteredArticles();
    const visible = filtered.slice(0, state.visibleCount);
    el.issueTitle.textContent = getTitle();
    el.resultCount.textContent = filtered.length.toLocaleString("ko-KR");
    renderFilterSummary();
    renderCustomerList();

    if (!filtered.length) {
      el.articleList.replaceChildren();
      el.articleList.hidden = true;
      el.emptyState.hidden = false;
      el.loadMoreBtn.hidden = true;
      return;
    }

    el.articleList.hidden = false;
    el.emptyState.hidden = true;
    const fragment = document.createDocumentFragment();
    visible.forEach((article, index) => fragment.appendChild(createArticleCard(article, index)));
    el.articleList.replaceChildren(fragment);
    el.loadMoreBtn.hidden = visible.length >= filtered.length;
  }

  function resetFilters() {
    state.selectedTag = ALL;
    state.selectedTeam = ALL;
    state.selectedCell = ALL;
    state.selectedRep = ALL;
    state.searchText = "";
    state.majorOnly = false;
    state.visibleCount = PAGE_SIZE;
    el.searchInput.value = "";
    el.clearSearchBtn.hidden = true;
    el.majorOnly.checked = false;
    buildSelectFilters();
    buildTagFilters();
    render();
  }

  function openFilterPanel() {
    el.filterOverlay.hidden = false;
    el.filterPanel.classList.add("is-open");
    el.filterPanel.setAttribute("aria-hidden", "false");
    document.body.classList.add("has-dialog");
    window.setTimeout(() => el.teamSelect.focus(), 280);
  }

  function closeFilterPanel() {
    el.filterPanel.classList.remove("is-open");
    el.filterPanel.setAttribute("aria-hidden", "true");
    document.body.classList.remove("has-dialog");
    window.setTimeout(() => {
      el.filterOverlay.hidden = true;
    }, 280);
  }

  el.teamSelect.addEventListener("change", (event) => {
    state.selectedTeam = event.target.value;
    state.selectedCell = ALL;
    state.selectedRep = ALL;
    state.visibleCount = PAGE_SIZE;
    buildSelectFilters();
    render();
  });

  el.cellSelect.addEventListener("change", (event) => {
    state.selectedCell = event.target.value;
    state.selectedRep = ALL;
    state.visibleCount = PAGE_SIZE;
    buildSelectFilters();
    render();
  });

  el.repSelect.addEventListener("change", (event) => {
    state.selectedRep = event.target.value;
    state.visibleCount = PAGE_SIZE;
    render();
  });

  el.majorOnly.addEventListener("change", (event) => {
    state.majorOnly = event.target.checked;
    state.visibleCount = PAGE_SIZE;
    render();
  });

  let searchDebounce = null;
  el.searchInput.addEventListener("input", (event) => {
    const value = event.target.value;
    el.clearSearchBtn.hidden = value.length === 0;
    window.clearTimeout(searchDebounce);
    searchDebounce = window.setTimeout(() => {
      state.searchText = value.trim();
      state.visibleCount = PAGE_SIZE;
      render();
    }, 180);
  });

  el.searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") el.clearSearchBtn.click();
  });

  el.clearSearchBtn.addEventListener("click", () => {
    state.searchText = "";
    state.visibleCount = PAGE_SIZE;
    el.searchInput.value = "";
    el.clearSearchBtn.hidden = true;
    render();
    el.searchInput.focus();
  });

  el.loadMoreBtn.addEventListener("click", () => {
    state.visibleCount += PAGE_SIZE;
    render();
  });

  [el.resetFiltersBtn, el.emptyResetBtn].forEach((button) => {
    button.addEventListener("click", resetFilters);
  });

  el.clearCustomerBtn.addEventListener("click", () => {
    state.searchText = "";
    state.visibleCount = PAGE_SIZE;
    el.searchInput.value = "";
    el.clearSearchBtn.hidden = true;
    render();
  });

  el.openFiltersBtn.addEventListener("click", openFilterPanel);
  el.closeFiltersBtn.addEventListener("click", closeFilterPanel);
  el.applyFiltersBtn.addEventListener("click", closeFilterPanel);
  el.filterOverlay.addEventListener("click", closeFilterPanel);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && el.filterPanel.classList.contains("is-open")) closeFilterPanel();
  });

  const SCROLL_TOP_THRESHOLD = 500;
  window.addEventListener("scroll", () => {
    el.scrollTopBtn.hidden = window.scrollY <= SCROLL_TOP_THRESHOLD;
  }, { passive: true });

  el.scrollTopBtn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  fetch("data/news.json", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error("news.json 로드 실패");
      return response.json();
    })
    .then((data) => {
      state.articles = Array.isArray(data.articles) ? data.articles : [];
      state.tagOrder = uniqueSorted(state.articles.map((article) => article.tag_label));
      el.updatedAt.textContent = data.generated_at
        ? formatUpdateDate(data.generated_at)
        : "업데이트 일시 확인 불가";
      buildSelectFilters();
      buildTagFilters();
      render();
    })
    .catch((error) => {
      el.updateStatus.classList.add("is-old");
      el.updatedAt.textContent = "업데이트 확인 실패";
      el.articleList.innerHTML = "";
      el.emptyState.hidden = false;
      el.emptyState.querySelector("h3").textContent = "뉴스 데이터를 불러오지 못했습니다.";
      el.emptyState.querySelector("p").textContent = `잠시 후 다시 시도해 주세요. (${error.message})`;
    });
})();
