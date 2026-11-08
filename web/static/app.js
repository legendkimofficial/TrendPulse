"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("ko-KR"));

let playing = false;
let busy = false;
let selected = null;
let timer = null;
let lastState = null;
let detailOpen = false;          // remember whether "비교·상세" is expanded
const TICK_MS = 700;

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok && !("track_ok" in data)) throw new Error(data.error || res.statusText);
  return data;
}

/* ------------------------------------------------------------------ rendering */
function render(st) {
  const prevRanking = lastState ? new Set(lastState.ranking.map((r) => r.keyword)) : null;
  lastState = st;
  const c = st.clock;
  $("clockDate").textContent = c.date;
  $("clockMeta").textContent =
    `기사 #${fmt(c.t)} · 재생 ${fmt(c.processed)}건 · 반감기 ≈ ${fmt(c.half_life)}건 · ` +
    `최근 ${fmt(c.effective_window)}건(${fmt(c.window_span_days)}일)이 가중치의 ${Math.round(c.recent_weight_share * 100)}%`;
  $("progressBar").style.width = `${(c.progress * 100).toFixed(2)}%`;
  $("kLabel").textContent = st.config.k;
  $("eventCaption").textContent =
    `첫 진입 = 이번 재생(${c.start_date}~)에서 처음 Top ${st.config.k}에 들어옴 · ` +
    `재진입 = ${fmt(st.config.new_entry_gap * st.config.snapshot_every)}건 이상 밖에 있다가 복귀`;
  renderRanking(st, prevRanking);
  renderEvents(st.events);
  renderMetrics(st.metrics, st.config);
  if (c.finished && playing) togglePlay(false);
}

function sparkPath(vals, w, h) {
  if (!vals || vals.length < 2) return "";
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  return vals.map((v, i) => `${i ? "L" : "M"}${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`).join("");
}

function deltaCell(r) {
  if (r.prev_rank == null) return '<span class="delta new" title="직전 화면 갱신 대비 Top K에 새로 들어옴">진입</span>';
  if (r.delta > 0) return `<span class="delta up">▲${r.delta}</span>`;
  if (r.delta < 0) return `<span class="delta down">▼${-r.delta}</span>`;
  return '<span class="delta same">–</span>';
}

function dgimCell(dg, N) {
  if (!dg) return '<span class="dg">–</span>';
  const part = dg.partial ? `<span class="partial" title="추적 시작 후 ${fmt(dg.observed)}건만 관측 — 관측 구간 집계 중">관측 ${fmt(dg.observed)}</span>` : "";
  return `<span class="dg"><b>${dg.estimate}</b> / ${dg.exact}${part}</span>`;
}

function renderRanking(st, prevSet) {
  const rows = st.ranking;
  const maxScore = rows.length ? rows[0].score : 1;
  $("rankBody").innerHTML = rows.map((r) => {
    const width = Math.max(4, (r.score / maxScore) * 120);
    const base = r.baseline_rank === r.rank
      ? `<span class="base same" title="정확 기준과 같은 순위">${r.baseline_rank}</span>`
      : `<span class="base diff" title="정확 기준 순위가 다름 (점수 ${r.baseline_score})">${r.baseline_rank ?? "밖"}</span>`;
    const flash = prevSet && !prevSet.has(r.keyword) ? " flash" : "";
    const sel = r.keyword === selected ? " selected" : "";
    return `<tr data-kw="${esc(r.keyword)}" class="${sel}${flash}">
      <td class="num rank">${r.rank}</td>
      <td>${deltaCell(r)}</td>
      <td class="kw">${esc(r.keyword)}</td>
      <td><div class="scorebar"><div class="bar" style="width:${width.toFixed(0)}px"></div><span>${r.score.toFixed(1)}</span></div></td>
      <td class="hide-sm"><svg class="spark" viewBox="0 0 84 24" preserveAspectRatio="none"><path d="${sparkPath(r.spark, 84, 24)}"/></svg></td>
      <td class="num hide-sm">${base}</td>
      <td class="num hide-sm">${dgimCell(r.dgim, st.config.dgim_n)}</td>
    </tr>`;
  }).join("");
}

