"use strict";
/* Trend Pulse dashboard (v0.4)
   Plain DOM + hand-drawn SVG charts (no framework). Sections:
     utils · state · timeline · render (clock, KPIs, table, charts, rail) ·
     drawer (keyword detail) · settings · agent chat · wiring/init            */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("ko-KR"));
const fmt1 = (n) => (n == null ? "–" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 1 }));
const compact = (n) => (n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(0)}K` : fmt(n));
const SVG_NS = "http://www.w3.org/2000/svg";
const TICK_MS = 700;

/* ------------------------------------------------------------------ state */
const S = {
  playing: false, busy: false, selected: null, timer: null, last: null,
  speed: 50, view: "table", eventFilter: "all", timeline: null, info: null,
  hoverKw: null, chat: [],
};

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok && !("track_ok" in data)) throw new Error(data.error || res.statusText);
  return data;
}

/* ------------------------------------------------------------------ tooltip & toasts */
const tip = $("tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  const left = Math.min(Math.max(8, x + 14), window.innerWidth - r.width - 8);
  const top = y + 16 + r.height > window.innerHeight ? y - r.height - 10 : y + 16;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
function hideTip() { tip.hidden = true; }

function toast(html, onClick, ms = 4200) {
  const box = $("toasts");
  if (box.children.length >= 3) box.firstElementChild.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = html;
  if (onClick) el.addEventListener("click", () => { onClick(); dismiss(); });
  box.appendChild(el);
  const t = setTimeout(dismiss, ms);
  function dismiss() { clearTimeout(t); el.classList.add("out"); setTimeout(() => el.remove(), 300); }
}

/* ------------------------------------------------------------------ theme */
function cycleTheme() {
  const order = ["system", "light", "dark"];
  const cur = document.documentElement.dataset.theme || "system";
  const next = order[(order.indexOf(cur) + 1) % order.length];
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("tp-theme", next); } catch (e) { /* private mode */ }
  toast(`테마: <b>${{ system: "시스템 설정", light: "라이트", dark: "다크" }[next]}</b>`, null, 1600);
}

/* ------------------------------------------------------------------ timeline */
const TL = { x0: 0, x1: 1, w: 0 };
const dayNum = (iso) => Math.floor(Date.parse(`${iso}T00:00:00Z`) / 86400000);
const isoFromDay = (d) => new Date(d * 86400000).toISOString().slice(0, 10);

function buildTimeline(tl) {
  S.timeline = tl;
  const svg = $("timelineSvg");
  const box = $("timeline");
  const W = box.clientWidth, H = box.clientHeight - 18;
  TL.w = W; TL.x0 = dayNum(tl.first_date); TL.x1 = dayNum(tl.last_date) + 1;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = "";
  const x = (iso) => ((dayNum(iso) - TL.x0) / (TL.x1 - TL.x0)) * W;
  const max = Math.max(...tl.months.map((m) => m.count)) || 1;
  const top = 10;
  let d = "", l = "";
  tl.months.forEach((m, i) => {
    const px = x(`${m.month}-01`), nx = i + 1 < tl.months.length ? x(`${tl.months[i + 1].month}-01`) : W;
    const y = H - 1 - (m.count / max) * (H - top - 2);
    d += `${i ? "L" : `M${px},${H - 1}L`}${px},${y}L${nx},${y}`;
    l += `${i ? "L" : "M"}${px},${y}L${nx},${y}`;
  });
  d += `L${W},${H - 1}Z`;
  svg.insertAdjacentHTML("beforeend", `<rect class="played" id="tlPlayed" x="0" y="0" width="0" height="${H}"/>
    <path class="density" d="${d}"/><path class="density-line" d="${l}"/>
    <line class="baseline" x1="0" x2="${W}" y1="${H - .5}" y2="${H - .5}"/>`);
  tl.events.forEach((ev) => {
    const px = x(ev.date);
    svg.insertAdjacentHTML("beforeend", `<path class="ev${ev.group === "holdout" ? " holdout" : ""}" data-date="${ev.date}" data-label="${esc(ev.label)}" data-exprs="${esc(ev.exprs.join(", "))}" d="M${px.toFixed(1)},2 l4,4 -4,4 -4,-4z"/>`);
  });
  // year axis
  const axis = $("tlAxis");
  axis.innerHTML = "";
  for (let y = Number(tl.first_date.slice(0, 4)) + 1; y <= Number(tl.last_date.slice(0, 4)); y++) {
    axis.insertAdjacentHTML("beforeend", `<span style="left:${x(`${y}-01-01`).toFixed(1)}px">${y}</span>`);
  }
  // interactions
  const tipEl = $("tlTip");
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const day = TL.x0 + (px / rect.width) * (TL.x1 - TL.x0);
    const iso = isoFromDay(Math.floor(day));
    const ev = e.target.closest(".ev");
    tipEl.style.left = `${px}px`;
    if (ev) {
      tipEl.innerHTML = `<b>${esc(ev.dataset.label)}</b> ${ev.dataset.date}<br>${esc(ev.dataset.exprs)}`;
    } else {
      const m = tl.months.find((mm) => mm.month === iso.slice(0, 7));
      tipEl.innerHTML = `${iso}<br>${iso.slice(0, 7)} 기사 ${fmt(m ? m.count : 0)}건`;
    }
    tipEl.hidden = false;
  });
  svg.addEventListener("pointerleave", () => { tipEl.hidden = true; });
  svg.addEventListener("click", (e) => {
    const ev = e.target.closest(".ev");
    if (ev) return seek(ev.dataset.date, ev.dataset.label);
    const rect = svg.getBoundingClientRect();
    const day = TL.x0 + ((e.clientX - rect.left) / rect.width) * (TL.x1 - TL.x0);
    seek(isoFromDay(Math.floor(day)));
  });
}

function updateTimeline(clock) {
  if (!S.timeline) return;
  const toPx = (iso) => Math.max(0, Math.min(1, (dayNum(iso) + 1 - TL.x0) / (TL.x1 - TL.x0))) * TL.w;
  const px = toPx(clock.date), sx = toPx(clock.start_date);
  $("tlCursor").style.left = `${px.toFixed(1)}px`;
  const played = $("tlPlayed");   // the part of the stream replayed since the start date
  if (played) { played.setAttribute("x", sx.toFixed(1)); played.setAttribute("width", Math.max(0, px - sx).toFixed(1)); }
  const start = dayNum(clock.start_date);
  document.querySelectorAll("#timelineSvg .ev").forEach((el) => {
    const d = dayNum(el.dataset.date);
    el.classList.toggle("hot", d >= start && d <= dayNum(clock.date));
  });
}

async function seek(date, label) {
  if (S.busy) return;
  togglePlay(false);
  S.busy = true;
  try {
    const st = await api("/api/seek", { date });
    S.last = null;
    render(st);
    if (S.selected) select(S.selected, false);
    toast(`${st.seek === "stepped" ? "앞으로 이동" : "다시 시작"}: <b>${esc(st.clock.date)}</b>${label ? ` · ${esc(label)}` : ""}`, null, 2500);
  } catch (e) {
    toast(`이동 실패: ${esc(e.message)}`);
  } finally {
    S.busy = false;
  }
}

/* ------------------------------------------------------------------ render */
function render(st) {
  const prev = S.last;
  S.last = st;
  const c = st.clock;
  $("clockDate").textContent = c.date;
  $("clockMeta").textContent = c.decay_unit === "day"
    ? `기사 #${fmt(c.t)} · 재생 ${fmt(c.processed)}건 · 날짜 감쇠 · 반감기 ${c.half_life}일 · 최근 ${fmt(c.effective_window)}일(${fmt(c.articles_in_window)}건)이 가중치의 ${Math.round(c.recent_weight_share * 100)}%`
    : `기사 #${fmt(c.t)} · 재생 ${fmt(c.processed)}건 · 기사 수 감쇠 · 반감기 ≈ ${fmt(c.half_life)}건 · 최근 ${fmt(c.effective_window)}건(${fmt(c.window_span_days)}일)이 가중치의 ${Math.round(c.recent_weight_share * 100)}%`;
  setLive(c.finished ? "finished" : S.playing ? "playing" : "paused");
  updateTimeline(c);
  $("kLabel").textContent = st.config.k;
  $("eventCaption").textContent =
    `첫 진입 = 이번 재생(${c.start_date}~)에서 처음 Top ${st.config.k}에 들어옴 · 재진입 = ${fmt(st.config.new_entry_gap * st.config.snapshot_every)}건 이상 밖에 있다가 복귀`;
  renderKpis(st);
  renderRanking(st, prev);
  renderBurst(st.burst || []);
  renderEvents(st.events);
  renderMetrics(st.metrics, st.config);
  if (S.view === "race") renderRace(st.race);
  if (S.view === "compare") renderCompare(st);
  notifyNewEvents(st, prev);
  if (c.finished && S.playing) togglePlay(false);
}

