(function () {
  const PAGE_SIZE = 30;

  const state = {
    articles: [],
    tagOrder: [],
    selectedTag: "전체",
    selectedTeam: "전체",
    selectedCell: "전체",
    selectedRep: "전체",
    searchText: "",
    majorOnly: false,
    visibleCount: PAGE_SIZE,
  };

  const el = {
    updatedAt: document.getElementById("updatedAt"),
    tagFilters: document.getElementById("tagFilters"),
    teamSelect: document.getElementById("teamSelect"),
    cellSelect: document.getElementById("cellSelect"),
    repSelect: document.getElementById("repSelect"),
    searchInput: document.getElementById("searchInput"),
    majorOnly: document.getElementById("majorOnly"),
    summaryBar: document.getElementById("summaryBar"),
    articleList: document.getElementById("articleList"),
    emptyState: document.getElementById("emptyState"),
    loadMoreBtn: document.getElementById("loadMoreBtn"),
    resetFiltersBtn: document.getElementById("resetFiltersBtn"),
    scrollTopBtn: document.getElementById("scrollTopBtn"),
  };

  function fmtUpdatedAt(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString("ko-KR", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      });
    } catch (e) {
      return iso;
    }
  }

  function uniqueSorted(values) {
    return [...new Set(values.filter((v) => v))].sort((a, b) => a.localeCompare(b, "ko"));
  }

  // team/cell로 범위를 좁힌 기사 목록을 반환 (드롭다운 옵션 계산용)
  function getScopedArticles({ team, cell } = {}) {
    return state.articles.filter((a) => {
      if (team && team !== "전체" && !(a.team || []).includes(team)) return false;
      if (cell && cell !== "전체" && !(a.cell || []).includes(cell)) return false;
      return true;
    });
  }

  function buildTagChips() {
    const tags = ["전체", ...state.tagOrder];
    el.tagFilters.innerHTML = "";
    tags.forEach((tag) => {
      const btn = document.createElement("button");
      btn.className = "chip" + (tag === state.selectedTag ? " active" : "");
      btn.textContent = tag;
      btn.addEventListener("click", () => {
        state.selectedTag = tag;
        state.visibleCount = PAGE_SIZE;
        buildTagChips();
        render();
      });
      el.tagFilters.appendChild(btn);
    });
  }

  function fillSelect(selectEl, options, selectedValue) {
    const nextValue = options.includes(selectedValue) ? selectedValue : "전체";
    selectEl.innerHTML = "";
    ["전체", ...options].forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt;
      if (opt === nextValue) o.selected = true;
      selectEl.appendChild(o);
    });
    return nextValue;
  }

  // 팀 → 셀 → 담당자 순으로 상위 선택에 맞는 항목만 남도록 옵션을 계산한다.
  function buildSelectFilters() {
    const teams = uniqueSorted(state.articles.flatMap((a) => a.team || []));
    state.selectedTeam = fillSelect(el.teamSelect, teams, state.selectedTeam);

    const teamScoped = getScopedArticles({ team: state.selectedTeam });
    const cells = uniqueSorted(teamScoped.flatMap((a) => a.cell || []));
    state.selectedCell = fillSelect(el.cellSelect, cells, state.selectedCell);

    const cellScoped = getScopedArticles({ team: state.selectedTeam, cell: state.selectedCell });
    const reps = uniqueSorted(cellScoped.flatMap((a) => a.reps || []));
    state.selectedRep = fillSelect(el.repSelect, reps, state.selectedRep);
  }

  function matchesSearch(article, text) {
    if (!text) return true;
    const t = text.toLowerCase();
    if ((article.company || "").toLowerCase().includes(t)) return true;
    if ((article.matched_sub_names || []).some((s) => (s || "").toLowerCase().includes(t))) return true;
    return false;
  }

  function getFiltered() {
    return state.articles.filter((a) => {
      if (state.selectedTag !== "전체" && a.tag_label !== state.selectedTag) return false;
      if (state.selectedTeam !== "전체" && !(a.team || []).includes(state.selectedTeam)) return false;
      if (state.selectedCell !== "전체" && !(a.cell || []).includes(state.selectedCell)) return false;
      if (state.selectedRep !== "전체" && !(a.reps || []).includes(state.selectedRep)) return false;
      if (state.majorOnly && !a.major) return false;
      if (!matchesSearch(a, state.searchText)) return false;
      return true;
    });
  }

  function render() {
    const filtered = getFiltered();
    el.summaryBar.textContent = `총 ${filtered.length}건`;

    const visible = filtered.slice(0, state.visibleCount);

    el.articleList.innerHTML = "";
    if (filtered.length === 0) {
      el.emptyState.hidden = false;
      el.loadMoreBtn.hidden = true;
      return;
    }
    el.emptyState.hidden = true;

    const frag = document.createDocumentFragment();
    visible.forEach((a) => {
      const card = document.createElement("article");
      card.className = "article-card";

      const top = document.createElement("div");
      top.className = "card-top";

      const tagBadge = document.createElement("span");
      tagBadge.className = "tag-badge";
      tagBadge.style.background = a.tag_color || "#7f8c8d";
      tagBadge.textContent = a.tag_label;
      top.appendChild(tagBadge);

      const companyBadge = document.createElement("span");
      companyBadge.className = "company-badge";
      companyBadge.textContent = a.company;
      top.appendChild(companyBadge);

      if (a.major) {
        const majorBadge = document.createElement("span");
        majorBadge.className = "major-badge";
        majorBadge.textContent = "메이저 언론사";
        top.appendChild(majorBadge);
      }

      (a.matched_sub_names || []).forEach((sub) => {
        const subBadge = document.createElement("span");
        subBadge.className = "sub-name-badge";
        subBadge.textContent = `관련: ${sub}`;
        top.appendChild(subBadge);
      });

      card.appendChild(top);

      const titleLink = document.createElement("a");
      titleLink.className = "article-title";
      titleLink.href = a.originallink || a.link;
      titleLink.target = "_blank";
      titleLink.rel = "noopener noreferrer";
      titleLink.textContent = a.title;
      card.appendChild(titleLink);

      if (a.description) {
        const desc = document.createElement("p");
        desc.className = "article-desc";
        desc.textContent = a.description;
        card.appendChild(desc);
      }

      const meta = document.createElement("div");
      meta.className = "card-meta";
      const press = document.createElement("span");
      press.textContent = a.press || "";
      const date = document.createElement("span");
      date.textContent = a.pubDate_display || "";
      meta.appendChild(press);
      meta.appendChild(date);
      card.appendChild(meta);

      if ((a.reps || []).length || (a.team || []).length || (a.cell || []).length) {
        const repInfo = document.createElement("div");
        repInfo.className = "rep-info";
        const parts = [];
        if ((a.team || []).length) parts.push(`팀: ${a.team.join(", ")}`);
        if ((a.cell || []).length) parts.push(`셀: ${a.cell.join(", ")}`);
        if ((a.reps || []).length) parts.push(`담당자: ${a.reps.join(", ")}`);
        repInfo.textContent = parts.join(" · ");
        card.appendChild(repInfo);
      }

      frag.appendChild(card);
    });
    el.articleList.appendChild(frag);

    el.loadMoreBtn.hidden = filtered.length <= visible.length;
  }

  el.majorOnly.addEventListener("change", (e) => {
    state.majorOnly = e.target.checked;
    state.visibleCount = PAGE_SIZE;
    render();
  });

  el.teamSelect.addEventListener("change", (e) => {
    state.selectedTeam = e.target.value;
    // 팀이 바뀌면 셀/담당자는 새 팀 기준으로 다시 좁혀서 보여준다.
    state.selectedCell = "전체";
    state.selectedRep = "전체";
    state.visibleCount = PAGE_SIZE;
    buildSelectFilters();
    render();
  });

  el.cellSelect.addEventListener("change", (e) => {
    state.selectedCell = e.target.value;
    // 셀이 바뀌면 담당자는 새 셀 기준으로 다시 좁혀서 보여준다.
    state.selectedRep = "전체";
    state.visibleCount = PAGE_SIZE;
    buildSelectFilters();
    render();
  });

  el.repSelect.addEventListener("change", (e) => {
    state.selectedRep = e.target.value;
    state.visibleCount = PAGE_SIZE;
    render();
  });

  let searchDebounce = null;
  el.searchInput.addEventListener("input", (e) => {
    clearTimeout(searchDebounce);
    const value = e.target.value;
    searchDebounce = setTimeout(() => {
      state.searchText = value.trim();
      state.visibleCount = PAGE_SIZE;
      render();
    }, 200);
  });

  el.loadMoreBtn.addEventListener("click", () => {
    state.visibleCount += PAGE_SIZE;
    render();
  });

  function resetFilters() {
    state.selectedTag = "전체";
    state.selectedTeam = "전체";
    state.selectedCell = "전체";
    state.selectedRep = "전체";
    state.searchText = "";
    state.majorOnly = false;
    state.visibleCount = PAGE_SIZE;

    el.searchInput.value = "";
    el.majorOnly.checked = false;
    buildTagChips();
    buildSelectFilters();
    render();
  }

  el.resetFiltersBtn.addEventListener("click", resetFilters);

  const SCROLL_TOP_THRESHOLD = 500;
  window.addEventListener("scroll", () => {
    el.scrollTopBtn.hidden = window.scrollY <= SCROLL_TOP_THRESHOLD;
  });

  el.scrollTopBtn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  fetch("data/news.json", { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("news.json 로드 실패");
      return res.json();
    })
    .then((data) => {
      state.articles = data.articles || [];
      state.tagOrder = uniqueSorted(state.articles.map((a) => a.tag_label));
      el.updatedAt.textContent = data.generated_at
        ? `마지막 업데이트: ${fmtUpdatedAt(data.generated_at)} · 최근 ${data.lookback_days || 7}일 · 총 ${data.total_articles || state.articles.length}건`
        : "";
      buildTagChips();
      buildSelectFilters();
      render();
    })
    .catch((err) => {
      el.articleList.innerHTML = `<p class="loading">뉴스 데이터를 불러오지 못했습니다. (${err.message})</p>`;
      el.updatedAt.textContent = "GitHub Actions가 아직 실행되지 않았을 수 있습니다.";
    });
})();