function renderEvents(events) {
  $("eventCount").textContent = events.length;
  if (!events.length) {
    $("events").innerHTML = '<li class="empty">재생을 시작하면 Top K에 들어온 키워드가 표시됩니다.</li>';
    return;
  }
  $("events").innerHTML = events.map((e) => `<li data-kw="${esc(e.keyword)}">
    <span class="ev-date">${esc(e.date)}</span>
    <span class="ev-kw">${esc(e.keyword)}</span>
    <span class="kind ${e.kind}">${e.kind === "first" ? "첫 진입" : "재진입"}</span>
    <span class="ev-meta">${e.rank}위 · ${e.score}</span></li>`).join("");
}

function renderMetrics(m, cfg) {
  const keptPct = m.tracked_baseline ? (m.tracked_pruned / m.tracked_baseline) * 100 : 100;
  const dgimPct = m.exact_window_bits ? (m.dgim_bits / m.exact_window_bits) * 100 : null;
  $("metrics").innerHTML = `
    <div class="metric wide">
      <div class="label">저장 키워드 수 (논리 지표 · 실제 바이트 아님)</div>
      <div class="value">${fmt(m.tracked_pruned)} <small>/ 기준 ${fmt(m.tracked_baseline)} · ${m.keyword_reduction_pct}% 감소</small></div>
      <div class="membar" title="기준 방식 대비 보관 비율"><div style="width:${keptPct.toFixed(1)}%"></div></div>
      <div class="note">θ=${cfg.threshold} 미만 삭제 · 누적 삭제 ${fmt(m.dropped_total)}회 · 제거용 힙 항목 ${fmt(m.heap_entries)}개(기준 방식엔 없음)</div>
    </div>
    <div class="metric">
      <div class="label">Top ${m.k} 구성원 일치</div>
      <div class="value">${m.topk_overlap}<small>/${m.k}</small></div>
      <div class="note">${m.same_order ? "순서까지 동일" : "순서 일부 다름"}</div>
    </div>
    <div class="metric">
      <div class="label">점수 최대 오차</div>
      <div class="value">${m.max_score_error_topk}</div>
      <div class="note">이론 상한 ${m.score_error_bound}</div>
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
      <div class="label">관련 기사 조회용 인덱스 (집계 메모리와 별도) · 엔진 처리 속도</div>
      <div class="value">${fmt(m.index_postings)} <small>항목 · 약 ${(m.index_bytes_est / 1024 / 1024).toFixed(1)} MB(추정) · ${fmt(m.docs_per_sec)} 기사/s</small></div>
    </div>`;
}

/* ------------------------------------------------------------------ detail */
function trendSvg(history) {
  const pts = history.filter((h) => h.score != null);
  if (pts.length < 2) return '<p class="empty">추이를 그리기에 스냅샷이 부족합니다. 조금 더 재생하세요.</p>';
  const W = 520, H = 150, L = 34, B = 20, T = 8;
  const max = Math.max(...pts.map((p) => p.score)) * 1.08 || 1;
  const x = (i) => L + (i / (pts.length - 1)) * (W - L - 6);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join("");
  const area = `${line}L${x(pts.length - 1).toFixed(1)},${H - B}L${L},${H - B}Z`;
  const ticks = [0, 0.5, 1].map((f) => {
    const v = max * f;
    return `<line class="grid-line" x1="${L}" x2="${W - 6}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 4}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`;
  }).join("");
  return `<svg class="trend" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="감쇠 점수 추이">
    ${ticks}<path class="area" d="${area}"/><path class="line" d="${line}"/>
    <text x="${L}" y="${H - 5}">${esc(pts[0].date)}</text><text x="${W - 6}" y="${H - 5}" text-anchor="end">${esc(pts[pts.length - 1].date)}</text>
  </svg>`;
}