function setLive(state) {
  const el = $("liveDot");
  el.dataset.state = state;
  $("liveLabel").textContent = { playing: "재생 중", paused: "일시정지", finished: "스트림 끝" }[state];
}

function notifyNewEvents(st, prev) {
  if (!prev) return;
  const seen = new Set(prev.events.map((e) => `${e.t}|${e.keyword}`));
  st.events.filter((e) => e.kind === "first" && !seen.has(`${e.t}|${e.keyword}`)).slice(0, 3).reverse().forEach((e) => {
    toast(`<span class="kind first">첫 진입</span><b>${esc(e.keyword)}</b> ${e.rank}위 · ${e.date}`, () => select(e.keyword));
  });
}

/* ---- KPIs */
function renderKpis(st) {
  const m = st.metrics, cfg = st.config, rows = st.ranking;
  const thin = rows.filter((r) => r.thin).length;
  const errPct = m.score_error_bound ? Math.min(100, (m.max_score_error_topk / m.score_error_bound) * 100) : 0;
  const unit = cfg.decay_unit === "day" ? `${st.clock.effective_window}일` : `${fmt(st.clock.effective_window)}건`;
  $("kpis").innerHTML = `
    <div class="kpi">
      <div class="label"><span>저장 키워드 수</span><span class="delta-chip good">−${m.keyword_reduction_pct}%</span></div>
      <div class="value">${fmt(m.tracked_pruned)}<small>/ 정확 ${fmt(m.tracked_baseline)}</small></div>
      <div class="meter"><div style="width:${m.tracked_baseline ? ((m.tracked_pruned / m.tracked_baseline) * 100).toFixed(1) : 100}%"></div></div>
      <div class="note">θ=${cfg.threshold} 미만 삭제 · 논리 지표(바이트 아님)</div>
    </div>
    <div class="kpi">
      <div class="label"><span>Top ${m.k} 구성원 일치</span>${m.same_order ? '<span class="delta-chip good">순서 동일</span>' : '<span class="delta-chip warn">순서 다름</span>'}</div>
      <div class="value">${m.topk_overlap}<small>/ ${m.k}</small></div>
      <div class="note">정확 계산(모든 키워드 유지)과 비교</div>
    </div>
    <div class="kpi">
      <div class="label"><span>점수 최대 오차</span><span class="delta-chip">상한 ${m.score_error_bound}</span></div>
      <div class="value">${m.max_score_error_topk.toFixed(3)}</div>
      <div class="meter"><div style="width:${errPct.toFixed(0)}%"></div></div>
      <div class="note">θ/(1−θ) 보장 안에서 ${errPct.toFixed(0)}% 사용</div>
    </div>
    <div class="kpi">
      <div class="label"><span>근거 얇은 순위</span>${thin ? `<span class="delta-chip ${thin > rows.length / 2 ? "bad" : "warn"}">${Math.round((thin / rows.length) * 100)}%</span>` : '<span class="delta-chip good">없음</span>'}</div>
      <div class="value">${thin}<small>/ ${rows.length}</small></div>
      <div class="note">최근 ${unit} 기사 ≤ ${cfg.min_evidence}건 (2018년 이후 흔함)</div>
    </div>
    <div class="kpi">
      <div class="label"><span>엔진 처리 속도</span></div>
      <div class="value">${m.docs_per_sec ? compact(m.docs_per_sec) : "–"}<small>기사/s</small></div>
      <div class="note">DGIM 추적 ${fmt(m.dgim_tracked)}/${fmt(m.dgim_capacity)} · 인덱스 ${(m.index_bytes_est / 1048576).toFixed(1)} MB</div>
    </div>`;
}

