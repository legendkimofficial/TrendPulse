/* Trend Pulse GKG dashboard: reads the precomputed snapshot cache through /api/gkg/*. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const KIND_KO = { P: "인물", O: "기관", L: "장소" };
  const TAB_KO = { major: "7일 주요 이슈", new: "72시간 신규 이슈", trend: "30일 반짝·꾸준" };
  const STATUS_TEXT = {
    warming_up: "판별 준비 중: 스트림 시작 후 충분한 시간이 지나지 않았습니다 (신규 7일, 반짝·꾸준 21일).",
    insufficient: "근거 부족: 이 판별에 쓰는 창에서 빠진 15분 슬롯이 5%를 넘어 보류했습니다.",
  };
  const state = { info: null, idx: 0, tab: "major", kind: "all", snap: null, reqId: 0 };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
  const fmtT = (t) => new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const num = (x, d = 1) => (x === null || x === undefined ? "–" : Number(x).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const pct = (x) => (x === null || x === undefined ? "–" : (x * 100).toFixed(1) + "%");

  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }

  // ------------------------------------------------------------------ theme
  function applyThemeIcon() {
    const dark = document.documentElement.dataset.theme === "dark" ||
      (document.documentElement.dataset.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    $("themeBtn").innerHTML = `<svg><use href="#${dark ? "i-sun" : "i-moon"}"/></svg>`;
  }
  $("themeBtn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark" ||
      (document.documentElement.dataset.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("tp-theme", next); } catch (e) {}
    applyThemeIcon();
  });
  applyThemeIcon();

  // ------------------------------------------------------------------ controls
  function setSlider(i) {
    const n = state.info.snapshots.length;
    state.idx = Math.max(0, Math.min(n - 1, i));
    $("slider").value = state.idx;
    $("sliderLabel").textContent = fmtT(state.info.snapshots[state.idx]);
    $("slider").setAttribute("aria-valuetext", fmtT(state.info.snapshots[state.idx]));
  }
  let timer = null;
  $("slider").addEventListener("input", () => {
    setSlider(Number($("slider").value));
    clearTimeout(timer);
    timer = setTimeout(load, 120);
  });
  $("prevBtn").addEventListener("click", () => { setSlider(state.idx - 1); load(); });
  $("nextBtn").addEventListener("click", () => { setSlider(state.idx + 1); load(); });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea")) return;
    if (e.key === "ArrowLeft") { setSlider(state.idx - 1); load(); }
    if (e.key === "ArrowRight") { setSlider(state.idx + 1); load(); }
    if (e.key === "Escape") closeDrawer();
  });
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (!b) return;
    state.tab = b.dataset.tab;
    for (const x of $("tabs").querySelectorAll("button")) x.setAttribute("aria-selected", String(x === b));
    load();
  });
  $("kinds").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-kind]");
    if (!b) return;
    state.kind = b.dataset.kind;
    for (const x of $("kinds").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
    load();
  });

  // ------------------------------------------------------------------ table
  function columns(tab) {
    const base = [
      ["#", "rank"], ["키", "l"], ["점수", ""], ["보정 보도율/h", ""],
    ];
    if (tab === "new") base.push(["lift", ""], ["72h 기사", ""]);
    if (tab !== "new") base.push(["ratio", ""]);
    if (tab === "trend") base.splice(3, 1, ["168h 보도율/h", ""]);
    base.push(["근거 기사", ""], ["언론사(72h)", ""], ["분류", "l"], ["빈 구간", ""], ["θ", ""]);
    return base;
  }

  function renderTable(s) {
    $("tableTitle").textContent = TAB_KO[s.tab];
    $("tableHint").textContent = `${s.window} 창 · ${s.kind === "all" ? "전체" : KIND_KO[s.kind]} · ${s.variant === "exact" ? "정확" : s.variant}`;
    $("thead").innerHTML = columns(s.tab).map(([h, c]) => `<th class="${c}" scope="col">${esc(h)}</th>`).join("");
    const st = $("status");
    if (s.status !== "ok") {
      st.hidden = false;
      st.className = "g-status" + (s.status === "warming_up" ? " info" : "");
      const extra = s.tab === "new" ? "" : " 아래 순위와 비율은 참고용으로 표시합니다.";
      st.textContent = (STATUS_TEXT[s.status] || s.status) + (s.elapsed_days != null ? ` (경과 ${num(s.elapsed_days, 1)}일)` : "") + extra;
    } else st.hidden = true;
    const rows = s.rows || [];
    if (!rows.length) {
      $("tbody").innerHTML = `<tr><td colspan="${columns(s.tab).length}" class="l g-muted">${s.status === "ok" ? "조건을 통과한 키가 없습니다." : "보류 중이라 목록이 없습니다."}</td></tr>`;
    } else {
      const GROUP_KO = {
        rising: "최근 급증 · ratio 내림차순", cooling: "식는 중 · ratio 오름차순", steady: "꾸준 · 168h 점수순",
      };
      let lastGroup = null, gi = 0;
      $("tbody").innerHTML = rows.map((r, i) => {
        let head = "";
        if (s.tab === "trend" && r.group !== lastGroup) {
          lastGroup = r.group; gi = 0;
          const n = rows.filter((x) => x.group === r.group).length;
          head = `<tr class="g-group"><th colspan="${columns(s.tab).length}" scope="colgroup">${esc(GROUP_KO[r.group] || r.group)} <span class="g-muted">(${n})</span></th></tr>`;
        }
        const rank = s.tab === "trend" ? ++gi : i + 1;
        const cells = [
          `<td class="rank">${rank}</td>`,
          `<td class="name"><span class="g-kind ${esc(r.kind)}">${esc(KIND_KO[r.kind])}</span>${esc(r.name)}</td>`,
          `<td>${num(r.score, 1)}</td>`, `<td>${num(r.rate, 1)}</td>`,
        ];
        if (s.tab === "new") cells.push(`<td>${num(r.lift, 2)}</td>`, `<td>${num(r.articles_72h, 0)}</td>`);
        else cells.push(`<td>${num(r.ratio, 2)}</td>`);
        cells.push(
          `<td>${num(r.evidence, 0)}</td>`,
          `<td>${r.outlets == null ? '<span class="g-muted">–</span>' : r.outlets >= 8 ? "8+" : r.outlets}</td>`,
          `<td class="l"><span class="g-label ${esc(r.label)}">${esc(r.label_ko)}</span></td>`,
          `<td>${pct(r.gap)}</td>`,
          `<td>${r.approx ? '<span class="g-approx" title="θ 삭제 근사: 과소추정 가능">근사</span>' : '<span class="g-muted" title="삭제 없는 정확 기준">정확</span>'}</td>`);
        return head + `<tr tabindex="0" data-key="${esc(r.key)}">${cells.join("")}</tr>`;
      }).join("");
    }
    $("tableFoot").textContent = s.tab === "new"
      ? `lift = (72h 보도율+1)/(168h 보도율+1). 후보는 72h 상위 2,000개, 조건: 최근 72시간 기사 ≥ 20, 언론사 ≥ 5 (조건 통과 ${num(s.qualified, 0)}개). 언론사 수는 최대 8곳까지 근사.`
      : s.tab === "major"
        ? "168h(7일) 감쇠 점수 순. ratio = (168h 보도율+1)/(720h 보도율+1): ≥1.5 최근 급증, 1/1.5~1.5 꾸준, <1/1.5 식는 중."
        : `후보는 168h 상위 ${num(s.candidates, 0)}개. ratio = (168h 보도율+1)/(720h 보도율+1)로 나눈 세 칸: 최근 급증(≥1.5, ratio 큰 순) · 식는 중(<1/1.5, ratio 작은 순) · 꾸준(168h 점수순), 칸마다 10개. 키 종류 필터를 먼저 적용합니다.`;
  }

  function renderGaps(s) {
    $("gaps").innerHTML = "빈 구간(빠진 15분 슬롯): " + Object.entries(s.gaps || {}).map(([w, g]) =>
      `<span class="g-gap${g.insufficient ? " bad" : ""}" title="${g.expected_slots}개 슬롯 중">${esc(w)} <b>${pct(g.missing_ratio)}</b></span>`).join("");
  }

  function renderSide(s) {
    $("countries").innerHTML = (s.countries || []).map((c) =>
      `<li><span>${esc(c.name)}</span><span>${num(c.rate, 1)}/h</span></li>`).join("") || '<li class="g-muted">없음</li>';
    $("events").innerHTML = (s.events || []).map((e) =>
      `<li><button type="button" data-key="${esc(e.key)}">${esc(e.key)}</button><small>${e.kind === "first" ? "첫 진입" : "재진입"} · ${e.rank}위 · ${fmtT(e.t)}</small></li>`).join("")
      || '<li class="g-muted">이 구간에는 없음</li>';
  }

  async function load() {
    if (!state.info) return;
    const id = ++state.reqId;
    const t = state.info.snapshots[state.idx];
    try {
      const s = await getJSON(`/api/gkg/snapshot?t=${t}&tab=${state.tab}&kind=${state.kind}`);
      if (id !== state.reqId) return;
      state.snap = s;
      $("clock").textContent = fmtT(s.t);
      $("clockMeta").textContent = `기사 ${num(s.articles, 0)}건 처리 시점`;
      renderTable(s); renderGaps(s); renderSide(s);
    } catch (err) {
      $("tbody").innerHTML = `<tr><td class="l">불러오기 실패: ${esc(err.message)}</td></tr>`;
    }
  }

  // ------------------------------------------------------------------ drawer
  function closeDrawer() {
    $("drawer").classList.remove("open");
    $("backdrop").hidden = true;
    setTimeout(() => { $("drawer").hidden = true; }, 250);
  }
  $("dClose").addEventListener("click", closeDrawer);
  $("backdrop").addEventListener("click", closeDrawer);

  async function openKey(key) {
    const t = state.info.snapshots[state.idx];
    $("drawer").hidden = false;
    $("backdrop").hidden = false;
    requestAnimationFrame(() => $("drawer").classList.add("open"));
    $("dTitle").textContent = key.slice(2);
    $("dKind").textContent = KIND_KO[key[0]] || "";
    $("dBody").innerHTML = '<p class="empty">불러오는 중…</p>';
    try {
      const d = await getJSON(`/api/gkg/key?t=${t}&key=${encodeURIComponent(key)}`);
      const r = d.row || {};
      const tiles = [
        ["보정 보도율", r.rate == null ? "–" : num(r.rate, 1) + "/h"],
        ["감쇠 점수", num(r.score, 1)],
        ["분류", r.label_ko || "–"],
        ["언론사(72h)", r.outlets == null ? "추적 전" : (r.outlets >= 8 ? "8+" : r.outlets)],
        ["근거 기사", num(d.evidence.total, 0)],
        ["θ", r.approx ? "근사" : "정확"],
      ];
      if (r.lift != null) tiles.splice(2, 0, ["lift", num(r.lift, 2)]);
      if (r.ratio != null) tiles.splice(2, 0, ["ratio", num(r.ratio, 2)]);
      const dg = r.dgim;
      const dgimHtml = dg
        ? `<p>최근 <b>${num(dg.window, 0)}</b>건 기사 중 약 <b>${num(dg.estimate, 0)}</b>건 언급 (DGIM, 상대 오차 ≤ ${Math.round(dg.error_bound * 100)}%)${dg.partial ? ` · 추적 시작 후 ${num(dg.observed, 0)}건만 관측` : ""}</p>`
        : '<p class="empty">DGIM은 근거 추적(어느 창이든 Top 200 진입) 이후의 키만 셉니다. 이 키는 아직 추적 전입니다.</p>';
      const ev = d.evidence.articles || [];
      const evHtml = ev.length ? `<ul class="g-evi">${ev.map((a) => {
        const u = safeUrl(a.url);
        const title = esc(a.title || a.url);
        return `<li>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${title} <svg class="h-ic"><use href="#i-external"/></svg></a>` : title}
          <div class="m">#${a.idx} · ${esc(a.domain)} · ${fmtT(a.ts)}</div></li>`;
      }).join("")}</ul>` : '<p class="empty">저장된 근거 기사가 없습니다 (Top 200 진입 이후부터, 키마다 하루 30건까지 저장).</p>';
      $("dBody").innerHTML = `
        <div class="d-grid">${tiles.map(([a, b]) => `<div class="d-tile"><div>${esc(a)}</div><div>${esc(b)}</div></div>`).join("")}</div>
        <h3>DGIM 최근 N건 언급 수</h3>${dgimHtml}
        <h3>근거 기사 <span class="g-muted">(최근 ${ev.length}건 / 저장 ${num(d.evidence.total, 0)}건, ${fmtT(d.t)}까지)</span></h3>${evHtml}
        <p class="foot">근거 기사는 키가 처음 Top 200에 든 시점부터, 키마다 UTC 하루 30건까지 먼저 온 순서로 저장한 표본입니다.</p>
        <p><button class="btn small" type="button" id="dAsk">에이전트에게 묻기</button></p>`;
      $("dAsk").addEventListener("click", () => {
        $("question").value = `${key.slice(2)} 왜 많이 나와?`;
        closeDrawer();
        ask(key);
      });
    } catch (err) {
      $("dBody").innerHTML = `<p class="empty">불러오기 실패: ${esc(err.message)}</p>`;
    }
  }
  $("tbody").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-key]"); if (tr) openKey(tr.dataset.key); });
  $("tbody").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const tr = e.target.closest("tr[data-key]");
    if (tr) { e.preventDefault(); openKey(tr.dataset.key); }
  });
  $("events").addEventListener("click", (e) => { const b = e.target.closest("button[data-key]"); if (b) openKey(b.dataset.key); });

  // ------------------------------------------------------------------ agent
  function renderAnswer(res) {
    const lines = String(res.answer || "").split("\n");
    let html = "", inList = false;
    for (const raw of lines) {
      let line = esc(raw).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[#(\d+)\]/g, '<span class="badge soft">#$1</span>');
      const m = /^\s*-\s+(.*)$/.exec(line);
      if (m) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${m[1]}</li>`; continue; }
      if (inList) { html += "</ul>"; inList = false; }
      html += line ? `<p>${line}</p>` : "";
    }
    if (inList) html += "</ul>";
    const warn = res.warning ? `<div class="warn">${esc(res.warning)}</div>` : "";
    $("answer").innerHTML = warn + html + `<div class="meta">${esc(res.mode)} · ${esc(res.as_of || "")}${res.key ? " · " + esc(res.key) : ""}</div>`;
  }
  async function ask(key) {
    const q = $("question").value.trim();
    if (!q) { $("question").focus(); return; }
    $("askBtn").disabled = true;
    $("answer").innerHTML = '<p class="empty">답변을 만드는 중…</p>';
    try {
      const res = await getJSON("/api/gkg/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, t: state.info.snapshots[state.idx], key: key || null }),
      });
      renderAnswer(res);
    } catch (err) {
      $("answer").innerHTML = `<div class="warn">질문 실패: ${esc(err.message)}</div>`;
    } finally {
      $("askBtn").disabled = false;
    }
  }
  $("askForm").addEventListener("submit", (e) => { e.preventDefault(); ask(null); });

  // ------------------------------------------------------------------ start
  (async () => {
    try {
      state.info = await getJSON("/api/gkg/info");
      const n = state.info.snapshots.length;
      $("slider").max = String(n - 1);
      $("range").textContent = `GDELT GKG 2.1 · ${state.info.range.start} ~ ${state.info.range.end} · 기사 ${num(state.info.articles, 0)}건 · 6시간 스냅샷 ${n}개`;
      $("agentMode").textContent = state.info.solar_enabled ? "Upstage Solar" : "오프라인 요약 (UPSTAGE_API_KEY 없음)";
      setSlider(n - 1);
      load();
    } catch (err) {
      $("clockMeta").textContent = "캐시를 읽지 못했습니다: " + err.message;
    }
  })();
})();