function summaryBox(d) {
  const win = lastState?.clock.effective_window ?? 1000;
  const fe = d.first_entry;
  const rankTxt = d.rank == null ? "집계에서 삭제됨(점수 < θ)" : `${d.rank}위${d.in_top_k ? "" : " (Top K 밖)"}`;
  const entryTxt = fe ? `${fe.kind === "first" ? "첫 진입" : "재진입"} ${esc(fe.date)} (${fe.rank}위)` : "이번 재생에서 Top K 진입 없음";
  const change = `최근 ${fmt(win)}건 중 <b>${d.mentions_in_effective_window}</b>건 · 그 이전 ${fmt(win)}건 중 ${d.mentions_in_previous_window}건`;
  const co = d.cooccurring.slice(0, 5).map(([w]) => esc(w)).join(", ") || "–";
  const top3 = d.related.slice(0, 3).map((a) => `<li><a href="${esc(a.link)}" target="_blank" rel="noopener noreferrer">${esc(a.headline)}</a>
      <span class="meta">#${a.id} · ${esc(a.date)} · ${esc(a.category)}</span></li>`).join("") || '<li class="empty">관련 기사 없음</li>';
  return `<div class="summary-box">
    <p class="lead">${esc(d.keyword)} · ${rankTxt} · ${entryTxt}</p>
    <div>${change} · 함께 등장: ${co}</div>
    <ol>${top3}</ol>
  </div>`;
}

function renderDetail(d, msg) {
  $("detailWord").textContent = d.keyword;
  const dg = d.dgim;
  const dgimHtml = dg
    ? `<div class="dgim-box">
        <div>최근 <b>${fmt(dg.window)}</b>개 기사 중 등장 기사 수 <span class="badge soft">추적 시작 ${esc(dg.since_date)}${dg.generation > 1 ? ` · ${dg.generation}번째 추적(관측 초기화)` : ""}${dg.pinned ? " · 고정" : ""}</span></div>
        <div class="big">≈ ${dg.estimate} <small>/ 정확 ${dg.exact}</small>${dg.partial ? ` <span class="partial">관측 ${fmt(dg.observed)}/${fmt(dg.window)}건 · 관측 구간 집계 중</span>` : ""}</div>
        <div>오차 ${dg.abs_error} (${(dg.rel_error * 100).toFixed(1)}%, 상한 ${(dg.bound * 100).toFixed(0)}%) · 버킷 ${dg.buckets}개 · 논리 ${dg.dgim_bits} bit (정확 ${dg.exact_bits} bit)</div>
        ${dg.pinned ? "" : `<button class="track-btn" data-track="${esc(d.keyword)}">추적 고정</button>`}
      </div>`
    : `<div class="dgim-box">DGIM으로 추적하지 않는 키워드입니다. <button class="track-btn" data-track="${esc(d.keyword)}">지금부터 추적</button>
        <div class="empty">Top K에 들어온 키워드는 자동으로 추적합니다. 추적 시작 이전 기사는 세지 않습니다.</div></div>`;
  const co = d.cooccurring.map(([w, n]) => `<span class="chip click" data-kw="${esc(w)}">${esc(w)} <b>${n}</b></span>`).join("") || '<span class="empty">–</span>';
  const cats = d.categories.map(([c, n]) => `<span class="chip">${esc(c)} <b>${n}</b></span>`).join("");
  const news = d.related.length
    ? d.related.map((a) => `<li><div class="meta">#${a.id} · ${esc(a.date)} · ${esc(a.category)}</div>
        <a href="${esc(a.link)}" target="_blank" rel="noopener noreferrer">${esc(a.headline)}</a>
        ${a.description ? `<div class="desc">${esc(a.description)}</div>` : ""}</li>`).join("")
    : '<li class="empty">아직 스트림에 관련 기사가 없습니다.</li>';
  $("detailBody").innerHTML = `
    ${summaryBox(d)}
    <details class="more" ${detailOpen ? "open" : ""}>
      <summary>비교 · 상세 (점수 추이, 정확 기준, DGIM, 전체 관련 기사)</summary>
      <div class="more-grid">
        <div>
          <div class="stat-row">
            <span class="chip">감쇠 점수 <b>${d.score}</b></span>
            <span class="chip" title="모든 키워드를 유지하는 정확 기준 점수">기준 점수 <b>${d.baseline_score}</b></span>
            ${d.first_seen ? `<span class="chip" title="이번 재생(warm-up 포함) 관측 범위에서 처음 등장">첫 관측 ${esc(d.first_seen.date)}</span>` : ""}
          </div>
          <div class="subhead">감쇠 점수 추이</div>
          ${trendSvg(d.history)}
          <div class="subhead">DGIM 근사 집계 (보조)</div>
          ${dgimHtml}
          ${msg ? `<div class="msg">${esc(msg)}</div>` : ""}
          <div class="subhead">함께 등장한 단어 (최근 관련 기사 60건)</div>
          <div class="stat-row">${co}</div>
          <div class="stat-row">${cats}</div>
        </div>
        <div>
          <div class="subhead">관련 기사 (최신순, 현재 시점까지)</div>
          <ul class="news">${news}</ul>
        </div>
      </div>
    </details>`;
  $("detailBody").querySelector("details.more").addEventListener("toggle", (e) => { detailOpen = e.target.open; });
}