/* ---- ranking table */
function sparkSvg(vals) {
  if (!vals || vals.length < 2) return '<svg class="spark" viewBox="0 0 84 24"></svg>';
  const w = 84, h = 24, min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const pt = (v, i) => [((i / (vals.length - 1)) * (w - 4) + 2).toFixed(1), (h - 3 - ((v - min) / span) * (h - 6)).toFixed(1)];
  const d = vals.map((v, i) => { const [x, y] = pt(v, i); return `${i ? "L" : "M"}${x},${y}`; }).join("");
  const [lx, ly] = pt(vals[vals.length - 1], vals.length - 1);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}"/><circle cx="${lx}" cy="${ly}" r="2.4"/></svg>`;
}
function deltaCell(r) {
  if (r.prev_rank == null) return '<span class="chip new" title="직전 화면 갱신 대비 Top K에 새로 들어옴">진입</span>';
  if (r.delta > 0) return `<span class="delta up" title="직전 화면보다 ${r.delta}계단 상승">▲${r.delta}</span>`;
  if (r.delta < 0) return `<span class="delta down" title="직전 화면보다 ${-r.delta}계단 하락">▼${-r.delta}</span>`;
  return '<span class="delta same">–</span>';
}
function uncMark(r, n) {
  if (!r.may_swap_above && !r.may_swap_below) return "";
  const parts = [];
  if (r.may_swap_above) parts.push("바로 위");
  if (r.may_swap_below) parts.push(r.rank === n ? "Top K 밖 키워드" : "바로 아래");
  return `<span class="unc" title="메모리를 아낀 근사 때문에 정확 계산에서는 ${parts.join("·")}와 순서가 바뀔 수 있음 (이 키워드 점수 오차 < ${r.error_bound})">≈</span>`;
}
function dgimCell(dg) {
  if (!dg) return '<span class="dg">–</span>';
  const part = dg.partial ? `<span class="partial" title="추적 시작 후 ${fmt(dg.observed)}건만 관측 — 관측 구간 집계 중">관측 ${fmt(dg.observed)}</span>` : "";
  return `<span class="dg"><b>${dg.estimate}</b> / ${dg.exact}${part}</span>`;
}
function kwHtml(w) {
  return esc(w).replace(/ /g, '<span class="phrase">·</span>');
}
function renderRanking(st, prev) {
  const tbody = $("rankBody");
  const rows = st.ranking;
  const maxScore = rows.length ? rows[0].score : 1;
  const maxEv = Math.max(...rows.map((r) => r.evidence), 1);
  const prevSet = prev ? new Set(prev.ranking.map((r) => r.keyword)) : null;
  const before = new Map([...tbody.rows].map((tr) => [tr.dataset.kw, tr.getBoundingClientRect().top]));
  tbody.innerHTML = rows.map((r) => {
    const base = r.baseline_rank == null
      ? '<span class="base out" title="정확 계산에서는 Top K 밖">밖</span>'
      : r.baseline_rank === r.rank
        ? `<span class="base same" title="정확 계산과 같은 순위">${r.baseline_rank}</span>`
        : `<span class="base diff" title="정확 계산 순위 ${r.baseline_rank}위 (점수 ${r.baseline_score})">${r.baseline_rank}</span>`;
    const cls = [r.keyword === S.selected ? "selected" : "", prevSet && !prevSet.has(r.keyword) ? "flash" : "", r.thin ? "thin" : ""].join(" ").trim();
    return `<tr data-kw="${esc(r.keyword)}" class="${cls}" tabindex="0">
      <td class="num rank">${r.rank}${uncMark(r, rows.length)}</td>
      <td>${deltaCell(r)}</td>
      <td class="kw">${kwHtml(r.keyword)}</td>
      <td><div class="scorebar"><div class="track"><div class="bar" style="width:${Math.max(3, (r.score / maxScore) * 100).toFixed(1)}%"></div></div><span>${r.score.toFixed(1)}</span></div></td>
      <td class="hide-sm"><span class="evi${r.thin ? " thin" : ""}" title="최근 구간에 이 키워드가 나온 기사 ${r.evidence}건"><i style="--w:${((r.evidence / maxEv) * 100).toFixed(0)}%"></i>${r.evidence}${r.thin ? '<span class="lbl">얇음</span>' : ""}</span></td>
      <td class="hide-sm">${sparkSvg(r.spark)}</td>
      <td class="num hide-sm">${base}</td>
      <td class="num hide-md">${dgimCell(r.dgim)}</td>
    </tr>`;
  }).join("");
  // FLIP: rows that moved slide from their old position
  if (before.size && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    for (const tr of tbody.rows) {
      const old = before.get(tr.dataset.kw);
      if (old == null) continue;
      const dy = old - tr.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) continue;
      tr.style.transition = "none";
      tr.style.transform = `translateY(${dy}px)`;
      requestAnimationFrame(() => {
        tr.style.transition = "transform .4s cubic-bezier(.2,.8,.2,1)";
        tr.style.transform = "";
      });
    }
  }
  const thin = rows.filter((r) => r.thin).length;
  $("rankFoot").innerHTML = `행을 누르면 화제 요약과 근거 기사가 열립니다. <b>근거</b> = 최근 구간 기사 수${thin ? ` (흐린 ${thin}행은 ≤ ${st.config.min_evidence}건)` : ""} ·
    <b>정확</b> = 모든 키워드를 유지한 계산의 순위(다르면 주황) · <b>≈</b> = 근사 때문에 순서가 바뀔 수 있는 순위.`;
}

/* ---- rank race (bump chart) */
const RACE_SNAPSHOTS = 36;   // snapshots shown in the rank-race chart (server keeps 60)
function renderRace(raceFull) {
  const wrap = $("raceChart");
  const W = wrap.clientWidth || 700;
  // Keep the most recent snapshots only: 10 lines over 60 points is a tangle.
  const cut = Math.max(0, raceFull.t.length - RACE_SNAPSHOTS);
  const race = { ...raceFull, t: raceFull.t.slice(cut), dates: raceFull.dates.slice(cut), series: {} };
  for (const [w, s] of Object.entries(raceFull.series)) { const ss = s.slice(cut); if (ss.some((r) => r != null)) race.series[w] = ss; }
  const k = race.k, n = race.t.length;
  const words = Object.keys(race.series);
  if (n < 2 || !words.length) { wrap.innerHTML = '<p class="empty">스냅샷이 2개 이상 쌓이면 순위 흐름을 그립니다. 조금 더 재생하세요.</p>'; return; }
  const L = 34, R = 150, T = 14, B = 26, rowH = 30;
  const H = T + k * rowH + B;
  const x = (i) => L + (i / (n - 1)) * (W - L - R);
  const y = (r) => T + (r - 1) * rowH + rowH / 2;
  let g = "";
  for (let r = 1; r <= k; r++) g += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(r)}" y2="${y(r)}"/><text class="mono" x="${L - 8}" y="${y(r) + 4}" text-anchor="end">${r}</text>`;
  const ticks = [0, Math.floor((n - 1) / 2), n - 1];
  ticks.forEach((i) => { g += `<text class="mono" x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(race.dates[i])}</text>`; });
  // Identity for the current top 3 (validated categorical slots), gray for the rest,
  // ink for the hovered/selected keyword. Lines break while a keyword is outside the Top k.
  const slot = {};
  words.filter((w) => race.series[w][n - 1] != null).sort((a, b) => race.series[a][n - 1] - race.series[b][n - 1]).slice(0, 3).forEach((w, i) => { slot[w] = `s${i + 1}`; });
  let lines = "", labels = "";
  words.forEach((w) => {
    const s = race.series[w];
    let d = "", pen = false, px = 0, py = 0;
    for (let i = 0; i < n; i++) {
      const r = s[i];
      if (r == null) { pen = false; continue; }
      const cx = x(i), cy = y(r);
      if (!pen) { d += `M${cx.toFixed(1)},${cy.toFixed(1)}`; pen = true; }
      else { const mx = ((px + cx) / 2).toFixed(1); d += `C${mx},${py.toFixed(1)} ${mx},${cy.toFixed(1)} ${cx.toFixed(1)},${cy.toFixed(1)}`; }
      px = cx; py = cy;
    }
    const cls = `${slot[w] || ""}${w === S.selected ? " hot" : ""}`;
    lines += `<path class="series ${cls}" data-kw="${esc(w)}" d="${d}"/><path class="hit" data-kw="${esc(w)}" d="${d}"/>`;
    if (s[n - 1] != null) {
      labels += `<circle class="dot ${cls}" data-kw="${esc(w)}" cx="${x(n - 1)}" cy="${y(s[n - 1])}" r="4.5"/>
        <text class="lbl${w === S.selected ? " hot" : ""}" data-kw="${esc(w)}" x="${W - R + 12}" y="${y(s[n - 1]) + 4}">${esc(w)}</text>`;
    }
  });
  wrap.innerHTML = `<svg class="chart${S.selected ? " dim" : ""}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="최근 ${n}개 스냅샷의 Top ${k} 순위 변화">
      ${g}${lines}<line class="crosshair" id="raceCross" x1="0" x2="0" y1="${T}" y2="${y(k) + 10}" visibility="hidden"/>${labels}</svg>
    <div class="legend"><span><i class="s1"></i>현재 1위</span><span><i class="s2"></i>2위</span><span><i class="s3"></i>3위</span><span><i></i>그 밖의 Top ${k}</span><span><i class="ink"></i>선택·가리킨 키워드</span><span class="t-hint">끊긴 구간 = Top ${k} 밖 · 스냅샷 ${n}개</span></div>`;
  const svg = wrap.querySelector("svg");
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1))));
    const cross = $("raceCross");
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    const at = words.filter((w) => race.series[w][i] != null).sort((a, b) => race.series[a][i] - race.series[b][i]);
    const hot = S.hoverKw || S.selected;
    showTip(`<b>${esc(race.dates[i])}</b> <span class="tt-mute">기사 #${fmt(race.t[i])}</span><br>` +
      at.map((w) => `<div class="tt-row"><span>${w === hot ? "<i></i>" : ""}${race.series[w][i]}. ${esc(w)}</span></div>`).join(""), e.clientX, e.clientY);
  });
  svg.addEventListener("pointerleave", () => { $("raceCross").setAttribute("visibility", "hidden"); hideTip(); });
  svg.addEventListener("pointerover", (e) => { const el = e.target.closest("[data-kw]"); if (el) highlight(svg, el.dataset.kw); });
  svg.addEventListener("pointerout", (e) => { if (!svg.contains(e.relatedTarget) || !e.relatedTarget.closest("[data-kw]")) highlight(svg, null); });
  svg.addEventListener("click", (e) => { const el = e.target.closest("[data-kw]"); if (el) select(el.dataset.kw); });
}

/* Hover emphasis without re-rendering: one keyword in the accent, the rest recede.
   Falls back to the selected keyword when the pointer leaves. */
function highlight(svg, kw) {
  S.hoverKw = kw;
  const hot = kw || S.selected;
  svg.classList.toggle("dim", !!hot);
  svg.querySelectorAll("[data-kw]").forEach((el) => el.classList.toggle("hot", el.dataset.kw === hot));
}

/* ---- approximate vs exact (slope chart) */
function renderCompare(st) {
  const wrap = $("compareChart");
  const W = wrap.clientWidth || 700;
  const left = st.ranking, right = st.baseline_ranking || [];
  const k = Math.max(left.length, right.length);
  if (!k) { wrap.innerHTML = '<p class="empty">순위가 없습니다.</p>'; return; }
  const rowH = 27, T = 34, H = T + k * rowH + 8;
  const colW = Math.min(170, Math.max(120, (W - 120) / 3));
  const xL = colW + 10, xR = W - colW - 10;
  const y = (r) => T + (r - 1) * rowH + rowH / 2;
  const rightRank = new Map(right.map((r) => [r.keyword, r]));
  const leftRank = new Map(left.map((r) => [r.keyword, r]));
  let out = `<text class="head" x="${xL - 10}" y="14" text-anchor="end">근사 · θ 제거 (화면)</text><text class="head" x="${xR + 10}" y="14">정확 · 모두 유지</text>`;
  out += `<line class="axis-line" x1="${xL}" x2="${xL}" y1="${T - 6}" y2="${H - 4}"/><line class="axis-line" x1="${xR}" x2="${xR}" y1="${T - 6}" y2="${H - 4}"/>`;
  const diffs = [];
  left.forEach((r) => {
    const b = rightRank.get(r.keyword);
    const hot = r.keyword === S.selected || r.keyword === S.hoverKw;
    const diff = !b || b.rank !== r.rank;
    if (diff) diffs.push(r.keyword);
    const y1 = y(r.rank);
    const path = b ? `M${xL},${y1}C${(xL + xR) / 2},${y1} ${(xL + xR) / 2},${y(b.rank)} ${xR},${y(b.rank)}` : `M${xL},${y1}L${(xL + xR) / 2 - 10},${y1}`;
    out += `<path class="slope${diff ? " diff" : ""}${hot ? " hot" : ""}${b ? "" : " only"}" data-kw="${esc(r.keyword)}" d="${path}"/><path class="slope-hit" data-kw="${esc(r.keyword)}" d="${path}"/>`;
    out += `<text class="col-rank" x="18" y="${y1 + 4}" text-anchor="end">${r.rank}</text>
      <text class="col-label${diff ? " diff" : ""}${hot ? " hot" : ""}" data-kw="${esc(r.keyword)}" x="${xL - 10}" y="${y1 + 4}" text-anchor="end">${esc(r.keyword)}</text>
      <circle class="dot${hot ? " hot" : ""}" data-kw="${esc(r.keyword)}" cx="${xL}" cy="${y1}" r="4"/>`;
  });
  right.forEach((b) => {
    const l = leftRank.get(b.keyword);
    const hot = b.keyword === S.selected || b.keyword === S.hoverKw;
    const diff = !l || l.rank !== b.rank;
    const y2 = y(b.rank);
    if (!l) out += `<path class="slope diff only${hot ? " hot" : ""}" data-kw="${esc(b.keyword)}" d="M${xR},${y2}L${(xL + xR) / 2 + 10},${y2}"/>`;
    out += `<text class="col-rank" x="${W - 2}" y="${y2 + 4}" text-anchor="end">${b.rank}</text>
      <text class="col-label${diff ? " diff" : ""}${hot ? " hot" : ""}" data-kw="${esc(b.keyword)}" x="${xR + 10}" y="${y2 + 4}">${esc(b.keyword)}</text>
      <circle class="dot${hot ? " hot" : ""}" data-kw="${esc(b.keyword)}" cx="${xR}" cy="${y2}" r="4"/>`;
  });
  const m = st.metrics;
  wrap.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="근사 순위와 정확 순위 비교">${out}</svg>
    <div class="legend"><span><i></i>같은 순위</span><span><i class="org"></i>순위 다름</span><span><i class="org dash"></i>한쪽에만 있음</span>
    <span class="t-hint">구성원 ${m.topk_overlap}/${m.k} 일치 · 순서 ${m.same_order ? "동일" : `다른 키워드 ${diffs.length}개`} · 점수 최대 오차 ${m.max_score_error_topk}</span></div>`;
  const svg = wrap.querySelector("svg");
  svg.addEventListener("pointerover", (e) => {
    const el = e.target.closest("[data-kw]");
    if (!el) return;
    const w = el.dataset.kw;
    highlight(svg, w);
    const l = leftRank.get(w), b = rightRank.get(w);
    showTip(`<b>${esc(w)}</b><br>
      <div class="tt-row"><span>근사 순위 · 점수</span><span>${l ? `${l.rank}위 · ${l.score}` : "Top K 밖"}</span></div>
      <div class="tt-row"><span>정확 순위 · 점수</span><span>${b ? `${b.rank}위 · ${b.score}` : "Top K 밖"}</span></div>
      ${l ? `<div class="tt-row tt-mute"><span>이 키워드 오차 상한</span><span>&lt; ${l.error_bound}</span></div><div class="tt-row tt-mute"><span>최근 구간 기사</span><span>${l.evidence}건</span></div>` : ""}`, e.clientX, e.clientY);
  });
  svg.addEventListener("pointermove", (e) => { if (!tip.hidden) { tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${e.clientY + 16}px`; } });
  svg.addEventListener("pointerout", (e) => { if (!svg.contains(e.relatedTarget) || !e.relatedTarget.closest("[data-kw]")) { hideTip(); highlight(svg, null); } });
  svg.addEventListener("click", (e) => { const el = e.target.closest("[data-kw]"); if (el) select(el.dataset.kw); });
}