async function select(word) {
  if (!word) return;
  selected = word.toLowerCase();
  document.querySelectorAll("#rankBody tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.kw === selected));
  try {
    renderDetail(await api(`/api/keyword/${encodeURIComponent(selected)}`));
  } catch (e) {
    $("detailBody").innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ playback */
async function tick() {
  if (busy) return;
  busy = true;
  try {
    const st = await api("/api/step", { n: Number($("speed").value) });
    render(st);
    if (selected) select(selected);
  } catch (e) {
    console.error(e);
    togglePlay(false);
  } finally {
    busy = false;
  }
  if (playing) timer = setTimeout(tick, TICK_MS);
}

function togglePlay(force) {
  playing = force ?? !playing;
  $("btnPlay").textContent = playing ? "❚❚ 일시정지" : "▶ 재생";
  clearTimeout(timer);
  if (playing) tick();
}

function startDate() {
  const p = $("preset").value;
  return p === "custom" ? $("customDate").value : p;
}

function syncSettings(cfg) {
  $("cfgC").value = cfg.c; $("cfgT").value = cfg.threshold; $("cfgK").value = cfg.k;
  $("cfgN").value = cfg.dgim_n; $("cfgR").value = cfg.dgim_r;
  updateCHint();
}

function updateCHint() {
  const c = Number($("cfgC").value);
  $("cfgCHint").textContent = c > 0 && c < 1
    ? `반감기 ≈ ${Math.round(Math.log(0.5) / Math.log(1 - c)).toLocaleString("ko-KR")}건 · 대표 규모 1/c ≈ ${Math.round(1 / c).toLocaleString("ko-KR")}건`
    : "0 < c < 1";
}

async function reset() {
  togglePlay(false);
  $("btnReset").disabled = true;
  try {
    const st = await api("/api/reset", {
      start_date: startDate(),
      c: $("cfgC").value, threshold: $("cfgT").value, k: $("cfgK").value,
      dgim_n: $("cfgN").value, dgim_r: $("cfgR").value,
    });
    lastState = null;
    render(st);
    if (selected) select(selected);
  } catch (e) {
    alert(`설정 오류: ${e.message}`);
  } finally {
    $("btnReset").disabled = false;
  }
}

async function demo(ask = true) {
  togglePlay(false);
  $("btnDemo").disabled = true;
  try {
    const st = await api("/api/demo", {});
    $("preset").value = st.demo.start_date;
    $("customDate").hidden = true;
    syncSettings(st.config);
    lastState = null;
    render(st);
    await select(st.demo.keyword);
    if (ask) {
      $("askInput").value = st.demo.question;
      $("askForm").requestSubmit();
    }
  } finally {
    $("btnDemo").disabled = false;
  }
}

/* ------------------------------------------------------------------ agent */
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

async function askAgent(ev) {
  ev.preventDefault();
  const q = $("askInput").value.trim();
  if (!q) return;
  togglePlay(false);
  const btn = $("askForm").querySelector("button");
  btn.disabled = true;
  $("answer").innerHTML = '<p class="empty"><span class="spinner"></span>도구를 호출해 통계와 관련 기사를 확인하는 중…</p>';
  try {
    const r = await api("/api/ask", { question: q, keyword: selected });
    const trace = r.trace.map((t) => `<span class="chip" title="${esc(JSON.stringify(t.args))}">${esc(t.tool)} → ${esc(t.summary)}</span>`).join("");
    const modeLabel = r.mode.startsWith("solar") ? `Solar 실시간 답변 · ${r.mode.slice(7, -1)}`
      : r.mode === "offline" ? "오프라인 요약 (Solar 미사용)" : "오프라인 요약 (Solar 대체)";
    $("answer").innerHTML = `
      <div class="asof">기준 시점 ${esc(r.as_of.date)} (기사 #${fmt(r.as_of.t)}) · ${esc(modeLabel)}
        ${r.verified ? ' · <span class="ok" title="인용한 기사 id가 모두 도구 조회 결과에 있음">근거 검증 통과</span>' : ""}</div>
      ${r.warning ? `<p class="warn">${esc(r.warning)}</p>` : ""}
      <div class="text">${formatAnswer(r.answer, r.citations)}</div>
      <div class="trace">${trace}</div>`;
    if (r.keyword && r.keyword !== selected) select(r.keyword);
  } catch (e) {
    $("answer").innerHTML = `<p class="warn">${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------------ wiring */
function bind() {
  $("btnPlay").addEventListener("click", () => togglePlay());
  $("btnStep").addEventListener("click", () => { if (!playing) tick(); });
  $("btnReset").addEventListener("click", reset);
  $("btnDemo").addEventListener("click", () => demo(true));
  $("btnSettings").addEventListener("click", () => {
    const s = $("settings");
    s.hidden = !s.hidden;
    $("btnSettings").setAttribute("aria-expanded", String(!s.hidden));
  });
  $("cfgC").addEventListener("input", updateCHint);
  $("preset").addEventListener("change", () => {
    const custom = $("preset").value === "custom";
    $("customDate").hidden = !custom;
    if (!custom) reset();
  });
  $("customDate").addEventListener("change", reset);
  $("rankBody").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-kw]"); if (tr) select(tr.dataset.kw); });
  $("events").addEventListener("click", (e) => { const li = e.target.closest("li[data-kw]"); if (li) select(li.dataset.kw); });
  $("detailBody").addEventListener("click", async (e) => {
    const chip = e.target.closest("[data-kw]");
    if (chip) return select(chip.dataset.kw);
    const tb = e.target.closest("[data-track]");
    if (tb) {
      const r = await api("/api/track", { keyword: tb.dataset.track });
      renderDetail(r, r.track_message);
    }
  });
  $("lookup").addEventListener("submit", (e) => { e.preventDefault(); select($("lookupInput").value.trim()); });
  $("askForm").addEventListener("submit", askAgent);
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      togglePlay();
    }
  });
}

async function init() {
  bind();
  const [st, info] = await Promise.all([api("/api/state"), api("/api/info")]);
  syncSettings(st.config);
  $("agentMode").textContent = info.solar_enabled ? `Solar · ${info.solar_model}` : "오프라인 요약 (API 키 없음)";
  render(st);
  // Demo links: /?demo=1  or  /?kw=scalia&q=scalia가 왜 순위에 올랐어?
  const params = new URLSearchParams(location.search);
  if (params.get("demo")) {
    detailOpen = params.get("open") === "1";
    return demo(params.get("ask") !== "0");
  }
  const kw = params.get("kw");
  if (kw) await select(kw);
  else if (st.ranking.length) await select(st.ranking[0].keyword);
  const q = params.get("q");
  if (q) {
    $("askInput").value = q;
    $("askForm").requestSubmit();
  }
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<p class="warn">초기화 실패: ${esc(e.message)}</p>`); });