/* ---- rail: burst, events, metrics */
function renderBurst(rows) {
  if (!rows.length) { $("burst").innerHTML = '<li class="empty">조건(최소 점수)을 넘는 급상승 키워드가 없습니다. 날짜 감쇠에서는 기사가 적은 날 비기도 합니다.</li>'; return; }
  const max = rows[0].excess || 1;
  $("burst").innerHTML = rows.map((b) => `<li class="burst-row" data-kw="${esc(b.keyword)}">
    <span class="ev-kw"><span>${esc(b.keyword)}</span><span class="lift" title="짧은 창 점수 ÷ 평소 비율로 예상한 점수">×${b.lift}</span>
      ${b.rank ? `<span class="rank-tag">Top ${b.rank}</span>` : '<span class="rank-tag out" title="아직 Top K에 없음 — 급상승이 먼저 잡았다">Top K 밖</span>'}</span>
    <span class="ev-meta" title="초과분 = 짧은 창 점수 − 예상 점수 · 최근 구간 기사 ${b.evidence}건">+${b.excess} · ${b.evidence}건</span>
    <span class="burst-bar"><div style="--w:${Math.max(4, (b.excess / max) * 100).toFixed(0)}%"></div></span></li>`).join("");
}
function renderEvents(events) {
  $("eventCount").textContent = events.length;
  const rows = S.eventFilter === "all" ? events : events.filter((e) => e.kind === S.eventFilter);
  if (!rows.length) { $("events").innerHTML = `<li class="empty">${events.length ? "해당 종류의 진입이 아직 없습니다." : "재생을 시작하면 Top K에 새로 들어온 키워드가 표시됩니다."}</li>`; return; }
  $("events").innerHTML = rows.map((e) => `<li data-kw="${esc(e.keyword)}">
    <span class="ev-date">${esc(e.date)}</span>
    <span class="ev-kw"><span>${esc(e.keyword)}</span><span class="kind ${e.kind}">${e.kind === "first" ? "첫 진입" : "재진입"}</span></span>
    <span class="ev-meta">${e.rank}위 · ${e.score}</span></li>`).join("");
}
function renderMetrics(m, cfg) {
  const keptPct = m.tracked_baseline ? (m.tracked_pruned / m.tracked_baseline) * 100 : 100;
  const dgimPct = m.exact_window_bits ? (m.dgim_bits / m.exact_window_bits) * 100 : null;
  $("metrics").innerHTML = `
    <div class="metric wide">
      <div class="label">저장 키워드 수 (논리 지표 · 실제 바이트 아님)</div>
      <div class="value">${fmt(m.tracked_pruned)} <small>/ 정확 ${fmt(m.tracked_baseline)} · ${m.keyword_reduction_pct}% 감소</small></div>
      <div class="membar" title="정확 방식 대비 보관 비율"><div style="width:${keptPct.toFixed(1)}%"></div></div>
      <div class="note">누적 삭제 ${fmt(m.dropped_total)}회 · 제거용 힙 항목 ${fmt(m.heap_entries)}개(정확 방식엔 없음) · 최고 ${fmt(m.peak_pruned)}개</div>
    </div>
    <div class="metric">
      <div class="label">DGIM 논리 bit (압축 저장 가정)</div>
      <div class="value">${fmt(m.dgim_bits)}<small> bit</small></div>
      <div class="note">정확 집계 ${fmt(m.exact_window_bits)} bit${dgimPct != null ? ` (${dgimPct.toFixed(0)}%)` : ""}</div>
    </div>
    <div class="metric">
      <div class="label">DGIM 추적</div>
      <div class="value">${fmt(m.dgim_tracked)}<small>/${fmt(m.dgim_capacity)}</small></div>
      <div class="note">고정 ${m.dgim_pinned} · 교체 ${fmt(m.dgim_evictions)}회</div>
    </div>
    <div class="metric wide">
      <div class="label">관련 기사 조회용 인덱스 (집계 메모리와 별도)</div>
      <div class="value">${fmt(m.index_postings)} <small>항목 · 약 ${(m.index_bytes_est / 1048576).toFixed(1)} MB(추정) · 급상승 짧은 창 ${fmt(m.short_window_keywords)}개</small></div>
    </div>`;
}

/* ------------------------------------------------------------------ drawer / detail */
function openDrawer(open) {
  $("drawer").classList.toggle("open", open);
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("drawerBackdrop").hidden = !open || window.innerWidth > 1180;
}
function trendSvg(history) {
  const pts = history.filter((h) => h.score != null);
  if (pts.length < 2) return '<p class="empty">추이를 그리기에 스냅샷이 부족합니다. 조금 더 재생하세요.</p>';
  const W = 540, H = 170, L = 36, B = 22, T = 10, R = 44;
  const max = Math.max(...pts.map((p) => p.score)) * 1.1 || 1;
  const x = (i) => L + (i / (pts.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join("");
  const area = `${line}L${x(pts.length - 1).toFixed(1)},${H - B}L${L},${H - B}Z`;
  const ticks = [0, 0.5, 1].map((f) => { const v = max * f; return `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="mono" x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`; }).join("");
  const last = pts[pts.length - 1];
  return `<div class="chart-wrap" id="trendWrap"><svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="감쇠 점수 추이">
    ${ticks}<path class="area" d="${area}"/><path class="line" d="${line}"/>
    <circle class="end-dot" cx="${x(pts.length - 1)}" cy="${y(last.score)}" r="4"/>
    <text class="mono" x="${x(pts.length - 1) + 8}" y="${y(last.score) + 4}" style="fill:var(--text)">${last.score.toFixed(1)}</text>
    <text class="mono" x="${L}" y="${H - 6}">${esc(pts[0].date)}</text><text class="mono" x="${W - R}" y="${H - 6}" text-anchor="end">${esc(last.date)}</text>
    <line class="crosshair" id="trendCross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>
    <circle class="end-dot" id="trendDot" r="4" visibility="hidden"/></svg></div>`;
}
function bindTrend(history) {
  const wrap = $("trendWrap");
  if (!wrap) return;
  const pts = history.filter((h) => h.score != null);
  const svg = wrap.querySelector("svg");
  const W = 540, H = 170, L = 36, B = 22, T = 10, R = 44;
  const max = Math.max(...pts.map((p) => p.score)) * 1.1 || 1;
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round(((px - L) / (W - L - R)) * (pts.length - 1))));
    const cx = L + (i / (pts.length - 1)) * (W - L - R), cy = T + (1 - pts[i].score / max) * (H - T - B);
    const cross = $("trendCross"), dot = $("trendDot");
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("visibility", "visible");
    showTip(`<b>${pts[i].score.toFixed(2)}</b> <span class="tt-mute">점수</span><br>${esc(pts[i].date)} · 기사 #${fmt(pts[i].t)}${pts[i].rank ? ` · ${pts[i].rank}위` : " · Top 2K 밖"}`, e.clientX, e.clientY);
  });
  svg.addEventListener("pointerleave", () => { $("trendCross").setAttribute("visibility", "hidden"); $("trendDot").setAttribute("visibility", "hidden"); hideTip(); });
}

function renderDetail(d, msg) {
  const fe = d.first_entry;
  const rankTxt = d.rank == null ? "집계에서 삭제됨 (점수 < θ)" : `${d.rank}위${d.in_top_k ? "" : " · Top K 밖"}`;
  const entry = fe ? `<span class="kind ${fe.kind}">${fe.kind === "first" ? "첫 진입" : "재진입"}</span> ${esc(fe.date)} (${fe.rank}위)` : "이번 재생에서 Top K 진입 없음";
  const recent = d.mentions_in_effective_window, prevN = d.mentions_in_previous_window, mx = Math.max(recent, prevN, 1);
  const dg = d.dgim;
  const dgimHtml = dg
    ? `<div class="dgim-box">
        <div class="row">최근 <b>${fmt(dg.window)}</b>개 기사 중 등장 기사 수 <span class="badge soft">추적 시작 ${esc(dg.since_date)}${dg.generation > 1 ? ` · ${dg.generation}번째 추적(관측 초기화)` : ""}${dg.pinned ? " · 고정" : ""}</span></div>
        <div class="big">≈ ${dg.estimate} <small>/ 정확 ${dg.exact}</small>${dg.partial ? ` <span class="partial">관측 ${fmt(dg.observed)}/${fmt(dg.window)}건 · 집계 중</span>` : ""}</div>
        <div class="row">오차 ${dg.abs_error} (${(dg.rel_error * 100).toFixed(1)}%, 상한 ${(dg.bound * 100).toFixed(0)}%) · 버킷 ${dg.buckets}개 · 논리 ${dg.dgim_bits} bit (정확 ${dg.exact_bits} bit)</div>
        ${dg.pinned ? "" : `<div><button class="btn small" data-track="${esc(d.keyword)}"><svg><use href="#i-pin"/></svg>추적 고정</button></div>`}
      </div>`
    : `<div class="dgim-box"><div class="row">DGIM으로 추적하지 않는 키워드입니다. Top K에 들어온 키워드는 자동으로 추적하며, 추적 시작 이전 기사는 세지 않습니다.</div>
        <div><button class="btn small" data-track="${esc(d.keyword)}"><svg><use href="#i-pin"/></svg>지금부터 추적</button></div></div>`;
  const co = d.cooccurring.map(([w, n]) => `<span class="chip click" data-kw="${esc(w)}">${esc(w)} <b>${n}</b></span>`).join("") || '<span class="empty">–</span>';
  const cats = d.categories.map(([c, n]) => `<span class="chip">${esc(c)} <b>${n}</b></span>`).join("");
  const news = d.related.length
    ? d.related.map((a, i) => `<li${i < 3 ? ' class="top"' : ""}><div class="meta">#${a.id} · ${esc(a.date)} · ${esc(a.category)}</div>
        <a href="${esc(a.link)}" target="_blank" rel="noopener noreferrer">${esc(a.headline)}<svg><use href="#i-external"/></svg></a>
        ${a.description ? `<div class="desc">${esc(a.description)}</div>` : ""}</li>`).join("")
    : '<li class="empty">아직 스트림에 관련 기사가 없습니다.</li>';
  $("detailBody").innerHTML = `
    <div class="d-title"><h2>${esc(d.keyword)}</h2><span class="kw-rank">${rankTxt}</span></div>
    <div class="d-entry">${entry}${d.first_seen ? `<span class="t-hint">· 첫 관측 ${esc(d.first_seen.date)}</span>` : ""}<span class="t-hint">· 누적 ${fmt(d.total_mentions_so_far)}건</span></div>
    <div class="d-tiles">
      <div class="d-tile"><div class="label">감쇠 점수 (근사 / 정확)</div><div class="value">${d.score.toFixed(2)}<small> / ${d.baseline_score.toFixed(2)}</small></div>
        <div class="cmp-lbl"><span>${d.in_pruned ? `차이 ${(d.baseline_score - d.score).toFixed(3)}` : "근사 집계에서 삭제됨"}</span></div></div>
      <div class="d-tile"><div class="label">${esc(d.window_label)} 기사 수 · 그 이전 구간</div><div class="value">${recent}<small> / ${prevN}건</small></div>
        <div class="cmp"><div style="height:${Math.max(6, (prevN / mx) * 100).toFixed(0)}%" title="이전 구간 ${prevN}건"></div><div class="now" style="height:${Math.max(6, (recent / mx) * 100).toFixed(0)}%" title="최근 구간 ${recent}건"></div></div>
        <div class="cmp-lbl"><span>${esc(d.previous_window_label)}</span><span>${esc(d.window_label)}</span></div></div>
    </div>
    <div class="d-actions">
      <button class="btn small" data-ask="${esc(d.keyword)}"><svg><use href="#i-bot"/></svg>에이전트에게 "왜 화제야?" 묻기</button>
      <a class="btn small ghost" href="?kw=${encodeURIComponent(d.keyword)}&start=${encodeURIComponent(S.last ? S.last.clock.start_date : "")}" id="kwLink" title="이 키워드 링크"><svg><use href="#i-link"/></svg>링크</a>
    </div>
    <div class="d-sec">감쇠 점수 추이 <span class="t-hint">스냅샷 ${d.history.length}개</span></div>
    ${trendSvg(d.history)}
    <div class="d-sec">근거 기사 <span class="t-hint">최신순 · 현재 시점까지 · 위 3건이 대표 기사</span></div>
    <ul class="news">${news}</ul>
    <div class="d-sec">함께 등장한 단어 <span class="t-hint">최근 관련 기사 60건</span></div>
    <div class="chips">${co}</div>
    <div class="chips" style="margin-top:8px">${cats}</div>
    <div class="d-sec">DGIM 근사 집계 <span class="t-hint">보조 기능</span></div>
    ${dgimHtml}
    ${msg ? `<div class="msg">${esc(msg)}</div>` : ""}`;
  bindTrend(d.history);
  $("kwLink").addEventListener("click", (e) => { e.preventDefault(); copyLink(d.keyword); });
}

async function select(word, open = true) {
  if (!word) return;
  S.selected = word.toLowerCase();
  document.querySelectorAll("#rankBody tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.kw === S.selected));
  if (open) openDrawer(true);
  try {
    renderDetail(await api(`/api/keyword/${encodeURIComponent(S.selected)}`));
  } catch (e) {
    $("detailBody").innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
  if (S.view === "race" && S.last) renderRace(S.last.race);
  if (S.view === "compare" && S.last) renderCompare(S.last);
}

/* ------------------------------------------------------------------ playback */
async function tick() {
  if (S.busy) return;
  S.busy = true;
  try {
    const st = await api("/api/step", { n: S.speed });
    render(st);
    if (S.selected && $("drawer").classList.contains("open")) select(S.selected, false);
  } catch (e) {
    console.error(e);
    toast(`오류: ${esc(e.message)}`);
    togglePlay(false);
  } finally {
    S.busy = false;
  }
  if (S.playing) S.timer = setTimeout(tick, TICK_MS);
}
function togglePlay(force) {
  S.playing = force ?? !S.playing;
  const b = $("btnPlay");
  b.setAttribute("aria-pressed", String(S.playing));
  b.querySelector("span").textContent = S.playing ? "일시정지" : "재생";
  setLive(S.last && S.last.clock.finished ? "finished" : S.playing ? "playing" : "paused");
  clearTimeout(S.timer);
  if (S.playing) tick();
}
function startDate() {
  const p = $("preset").value;
  return p === "custom" ? $("customDate").value : p;
}
function setPresetFromDate(date) {
  const opt = [...$("preset").options].find((o) => o.value === date);
  $("preset").value = opt ? date : "custom";
  $("customDate").hidden = !!opt;
  if (!opt) $("customDate").value = date;
}

async function reset(extra = {}) {
  togglePlay(false);
  $("btnReset").disabled = true;
  try {
    const st = await api("/api/reset", { start_date: startDate(), ...extra });
    S.last = null;
    render(st);
    syncSettings(st.config);
    if (S.selected) select(S.selected, false);
    return st;
  } catch (e) {
    toast(`설정 오류: ${esc(e.message)}`);
    throw e;
  } finally {
    $("btnReset").disabled = false;
  }
}

async function demo(ask = true) {
  togglePlay(false);
  $("btnDemo").disabled = true;
  try {
    const st = await api("/api/demo", {});
    setPresetFromDate(st.demo.start_date);
    syncSettings(st.config);
    S.last = null;
    render(st);
    await select(st.demo.keyword);
    if (ask) { $("askInput").value = st.demo.question; $("askForm").requestSubmit(); }
  } finally {
    $("btnDemo").disabled = false;
  }
}

/* ------------------------------------------------------------------ settings dialog */
const CFG_FIELDS = {
  c: "cfgC", threshold: "cfgT", k: "cfgK", dgim_n: "cfgN", dgim_r: "cfgR", decay_unit: "cfgUnit",
  half_life_days: "cfgHalf", min_evidence: "cfgEvidence", burst_ratio: "cfgBurstRatio", burst_min: "cfgBurstMin",
};
function syncSettings(cfg) {
  for (const [key, id] of Object.entries(CFG_FIELDS)) $(id).value = cfg[key];
  $("cfgPhrases").checked = cfg.phrases;
  updateHints();
}
function updateHints() {
  const c = Number($("cfgC").value), t = Number($("cfgT").value), r = Number($("cfgR").value);
  $("cfgCHint").textContent = c > 0 && c < 1
    ? `반감기 ≈ ${Math.round(Math.log(0.5) / Math.log(1 - c)).toLocaleString("ko-KR")}건 · 대표 규모 1/c ≈ ${Math.round(1 / c).toLocaleString("ko-KR")}건 (기사 수 기준일 때)`
    : "0 < c < 1";
  $("cfgTHint").textContent = t > 0 && t < 1 ? `점수 < θ 인 키워드 삭제 · 오차 상한 θ/(1−θ) = ${(t / (1 - t)).toFixed(2)}` : "0 < θ < 1";
  $("cfgRHint").textContent = r >= 2 ? `오차 상한 ${(100 / r).toFixed(0)}% · 버킷 수 ≈ r·log₂N` : "r ≥ 2";
  const day = $("cfgUnit").value === "day";
  $("cfgHalf").disabled = !day; $("cfgC").disabled = day;
}
function applyPreset(name) {
  const p = name === "v02"
    ? { cfgUnit: "article", cfgC: 0.001, cfgHalf: 7, cfgT: 0.5, cfgK: 20, cfgPhrases: false, cfgEvidence: 2 }
    : { cfgUnit: "day", cfgC: 0.001, cfgHalf: 7, cfgT: 0.5, cfgK: 20, cfgPhrases: true, cfgEvidence: 2, cfgN: 1000, cfgR: 2, cfgBurstRatio: 7, cfgBurstMin: 3 };
  for (const [id, v] of Object.entries(p)) { if (typeof v === "boolean") $(id).checked = v; else $(id).value = v; }
  updateHints();
}
async function applySettings() {
  const body = {};
  for (const [key, id] of Object.entries(CFG_FIELDS)) body[key] = $(id).value;
  body.phrases = $("cfgPhrases").checked;
  $("settingsError").textContent = "";
  try {
    await reset(body);
    $("settingsDlg").close();
    toast("설정을 적용하고 다시 시작했습니다.", null, 2200);
  } catch (e) {
    $("settingsError").textContent = e.message;
  }
}

/* ------------------------------------------------------------------ agent chat */
function formatAnswer(text, cites) {
  const byId = new Map((cites || []).map((c) => [String(c.id), c]));
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\[#(\d+)\]/g, (m, id) => {
      const c = byId.get(id);
      if (!c || !c.valid) return `<span class="cite bad" title="조회 결과에 없는 기사">[#${id}]</span>`;
      return `<a class="cite" href="${esc(c.link)}" target="_blank" rel="noopener noreferrer" title="${esc(c.date)} · ${esc(c.headline)}">[#${id}]</a>`;
    });
}
function renderSuggestions() {
  const st = S.last;
  if (!st) return;
  const top = st.ranking.slice(0, 3).map((r) => r.keyword);
  const burst = (st.burst || []).find((b) => !b.rank);
  const qs = [...top.map((w) => `${w}가 왜 순위에 올랐어?`), "지금 가장 큰 화제는 뭐야?"];
  if (burst) qs.push(`${burst.keyword}가 왜 급상승이야?`);
  $("suggest").innerHTML = qs.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("");
}
function addBubble(cls, html) {
  const el = document.createElement("div");
  el.className = `bubble ${cls}`;
  el.innerHTML = html;
  $("chat").appendChild(el);
  $("chat").scrollTop = $("chat").scrollHeight;
  $("chatEmpty").hidden = true;
  $("btnClearChat").hidden = false;
  return el;
}
async function askAgent(ev) {
  ev.preventDefault();
  const q = $("askInput").value.trim();
  if (!q) return;
  togglePlay(false);
  const btn = $("askForm").querySelector("button");
  btn.disabled = true;
  $("askInput").value = "";
  addBubble("user", esc(q));
  const pending = addBubble("bot", '<p class="empty"><span class="spinner"></span>도구를 호출해 통계와 관련 기사를 확인하는 중…</p>');
  try {
    const r = await api("/api/ask", { question: q, keyword: S.selected });
    const trace = r.trace.map((t) => `<span class="chip" title="${esc(JSON.stringify(t.args))}">${esc(t.tool)} → ${esc(t.summary)}</span>`).join("");
    const modeLabel = r.mode.startsWith("solar") ? `Solar 실시간 답변 · ${r.mode.slice(7, -1)}`
      : r.mode === "offline" ? "오프라인 요약 (Solar 미사용)" : "오프라인 요약 (Solar 대체)";
    pending.innerHTML = `
      <div class="asof"><span>기준 시점 ${esc(r.as_of.date)} (기사 #${fmt(r.as_of.t)})</span><span>${esc(modeLabel)}</span>
        ${r.verified ? '<span class="ok" title="인용한 기사 id가 모두 도구 조회 결과에 있음"><svg><use href="#i-check"/></svg>근거 검증 통과</span>' : ""}</div>
      ${r.warning ? `<p class="warn"><svg><use href="#i-alert"/></svg><span>${esc(r.warning)}</span></p>` : ""}
      <div class="text">${formatAnswer(r.answer, r.citations)}</div>
      <details><summary>도구 호출 ${r.trace.length}회 보기</summary><div class="trace">${trace}</div></details>`;
    if (r.keyword && r.keyword !== S.selected) select(r.keyword, false);
  } catch (e) {
    pending.innerHTML = `<p class="warn"><svg><use href="#i-alert"/></svg><span>${esc(e.message)}</span></p>`;
  } finally {
    btn.disabled = false;
    $("chat").scrollTop = $("chat").scrollHeight;
  }
}

/* ------------------------------------------------------------------ share link */
function copyLink(kw) {
  const p = new URLSearchParams();
  if (S.last) p.set("start", S.last.clock.start_date);
  if (kw || S.selected) p.set("kw", kw || S.selected);
  if (S.view !== "table") p.set("view", S.view);
  const url = `${location.origin}${location.pathname}?${p}`;
  navigator.clipboard?.writeText(url).then(() => toast(`링크를 복사했습니다 <span class="tt-mute">${esc(url.slice(url.indexOf("?")))}</span>`, null, 2600))
    .catch(() => toast(esc(url), null, 6000));
}

/* ------------------------------------------------------------------ CSV export */
function downloadCsv() {
  const st = S.last;
  if (!st) return;
  const head = ["rank", "keyword", "score", "evidence_articles", "thin", "exact_rank", "exact_score", "may_swap_above", "may_swap_below", "error_bound", "dgim_estimate", "dgim_exact"];
  const q = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const lines = [head.join(",")].concat(st.ranking.map((r) => [r.rank, r.keyword, r.score, r.evidence, r.thin, r.baseline_rank, r.baseline_score,
    r.may_swap_above, r.may_swap_below, r.error_bound, r.dgim ? r.dgim.estimate : "", r.dgim ? r.dgim.exact : ""].map(q).join(",")));
  const meta = `# Trend Pulse v${S.info ? S.info.version : ""} · ${st.clock.date} (article #${st.clock.t}) · decay=${st.config.decay_unit} half_life=${st.config.decay_unit === "day" ? st.config.half_life_days + "d" : st.clock.half_life + " articles"} theta=${st.config.threshold} k=${st.config.k} phrases=${st.config.phrases}`;
  const blob = new Blob(["﻿", meta, "\n", lines.join("\n"), "\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `trendpulse_top${st.config.k}_${st.clock.date}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast(`CSV 저장: <b>${esc(a.download)}</b>`, null, 2200);
}

/* ------------------------------------------------------------------ views */
function setView(v) {
  S.view = v;
  $("rankView").querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
  $("viewTable").hidden = v !== "table";
  $("viewRace").hidden = v !== "race";
  $("viewCompare").hidden = v !== "compare";
  if (S.last) { if (v === "race") renderRace(S.last.race); if (v === "compare") renderCompare(S.last); }
}

/* ------------------------------------------------------------------ wiring */
function bind() {
  $("btnPlay").addEventListener("click", () => togglePlay());
  $("btnStep").addEventListener("click", () => { if (!S.playing) tick(); });
  $("btnReset").addEventListener("click", () => reset().catch(() => {}));
  $("btnDemo").addEventListener("click", () => demo(true));
  $("btnTheme").addEventListener("click", cycleTheme);
  $("btnShare").addEventListener("click", () => copyLink());
  $("btnCsv").addEventListener("click", downloadCsv);
  $("btnHelp").addEventListener("click", () => $("helpDlg").showModal());
  $("btnHelpClose").addEventListener("click", () => $("helpDlg").close());
  $("btnHelpOk").addEventListener("click", () => { $("helpDlg").close(); try { localStorage.setItem("tp-help-seen", "1"); } catch (e) { /* ignore */ } });
  $("btnSettings").addEventListener("click", () => { if (S.last) syncSettings(S.last.config); $("settingsError").textContent = ""; $("settingsDlg").showModal(); });
  $("btnSettingsClose").addEventListener("click", () => $("settingsDlg").close());
  $("btnSettingsCancel").addEventListener("click", () => $("settingsDlg").close());
  $("btnSettingsApply").addEventListener("click", applySettings);
  $("settingsForm").addEventListener("submit", (e) => { e.preventDefault(); applySettings(); });
  document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => applyPreset(b.dataset.preset)));
  ["cfgC", "cfgT", "cfgR"].forEach((id) => $(id).addEventListener("input", updateHints));
  $("cfgUnit").addEventListener("change", updateHints);
  $("speed").addEventListener("click", (e) => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    S.speed = Number(b.dataset.v);
    $("speed").querySelectorAll("[data-v]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
  });
  $("rankView").addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b) setView(b.dataset.view); });
  $("eventFilter").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    S.eventFilter = b.dataset.f;
    $("eventFilter").querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    if (S.last) renderEvents(S.last.events);
  });
  $("preset").addEventListener("change", () => {
    const custom = $("preset").value === "custom";
    $("customDate").hidden = !custom;
    if (!custom) reset().catch(() => {});
  });
  $("customDate").addEventListener("change", () => reset().catch(() => {}));
  $("rankBody").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-kw]"); if (tr) select(tr.dataset.kw); });
  $("rankBody").addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-kw]"); if (tr) select(tr.dataset.kw); } });
  $("events").addEventListener("click", (e) => { const li = e.target.closest("li[data-kw]"); if (li) select(li.dataset.kw); });
  $("burst").addEventListener("click", (e) => { const li = e.target.closest("li[data-kw]"); if (li) select(li.dataset.kw); });
  $("detailBody").addEventListener("click", async (e) => {
    const chip = e.target.closest("[data-kw]");
    if (chip) return select(chip.dataset.kw);
    const ask = e.target.closest("[data-ask]");
    if (ask) {
      $("askInput").value = `${ask.dataset.ask}가 왜 화제야?`;
      $("askForm").requestSubmit();
      if (window.innerWidth <= 1180) openDrawer(false);
      $("agentCard").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const tb = e.target.closest("[data-track]");
    if (tb) {
      const r = await api("/api/track", { keyword: tb.dataset.track });
      renderDetail(r, r.track_message);
    }
  });
  $("btnDrawerClose").addEventListener("click", () => openDrawer(false));
  $("drawerBackdrop").addEventListener("click", () => openDrawer(false));
  $("lookup").addEventListener("submit", (e) => { e.preventDefault(); select($("lookupInput").value.trim()); });
  $("askForm").addEventListener("submit", askAgent);
  $("suggest").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) { $("askInput").value = b.dataset.q; $("askForm").requestSubmit(); } });
  $("btnClearChat").addEventListener("click", () => { $("chat").querySelectorAll(".bubble").forEach((b) => b.remove()); $("chatEmpty").hidden = false; $("btnClearChat").hidden = true; renderSuggestions(); });
  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) || document.querySelector("dialog[open]");
    if (e.key === "Escape") { openDrawer(false); hideTip(); return; }
    if (typing) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); if (!S.playing) tick(); }
    else if (e.key === "/") { e.preventDefault(); openDrawer(true); $("lookupInput").focus(); }
    else if (e.key === "?") { $("helpDlg").showModal(); }
    else if (e.key === "1") setView("table");
    else if (e.key === "2") setView("race");
    else if (e.key === "3") setView("compare");
  });
  let resizeT;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => {
      if (S.timeline) { buildTimeline(S.timeline); if (S.last) updateTimeline(S.last.clock); }
      if (S.last) { if (S.view === "race") renderRace(S.last.race); if (S.view === "compare") renderCompare(S.last); }
      if (!$("drawer").classList.contains("open")) $("drawerBackdrop").hidden = true;
    }, 150);
  });
}

async function init() {
  bind();
  const params = new URLSearchParams(location.search);
  const [st, info, tl] = await Promise.all([api("/api/state"), api("/api/info"), api("/api/timeline")]);
  S.info = info;
  buildTimeline(tl);
  syncSettings(st.config);
  $("agentMode").textContent = info.solar_enabled ? `Solar · ${info.solar_model}` : "오프라인 요약 (API 키 없음)";
  $("agentMode").classList.toggle("good", info.solar_enabled);
  setPresetFromDate(st.clock.start_date);
  render(st);
  renderSuggestions();
  if (params.get("view")) setView(params.get("view"));
  if (params.get("theme")) document.documentElement.dataset.theme = params.get("theme");
  // First visit without a deep link: show "how to read this screen" once.
  try { if (!localStorage.getItem("tp-help-seen") && ![...params.keys()].length) $("helpDlg").showModal(); } catch (e) { /* ignore */ }
  // Deep links: /?demo=1 · /?start=2016-01-01&kw=scalia&view=race · /?kw=trump&q=trump가 왜 순위에 올랐어?
  if (params.get("demo")) return demo(params.get("ask") !== "0");
  const start = params.get("start");
  if (start && start !== st.clock.start_date) { setPresetFromDate(start); await reset().catch(() => {}); }
  const kw = params.get("kw");
  if (kw) await select(kw, true);
  else if (st.ranking.length) await select(st.ranking[0].keyword, false);
  const q = params.get("q");
  if (q) { $("askInput").value = q; $("askForm").requestSubmit(); }
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<p class="empty" style="padding:12px 24px;color:var(--bad-ink)">초기화 실패: ${esc(e.message)}</p>`); });
