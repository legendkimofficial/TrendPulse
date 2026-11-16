"use strict";
/* Trend Pulse GKG dashboard, laid out and wired like the HuffPost v0.4 screen (app.js).
   Plain DOM + hand-drawn SVG charts. The server only reads precomputed 6-hour snapshots
   (/api/gkg/*); playback moves through those snapshots.
     utils · state · theme · timeline · render (clock, KPIs, table, race, compare, rail) ·
     drawer (key detail, search) · settings · agent · share/CSV · wiring/init              */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("ko-KR"));
const fmtN = (n, d = 1) => (n == null ? "–" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const compact = (n) => (n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(0)}K` : fmt(n));
const pct = (x, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const iso = (t) => new Date(t * 1000).toISOString();
const fmtT = (t) => `${iso(t).slice(0, 10)} ${iso(t).slice(11, 16)}`;
const fmtShort = (t) => `${iso(t).slice(5, 10)} ${iso(t).slice(11, 13)}시`;
const SVG_NS = "http://www.w3.org/2000/svg";
const TICK_MS = 900;
const KIND_KO = { P: "인물", O: "기관", L: "장소" };
const TAB_KO = { major: "7일 주요 이슈", new: "72시간 신규 이슈", trend: "30일 반짝·꾸준" };
const GROUP_KO = { rising: "최근 급증 · ratio 큰 순", cooling: "식는 중 · ratio 작은 순", steady: "꾸준 · 168h 점수순" };
const STATUS_TEXT = {
  warming_up: "판별 준비 중: 스트림 시작 후 충분한 시간이 지나지 않았습니다 (신규 이슈 7일, 반짝·꾸준 21일).",
  insufficient: "근거 부족: 이 판별에 쓰는 창에서 빠진 15분 슬롯이 5%를 넘어 보류했습니다.",
};
const FIELD_KO = { r72: "72h", r168: "168h", r720: "720h" };

/* ------------------------------------------------------------------ state */
const S = {
  info: null, times: [], idx: 0, tab: "major", kind: "all", view: "table", k: 20, thin: 0,
  variant: null, playing: false, busy: false, timer: null, speed: 1, last: null, selected: null,
  compare: [], cmpField: "r168", eventFilter: "all", memWin: "168h", e1: null, hoverKw: null, reqId: 0,
};

async function api(path, params = {}, body) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else if (v != null && v !== "") p.set(k, v);
  }
  if (S.variant && !p.has("v")) p.set("v", S.variant);
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(`${path}${[...p].length ? `?${p}` : ""}`, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
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

/* ------------------------------------------------------------------ theme (same cycle and key as v0.4) */
function cycleTheme() {
  const order = ["system", "light", "dark"];
  const cur = document.documentElement.dataset.theme || "system";
  const next = order[(order.indexOf(cur) + 1) % order.length];
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("tp-theme", next); } catch (e) { /* private mode */ }
  toast(`테마: <b>${{ system: "시스템 설정", light: "라이트", dark: "다크" }[next]}</b>`, null, 1600);
}

/* ------------------------------------------------------------------ timeline (61 days: bars, missing slots, events) */
const TL = { t0: 0, t1: 1, w: 0 };
const dayStart = (d) => Date.parse(`${d}T00:00:00Z`) / 1000;

function buildTimeline() {
  const days = S.info.days || [];
  if (!days.length) return;
  const svg = $("timelineSvg");
  const box = $("timeline");
  const W = box.clientWidth, H = box.clientHeight - 18;
  TL.w = W; TL.t0 = dayStart(days[0].date); TL.t1 = dayStart(days[days.length - 1].date) + 86400;
  const x = (t) => ((t - TL.t0) / (TL.t1 - TL.t0)) * W;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const max = Math.max(...days.map((d) => d.articles)) || 1;
  const top = 12, missH = 7, base = H - missH - 2;
  let out = `<rect class="played" id="tlPlayed" x="0" y="0" width="0" height="${H}"/>`;
  days.forEach((d) => {
    const x0 = x(dayStart(d.date)), bw = Math.max(1, x(dayStart(d.date) + 86400) - x0 - 1);
    const h = (d.articles / max) * (base - top);
    out += `<rect class="day" data-date="${d.date}" x="${x0.toFixed(1)}" y="${(base - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`;
    if (d.missing.length) {
      const mh = Math.max(2, Math.min(1, d.missing.length / 8) * missH);
      out += `<rect class="miss" x="${x0.toFixed(1)}" y="${(base + 2).toFixed(1)}" width="${bw.toFixed(1)}" height="${mh.toFixed(1)}"/>`;
    }
  });
  out += `<line class="baseline" x1="0" x2="${W}" y1="${base + .5}" y2="${base + .5}"/>`;
  (S.info.events || []).forEach((ev, i) => {
    const t = Date.parse(ev.time) / 1000;
    if (t < TL.t0 || t > TL.t1) return;
    const px = x(t);
    out += `<path class="ev${ev.group === "warmup" ? " warmup" : ""}" data-i="${i}" d="M${px.toFixed(1)},1 l4,4 -4,4 -4,-4z"/>`;
  });
  svg.innerHTML = out;
  const axis = $("tlAxis");
  axis.innerHTML = "";
  days.forEach((d, i) => {
    if (i % 7 === 0) axis.insertAdjacentHTML("beforeend", `<span style="left:${x(dayStart(d.date) + 43200).toFixed(1)}px">${d.date.slice(5)}</span>`);
  });
}

function bindTimeline() {
  const svg = $("timelineSvg");
  const tipEl = $("tlTip");
  const tAt = (e) => { const r = svg.getBoundingClientRect(); return TL.t0 + ((e.clientX - r.left) / r.width) * (TL.t1 - TL.t0); };
  svg.addEventListener("pointermove", (e) => {
    if (!S.info) return;
    const r = svg.getBoundingClientRect();
    tipEl.style.left = `${e.clientX - r.left}px`;
    const evEl = e.target.closest(".ev");
    if (evEl) {
      const ev = S.info.events[Number(evEl.dataset.i)];
      tipEl.innerHTML = `<b>${esc(ev.name)}</b> ${esc(ev.time.slice(0, 16).replace("T", " "))} UTC<br>${esc(ev.match.join(", "))}${ev.group === "warmup" ? " · 워밍업 참고" : ""}`;
    } else {
      const day = iso(Math.floor(tAt(e) / 86400) * 86400).slice(0, 10);
      const d = (S.info.days || []).find((x) => x.date === day);
      tipEl.innerHTML = d ? `${day}<br>기사 ${fmt(d.articles)}건${d.missing.length ? ` · 빠진 슬롯 ${d.missing.length}개 (${esc(d.missing.slice(0, 4).join(", "))}${d.missing.length > 4 ? "…" : ""})` : ""}` : day;
    }
    tipEl.hidden = false;
  });
  svg.addEventListener("pointerleave", () => { tipEl.hidden = true; });
  svg.addEventListener("click", (e) => {
    const evEl = e.target.closest(".ev");
    if (evEl) { const ev = S.info.events[Number(evEl.dataset.i)]; return seekTime(Date.parse(ev.time) / 1000, ev.name, true); }
    seekTime(tAt(e), null, false);
  });
}

function updateTimeline(t) {
  if (!TL.w) return;
  const px = Math.max(0, Math.min(1, (t - TL.t0) / (TL.t1 - TL.t0))) * TL.w;
  $("tlCursor").style.left = `${px.toFixed(1)}px`;
  const played = $("tlPlayed");
  if (played) played.setAttribute("width", px.toFixed(1));
  const day = iso(t).slice(0, 10);
  document.querySelectorAll("#timelineSvg .day").forEach((el) => el.classList.toggle("cur", el.dataset.date === day));
  document.querySelectorAll("#timelineSvg .ev").forEach((el) => {
    const ev = S.info.events[Number(el.dataset.i)];
    const te = Date.parse(ev.time) / 1000;
    el.classList.toggle("hot", te <= t && t - te < 3 * 86400);
  });
}

/* index of a stored snapshot for time t: after=true -> first snapshot at or after t (an event is included) */
function idxFor(t, after) {
  const ts = S.times;
  if (after) { const i = ts.findIndex((x) => x >= t); return i < 0 ? ts.length - 1 : i; }
  let i = 0;
  for (let j = 0; j < ts.length; j++) { if (ts[j] <= t) i = j; else break; }
  return i;
}
async function seekTime(t, label, after) {
  togglePlay(false);
  await go(idxFor(t, after));
  toast(`이동: <b>${esc(fmtT(S.times[S.idx]))} UTC</b>${label ? ` · ${esc(label)}` : ""}`, null, 2500);
}

/* ------------------------------------------------------------------ load + render */
async function go(i, opts = {}) {
  S.idx = Math.max(0, Math.min(S.times.length - 1, i));
  const id = ++S.reqId;
  const st = await api("/api/gkg/state", { t: S.times[S.idx], tab: S.tab, kind: S.kind, race: S.view === "race" ? 1 : null, k: Math.min(S.k, 10) });
  if (id !== S.reqId) return null;
  render(st, opts);
  if (S.selected && $("drawer").classList.contains("open")) select(S.selected, false);
  return st;
}

function render(st, opts = {}) {
  const prev = S.last;
  S.last = st;
  const finished = st.idx >= st.n - 1;
  $("clockDate").textContent = `${fmtT(st.t)} UTC`;
  $("clockMeta").textContent = `스냅샷 ${st.idx + 1}/${st.n} · 기사 ${fmt(st.articles)}건 처리 · 6시간 단위 · ${st.variant === "exact" ? "정확(θ 없음)" : `근사 ${st.variant}`}`;
  setLive(finished ? "finished" : S.playing ? "playing" : "paused");
  updateTimeline(st.t);
  $("kLabel").textContent = st.tab === "trend" ? `${Math.min(S.k, 10)} ×3` : S.k;
  $("tabTitle").textContent = TAB_KO[st.tab];
  $("variantBadge").textContent = st.variant === "exact" ? "정확 · 삭제 없음" : `근사 · ${st.variant}`;
  $("variantBadge").title = st.variant === "exact" ? "θ 삭제 없이 모든 키를 유지한 감쇠 점수" : "점수가 θ보다 작아진 키를 지운 근사 감쇠 점수 (과소추정 가능)";
  renderStatus(st);
  renderKpis(st);
  renderRanking(st, prev);
  renderBurst(st.burst);
  renderEvents(st.events);
  renderMemory();
  renderCountries(st.countries);
  if (S.view === "race") renderRace(st.race);
  if (S.view === "compare") renderCompare();
  if (opts.notify) notifyNewEvents(st, prev);
  if (finished && S.playing) togglePlay(false);
}

function setLive(state) {
  $("liveDot").dataset.state = state;
  $("liveLabel").textContent = { playing: "재생 중", paused: "일시정지", finished: "스트림 끝" }[state];
}

function notifyNewEvents(st, prev) {
  if (!prev || st.t <= prev.t) return;
  st.events.rows.filter((e) => e.kind === "first" && e.t > prev.t).slice(0, 3).reverse().forEach((e) => {
    toast(`<span class="kind first">첫 진입</span><b>${esc(e.key.slice(2))}</b> 신규 ${e.rank}위 · ${fmtShort(e.t)}`, () => select(e.key));
  });
}

function renderStatus(st) {
  const el = $("status");
  if (st.status === "ok") { el.hidden = true; return; }
  el.hidden = false;
  el.className = `g-status${st.status === "warming_up" ? " info" : ""}`;
  const extra = st.tab === "major" ? " 아래 순위는 그대로 보고, 분류(ratio 라벨)만 보류합니다." : " 판정이 보류되어 목록이 없습니다.";
  el.textContent = (STATUS_TEXT[st.status] || st.status) + (st.elapsed_days != null ? ` (경과 ${fmtN(st.elapsed_days, 1)}일)` : "") + extra;
}

/* ---- KPIs */
function renderKpis(st) {
  const k = st.kpi, stt = k.stats || {}, keys = stt.keys || {};
  const g = k.gaps || {};
  const worst = Math.max(0, ...Object.values(g).map((x) => x.missing_ratio || 0));
  const gapChip = Object.values(g).some((x) => x.insufficient) ? '<span class="delta-chip bad">5% 초과</span>' : worst > 0 ? '<span class="delta-chip warn">빈 슬롯 있음</span>' : '<span class="delta-chip good">없음</span>';
  const newChip = k.new_status === "ok" ? '<span class="delta-chip good">판별 중</span>' : `<span class="delta-chip warn">${k.new_status === "warming_up" ? "준비 중" : "근거 부족"}</span>`;
  const cache = S.info.variants.find((v) => v.name === st.variant) || {};
  $("kpis").innerHTML = `
    <div class="kpi">
      <div class="label"><span>누적 기사 수</span><span class="delta-chip">${pct(k.articles / (k.articles_total || 1), 0)}</span></div>
      <div class="value">${compact(k.articles)}<small>/ ${compact(k.articles_total)}</small></div>
      <div class="meter"><div style="width:${((k.articles / (k.articles_total || 1)) * 100).toFixed(1)}%"></div></div>
      <div class="note">캐시 재생 ${fmt(S.info.articles_per_s)}건/s · ${esc(cache.range ? `${cache.range.start}~${cache.range.end}` : "")}</div>
    </div>
    <div class="kpi">
      <div class="label"><span>추적 키 수 (168h)</span></div>
      <div class="value">${compact(keys["168h"])}<small>개</small></div>
      <div class="note">72h ${compact(keys["72h"])} · 720h ${compact(keys["720h"])} · 근거 추적 ${fmt(stt.evidence_keys)}</div>
    </div>
    <div class="kpi">
      <div class="label"><span>감쇠 메모리</span><span class="delta-chip">${st.variant === "exact" ? "정확" : esc(st.variant)}</span></div>
      <div class="value">${stt.decay_bytes != null ? fmtN(stt.decay_bytes / 1048576, 1) : "–"}<small>MB</small></div>
      <div class="note">세 창의 dict+float 추정치 · 키 문자열 제외</div>
    </div>
    <div class="kpi">
      <div class="label"><span>빈 구간 비율</span>${gapChip}</div>
      <div class="value">${pct(g["168h"] ? g["168h"].missing_ratio : null)}<small>168h</small></div>
      <div class="meter${worst > 0.05 ? " warn" : ""}"><div style="width:${Math.min(100, (worst / 0.05) * 100).toFixed(0)}%"></div></div>
      <div class="note">72h ${pct(g["72h"] && g["72h"].missing_ratio)} · 720h ${pct(g["720h"] && g["720h"].missing_ratio)} · 기준 5%</div>
    </div>
    <div class="kpi">
      <div class="label"><span>현재 신규 이슈</span>${newChip}</div>
      <div class="value">${k.new_status === "ok" ? fmt(k.new_qualified) : "–"}<small>개 조건 통과</small></div>
      <div class="note">${k.new_status === "ok" ? `표시 ${k.new_shown}개 · 72h 기사 ≥ 20, 언론사 ≥ 5` : esc(STATUS_TEXT[k.new_status] || "").slice(0, 40)}</div>
    </div>`;
}

/* ---- ranking table */
function sparkSvg(vals) {
  const v = (vals || []).map((x) => (x == null ? null : x));
  const pts = v.map((x, i) => [i, x]).filter((p) => p[1] != null);
  if (pts.length < 2) return '<svg class="spark" viewBox="0 0 84 24"></svg>';
  const w = 84, h = 24, ys = pts.map((p) => p[1]), min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
  const n = v.length;
  const pt = (i, y) => [((i / (n - 1)) * (w - 4) + 2).toFixed(1), (h - 3 - ((y - min) / span) * (h - 6)).toFixed(1)];
  const d = pts.map(([i, y], j) => { const [x, yy] = pt(i, y); return `${j ? "L" : "M"}${x},${yy}`; }).join("");
  const [lx, ly] = pt(...pts[pts.length - 1]);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}"/><circle cx="${lx}" cy="${ly}" r="2.4"/></svg>`;
}
function deltaCell(r) {
  if (r.prev_rank == null) return '<span class="chip new" title="6시간 전 스냅샷의 이 목록에 없다가 들어옴">진입</span>';
  if (r.delta > 0) return `<span class="delta up" title="6시간 전보다 ${r.delta}계단 상승">▲${r.delta}</span>`;
  if (r.delta < 0) return `<span class="delta down" title="6시간 전보다 ${-r.delta}계단 하락">▼${-r.delta}</span>`;
  return '<span class="delta same">–</span>';
}
function dgimCell(dg) {
  if (!dg) return '<span class="dg" title="DGIM 추적 전 (어느 창이든 Top 200에 들거나 신규 목록에 표시된 뒤부터 셈)">–</span>';
  const part = dg.partial ? `<span class="partial" title="추적 시작 후 ${fmt(dg.observed)}건만 관측">관측 ${compact(dg.observed)}</span>` : "";
  return `<span class="dg"><b>${fmt(Math.round(dg.estimate))}</b>${part}</span>`;
}
function exactCell(r) {
  if (r.exact_rank === undefined) return "";
  if (r.exact_rank == null) return '<td class="num hide-sm"><span class="base out" title="정확 캐시에서는 이 목록 밖">밖</span></td>';
  return `<td class="num hide-sm"><span class="base ${r.exact_rank === r.rank ? "same" : "diff"}" title="정확 캐시(θ 없음)에서의 순위">${r.exact_rank}</span></td>`;
}
function labelChip(r) { return `<span class="g-label ${esc(r.label)}">${esc(r.label_ko)}</span>`; }

function columns(st) {
  const c = [["num", "순위"], ["", "변동"], ["", "키"]];
  if (st.tab === "major") c.push(["scorecol", "168h 점수"], ["num", "보도율/h"], ["", "분류"]);
  if (st.tab === "new") c.push(["scorecol", "lift"], ["num", "72h 보도율/h"], ["num hide-sm", "72h 기사"], ["num hide-sm", "언론사"]);
  if (st.tab === "trend") c.push(["scorecol", "ratio"], ["num", "168h/h"], ["num hide-sm", "720h/h"]);
  c.push(["hide-sm", "근거"], ["hide-sm", "추이"]);
  if (st.exact_compared) c.push(["num hide-sm", "정확"]);
  c.push(["num hide-md", "DGIM ≈"]);
  return c;
}
const COL_TITLE = {
  "168h 점수": "168시간(7일) 반감기 감쇠 점수: 최근 기사일수록 큰 가중치",
  "보도율/h": "보정 보도율: 시간당 기사 수 (168h 창)",
  lift: "(72h 보도율+1)/(168h 보도율+1): 최근 사흘이 평소보다 몇 배인지",
  ratio: "(168h 보도율+1)/(720h 보도율+1): 최근 7일이 30일보다 몇 배인지",
  근거: "저장된 근거 기사 수 (Top 200 진입 후, 하루 30건까지)",
  추이: "최근 7일 보도율 (72시간 탭은 72h, 그 밖은 168h)",
  정확: "θ 없이 모든 키를 유지한 캐시에서의 순위 (다르면 주황)",
  "DGIM ≈": "최근 16만 건 기사 중 등장 기사 수 근사 (추적 시작 이후)",
};

function renderRanking(st, prev) {
  const tbody = $("rankBody");
  const cols = columns(st);
  $("thead").innerHTML = cols.map(([c, h]) => `<th class="${c}"${COL_TITLE[h] ? ` title="${esc(COL_TITLE[h])}"` : ""}>${esc(h)}</th>`).join("");
  const kGroup = Math.min(S.k, 10);
  const rows = st.tab === "trend" ? st.rows.filter((r) => r.rank <= kGroup) : st.rows.slice(0, S.k);
  const metric = (r) => (st.tab === "major" ? r.score : st.tab === "new" ? r.lift : r.ratio);
  const maxM = Math.max(...rows.map(metric).filter((x) => x != null), 1e-9);
  const maxEv = Math.max(...rows.map((r) => r.evidence), 1);
  const prevKeys = prev && prev.tab === st.tab && prev.kind === st.kind ? new Set(prev.rows.map((r) => r.key)) : null;
  const before = new Map([...tbody.rows].map((tr) => [tr.dataset.kw, tr.getBoundingClientRect().top]));
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${cols.length}" class="empty">${st.status === "ok" ? "조건을 통과한 키가 없습니다." : "보류 중이라 목록이 없습니다."}</td></tr>`;
  } else {
    let lastGroup = null;
    tbody.innerHTML = rows.map((r) => {
      let head = "";
      if (st.tab === "trend" && r.group !== lastGroup) {
        lastGroup = r.group;
        head = `<tr class="g-group"><td colspan="${cols.length}">${esc(GROUP_KO[r.group] || r.group)} <span class="g-muted">(${rows.filter((x) => x.group === r.group).length})</span></td></tr>`;
      }
      const thin = r.evidence <= S.thin;
      const ci = S.compare.indexOf(r.key);
      const cls = [r.key === S.selected ? "selected" : "", prevKeys && !prevKeys.has(r.key) ? "flash" : "", thin ? "thin" : ""].join(" ").trim();
      const m = metric(r);
      const cells = [
        `<td class="num rank">${r.rank}</td>`,
        `<td>${deltaCell(r)}</td>`,
        `<td class="kw"><span class="g-kind ${esc(r.kind)}">${esc(KIND_KO[r.kind])}</span>${esc(r.name)}${ci >= 0 ? `<span class="g-cmp-dot s${ci + 1}" title="보도율 비교에 포함"></span>` : ""}</td>`,
        `<td><div class="scorebar"><div class="track"><div class="bar" style="width:${Math.max(3, ((m || 0) / maxM) * 100).toFixed(1)}%"></div></div><span>${fmtN(m, st.tab === "major" ? 1 : 2)}</span></div></td>`,
      ];
      if (st.tab === "major") cells.push(`<td class="g-num">${fmtN(r.rate, 1)}</td>`, `<td>${labelChip(r)}${r.ratio != null ? ` <span class="g-muted g-num" title="ratio">${fmtN(r.ratio, 2)}</span>` : ""}</td>`);
      if (st.tab === "new") cells.push(`<td class="g-num">${fmtN(r.rate, 1)}</td>`, `<td class="g-num hide-sm">${fmt(r.articles_72h)}</td>`, `<td class="g-num hide-sm">${r.outlets == null ? "–" : r.outlets >= 8 ? "8+" : r.outlets}</td>`);
      if (st.tab === "trend") cells.push(`<td class="g-num">${fmtN(r.rate, 1)}</td>`, `<td class="g-num hide-sm">${fmtN(r.rate_720h, 1)}</td>`);
      cells.push(
        `<td class="hide-sm"><span class="evi${thin ? " thin" : ""}" title="저장된 근거 기사 ${r.evidence}건"><i style="--w:${((r.evidence / maxEv) * 100).toFixed(0)}%"></i>${fmt(r.evidence)}${thin ? '<span class="lbl">얇음</span>' : ""}</span></td>`,
        `<td class="hide-sm">${sparkSvg(r.spark)}</td>`,
        exactCell(r),
        `<td class="num hide-md">${dgimCell(r.dgim)}</td>`);
      return `${head}<tr data-kw="${esc(r.key)}" class="${cls}" tabindex="0">${cells.join("")}</tr>`;
    }).join("");
  }
  if (before.size && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    for (const tr of tbody.rows) {
      const old = before.get(tr.dataset.kw);
      if (old == null || !tr.dataset.kw) continue;
      const dy = old - tr.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) continue;
      tr.style.transition = "none";
      tr.style.transform = `translateY(${dy}px)`;
      requestAnimationFrame(() => { tr.style.transition = "transform .4s cubic-bezier(.2,.8,.2,1)"; tr.style.transform = ""; });
    }
  }
  const foot = {
    major: "168h(7일) 감쇠 점수 순. 분류는 ratio = (168h 보도율+1)/(720h 보도율+1)로 매기며 21일째부터 판별합니다.",
    new: `lift = (72h 보도율+1)/(168h 보도율+1) 순. 후보는 72h 상위 2,000개 중 최근 72시간 기사 ≥ 20, 언론사 ≥ 5 (조건 통과 ${fmt(st.qualified)}개).`,
    trend: `후보는 168h 상위 ${fmt(st.candidates)}개. 최근 급증(ratio ≥ 1.5) · 식는 중(< 1/1.5) · 꾸준(168h 점수순) 세 칸, 키 종류 필터를 먼저 적용합니다.`,
  }[st.tab];
  $("rankFoot").innerHTML = `${esc(foot)} 행을 누르면 상세가 열리고, <kbd>Shift</kbd>+클릭은 보도율 비교에 넣고 뺍니다.`;
}

/* ---- rank race (bump chart, v0.4 renderRace on cached ranks) */
function renderRace(race) {
  const wrap = $("raceChart");
  const W = wrap.clientWidth || 700;
  if (!race) { wrap.innerHTML = '<p class="empty">불러오는 중…</p>'; return; }
  const n = race.t.length, k = race.k;
  const words = Object.keys(race.series);
  $("raceCaption").textContent = `최근 ${n}개 스냅샷(6시간 간격) 동안 ${S.tab === "trend" ? "30일 탭 '최근 급증' 칸" : TAB_KO[S.tab]}의 Top ${k} 순위 변화. 선택한 키는 강조, 나머지는 회색. 선 끝의 이름을 누르면 상세가 열립니다.`;
  if (n < 2 || !words.length) { wrap.innerHTML = '<p class="empty">이 시점까지 쌓인 순위가 없습니다 (판별 준비 중이거나 보류 중).</p>'; return; }
  const L = 34, R = 170, T = 14, B = 26, rowH = 30;
  const H = T + k * rowH + B;
  const x = (i) => L + (i / (n - 1)) * (W - L - R);
  const y = (r) => T + (r - 1) * rowH + rowH / 2;
  let g = "";
  for (let r = 1; r <= k; r++) g += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(r)}" y2="${y(r)}"/><text class="mono" x="${L - 8}" y="${y(r) + 4}" text-anchor="end">${r}</text>`;
  [0, Math.floor((n - 1) / 2), n - 1].forEach((i) => { g += `<text class="mono" x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(fmtShort(race.t[i]))}</text>`; });
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
      if (!pen) { d += `M${cx.toFixed(1)},${cy.toFixed(1)}`; pen = true; } else { const mx = ((px + cx) / 2).toFixed(1); d += `C${mx},${py.toFixed(1)} ${mx},${cy.toFixed(1)} ${cx.toFixed(1)},${cy.toFixed(1)}`; }
      px = cx; py = cy;
    }
    if (!d) return;
    const cls = `${slot[w] || ""}${w === S.selected ? " hot" : ""}`;
    lines += `<path class="series ${cls}" data-kw="${esc(w)}" d="${d}"/><path class="hit" data-kw="${esc(w)}" d="${d}"/>`;
    if (s[n - 1] != null) {
      labels += `<circle class="dot ${cls}" data-kw="${esc(w)}" cx="${x(n - 1)}" cy="${y(s[n - 1])}" r="4.5"/>
        <text class="lbl${w === S.selected ? " hot" : ""}" data-kw="${esc(w)}" x="${W - R + 12}" y="${y(s[n - 1]) + 4}">${esc(w.slice(2))}</text>`;
    }
  });
  wrap.innerHTML = `<svg class="chart${S.selected ? " dim" : ""}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="최근 ${n}개 스냅샷의 Top ${k} 순위 변화">
      ${g}${lines}<line class="crosshair" id="raceCross" x1="0" x2="0" y1="${T}" y2="${y(k) + 10}" visibility="hidden"/>${labels}</svg>
    <div class="legend"><span><i class="s1"></i>현재 1위</span><span><i class="s2"></i>2위</span><span><i class="s3"></i>3위</span><span><i></i>그 밖의 Top ${k}</span><span><i class="ink"></i>선택·가리킨 키</span><span class="t-hint">끊긴 구간 = Top ${k} 밖 · 스냅샷 ${n}개</span></div>`;
  const svg = wrap.querySelector("svg");
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1))));
    const cross = $("raceCross");
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    const at = words.filter((w) => race.series[w][i] != null).sort((a, b) => race.series[a][i] - race.series[b][i]);
    const hot = S.hoverKw || S.selected;
    showTip(`<b>${esc(fmtT(race.t[i]))} UTC</b><br>` + at.map((w) => `<div class="tt-row"><span>${w === hot ? "<i></i>" : ""}${race.series[w][i]}. ${esc(w.slice(2))}</span></div>`).join(""), e.clientX, e.clientY);
  });
  svg.addEventListener("pointerleave", () => { $("raceCross").setAttribute("visibility", "hidden"); hideTip(); });
  svg.addEventListener("pointerover", (e) => { const el = e.target.closest("[data-kw]"); if (el) highlight(svg, el.dataset.kw); });
  svg.addEventListener("pointerout", (e) => { if (!svg.contains(e.relatedTarget) || !e.relatedTarget.closest("[data-kw]")) highlight(svg, null); });
  svg.addEventListener("click", (e) => { const el = e.target.closest("[data-kw]"); if (el) select(el.dataset.kw); });
}
function highlight(svg, kw) {
  S.hoverKw = kw;
  const hot = kw || S.selected;
  svg.classList.toggle("dim", !!hot);
  svg.querySelectorAll("[data-kw]").forEach((el) => el.classList.toggle("hot", el.dataset.kw === hot));
}

/* ---- compare: rate lines of chosen keys (replaces v0.4's approx-vs-exact slope chart) */
function compareKeys() {
  if (S.compare.length) return S.compare;
  return (S.last ? S.last.rows : []).slice(0, 3).map((r) => r.key);
}
function toggleCompare(key) {
  const i = S.compare.indexOf(key);
  if (i >= 0) S.compare.splice(i, 1);
  else { if (S.compare.length >= 3) S.compare.shift(); S.compare.push(key); }
  toast(i >= 0 ? `비교에서 뺌: <b>${esc(key.slice(2))}</b>` : `보도율 비교에 추가: <b>${esc(key.slice(2))}</b> (${S.compare.length}/3)`, () => setView("compare"), 2200);
  if (S.last) renderRanking(S.last, S.last);
  if (S.view === "compare") renderCompare();
}
async function renderCompare() {
  const wrap = $("compareChart");
  const keys = compareKeys();
  if (!keys.length) { wrap.innerHTML = '<p class="empty">비교할 키가 없습니다. 표에서 Shift+클릭하거나 상세 패널의 "비교에 추가"를 누르세요.</p>'; return; }
  const id = S.reqId;
  let data;
  try { data = await api("/api/gkg/series", { key: keys, t: S.times[S.idx] }); } catch (e) { wrap.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  if (id !== S.reqId && S.playing) return;
  const W = wrap.clientWidth || 700, H = 260, L = 44, R = 16, T = 12, B = 26;
  const ts = data.t, n = ts.length, f = S.cmpField;
  if (n < 2) { wrap.innerHTML = '<p class="empty">스냅샷이 2개 이상 쌓이면 그립니다.</p>'; return; }
  const vals = keys.flatMap((k) => data.series[k][f].filter((v) => v != null));
  const max = (Math.max(...vals, 0) || 1) * 1.1;
  const x = (i) => L + (i / (n - 1)) * (W - L - R);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  let g = [0, 0.5, 1].map((q) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(max * q)}" y2="${y(max * q)}"/><text class="mono" x="${L - 6}" y="${y(max * q) + 3}" text-anchor="end">${fmtN(max * q, max * q < 10 ? 1 : 0)}</text>`).join("");
  [0, Math.floor((n - 1) / 2), n - 1].forEach((i) => { g += `<text class="mono" x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(iso(ts[i]).slice(5, 10))}</text>`; });
  let lines = "";
  keys.forEach((k, j) => {
    const s = data.series[k][f];
    let d = "", pen = false;
    s.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
    lines += `<path class="series s${j + 1}" data-kw="${esc(k)}" d="${d}"/><path class="hit" data-kw="${esc(k)}" d="${d}"/>`;
  });
  const chosen = S.compare.length > 0;
  wrap.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="선택 키 보도율 비교">${g}${lines}
      <line class="crosshair" id="cmpCross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/></svg>
    <div class="legend">${keys.map((k, j) => `<span><i class="s${j + 1}"></i>${esc(k.slice(2))}${chosen ? ` <button type="button" class="g-x" data-rm="${esc(k)}" aria-label="비교에서 빼기">×</button>` : ""}</span>`).join("")}
      <span class="t-hint">${FIELD_KO[f]} 보정 보도율(건/시간) · ${chosen ? "고른 키" : "현재 목록 상위 3개"} · 기록은 키가 목록이나 Top 200에 처음 든 뒤부터</span></div>`;
  const svg = wrap.querySelector("svg");
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1))));
    const c = $("cmpCross");
    c.setAttribute("x1", x(i)); c.setAttribute("x2", x(i)); c.setAttribute("visibility", "visible");
    showTip(`<b>${esc(fmtT(ts[i]))} UTC</b>` + keys.map((k) => `<div class="tt-row"><span>${esc(k.slice(2))}</span><span>${fmtN(data.series[k][f][i], 1)}/h</span></div>`).join(""), e.clientX, e.clientY);
  });
  svg.addEventListener("pointerleave", () => { $("cmpCross").setAttribute("visibility", "hidden"); hideTip(); });
  svg.addEventListener("click", (e) => { const el = e.target.closest("[data-kw]"); if (el) select(el.dataset.kw); });
  wrap.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => toggleCompare(b.dataset.rm)));
}

/* ---- rail: burst (72h new top), entry events, memory cost (E1), countries */
function renderBurst(b) {
  if (b.status !== "ok") { $("burst").innerHTML = `<li class="empty">${esc(STATUS_TEXT[b.status] || b.status)}</li>`; return; }
  if (!b.rows.length) { $("burst").innerHTML = '<li class="empty">조건(72h 기사 ≥ 20, 언론사 ≥ 5)을 넘는 신규 이슈가 없습니다.</li>'; return; }
  const max = Math.max(...b.rows.map((r) => r.lift - 1), 1e-9);
  $("burst").innerHTML = b.rows.map((r) => `<li class="burst-row" data-kw="${esc(r.key)}">
    <span class="ev-kw"><span><span class="g-kind ${esc(r.kind)}">${esc(KIND_KO[r.kind])}</span>${esc(r.name)}</span><span class="lift" title="lift = (72h 보도율+1)/(168h 보도율+1)">×${fmtN(r.lift, 2)}</span>
      ${r.major_rank ? `<span class="rank-tag" title="7일 주요 이슈 순위">7일 ${r.major_rank}위</span>` : '<span class="rank-tag out" title="7일 주요 이슈 Top 20에는 아직 없음: 신규 판별이 먼저 잡았다">7일 Top 20 밖</span>'}</span>
    <span class="ev-meta" title="최근 72시간 기사 수 · 언론사 수(최대 8곳까지 근사)">${fmt(r.articles_72h)}건 · ${r.outlets >= 8 ? "8+" : fmt(r.outlets)}곳</span>
    <span class="burst-bar"><div style="--w:${Math.max(4, ((r.lift - 1) / max) * 100).toFixed(0)}%"></div></span></li>`).join("");
}
function renderEvents(ev) {
  $("eventCount").textContent = fmt(ev.total);
  const rows = S.eventFilter === "all" ? ev.rows : ev.rows.filter((e) => e.kind === S.eventFilter);
  if (!rows.length) { $("events").innerHTML = `<li class="empty">${ev.total ? "해당 종류의 진입이 이 시점까지 없습니다 (최근 150건 중)." : "신규 이슈 판별은 7일째부터입니다. 이 시점까지 진입 기록이 없습니다."}</li>`; return; }
  $("events").innerHTML = rows.slice(0, 60).map((e) => `<li data-kw="${esc(e.key)}">
    <span class="ev-date">${esc(fmtShort(e.t))}</span>
    <span class="ev-kw"><span>${esc(e.key.slice(2))}</span><span class="kind ${e.kind === "first" ? "first" : "reentry"}">${e.kind === "first" ? "첫 진입" : "재진입"}</span></span>
    <span class="ev-meta">${e.rank}위${e.lift != null ? ` · ×${fmtN(e.lift, 2)}` : ""}</span></li>`).join("");
}
function renderMemory() {
  const box = $("metrics");
  const e1 = S.e1;
  const cur = S.last && S.last.kpi.stats;
  const now = cur ? `<div class="metric wide"><div class="label">이 캐시의 현재 감쇠 메모리 (${esc(S.last.variant)})</div>
      <div class="value">${fmtN((cur.decay_bytes || 0) / 1048576, 1)} <small>MB · 키 ${compact((cur.keys || {})[S.memWin])}개 (${S.memWin})</small></div></div>` : "";
  if (!e1 || e1.status !== "ok") {
    box.innerHTML = `<div class="metric wide"><div class="label">θ별 메모리 vs Top-20 겹침 (실험 E1)</div><div class="value"><small>${e1 && e1.status === "error" ? `결과 파일을 읽지 못했습니다: ${esc(e1.error)}` : "실험 결과 대기 중"}</small></div>
      <div class="note">experiments/results/gkg/results.json 이 생기면 표시합니다.</div></div>${now}`;
    return;
  }
  const rows = e1.rows.filter((r) => r.window === S.memWin);
  const exact = rows.find((r) => r.theta == null) || rows[0];
  box.innerHTML = rows.map((r) => {
    const kept = exact && exact.keys_mean ? (r.keys_mean / exact.keys_mean) * 100 : 100;
    return `<div class="metric wide${r.theta == null ? " exact" : ""}">
      <div class="label">${r.theta == null ? "정확 (삭제 없음)" : `θ = ${r.theta}`}${r.theta != null ? `<span class="badge soft">키 −${(100 - kept).toFixed(0)}%</span>` : ""}</div>
      <div class="value">${compact(r.keys_mean)} <small>키 · ${fmtN(r.mb_mean, 1)} MB (평균)</small></div>
      <div class="membar" title="정확 대비 보관 키 비율"><div style="width:${kept.toFixed(1)}%"></div></div>
      <div class="note"><span class="g-overlap">Top-${e1.k} 겹침 평균 ${fmtN(r.overlap_mean, 3)} · 최소 ${fmtN(r.overlap_min, 2)}</span> · 순서 동일 ${pct(r.same_order_share, 0)} · 점수 상대오차 평균 ${pct(r.rel_err_mean, 2)}</div>
    </div>`;
  }).join("") + now + `<p class="g-e1-meta">E1 · ${esc(e1.range ? `${e1.range.start}~${e1.range.end}` : "")} (평가 ${esc(e1.eval_from || "")}~, 6시간마다) · <code>${esc(e1.command || "")}</code> · 입력 ${esc((e1.stream_sha256 || "").slice(0, 12))}…</p>`;
}
function renderCountries(cs) {
  const max = Math.max(...(cs || []).map((c) => c.rate || 0), 1e-9);
  $("countries").innerHTML = (cs || []).map((c) => `<li><span>${esc(c.name)}</span><span>${fmtN(c.rate, 1)}/h</span><i style="width:${(((c.rate || 0) / max) * 100).toFixed(0)}%"></i></li>`).join("") || '<li class="g-muted">없음</li>';
}

/* ------------------------------------------------------------------ drawer / key detail */
function openDrawer(open) {
  $("drawer").classList.toggle("open", open);
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("drawerBackdrop").hidden = !open || window.innerWidth > 1180;
}
function trendSvg(ser) {
  const n = ser.t.length;
  const has = (f) => ser[f].some((v) => v != null);
  if (n < 2 || !has("r168")) return '<p class="empty">이 키는 아직 보도율 기록이 없습니다 (목록이나 Top 200에 처음 든 뒤부터 기록).</p>';
  const W = 540, H = 170, L = 40, B = 22, T = 10, R = 44;
  const max = Math.max(...["r72", "r168", "r720"].flatMap((f) => ser[f].filter((v) => v != null)), 0) * 1.1 || 1;
  const x = (i) => L + (i / (n - 1)) * (W - L - R);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const path = (f) => { let d = "", pen = false; ser[f].forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; }); return d; };
  const ticks = [0, 0.5, 1].map((q) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(max * q)}" y2="${y(max * q)}"/><text class="mono" x="${L - 6}" y="${y(max * q) + 3}" text-anchor="end">${fmtN(max * q, max < 10 ? 1 : 0)}</text>`).join("");
  let li = n - 1;
  while (li > 0 && ser.r168[li] == null) li--;
  const last = ser.r168[li];
  return `<div class="chart-wrap" id="trendWrap"><svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="보도율 추이">
    ${ticks}<path class="line r720" d="${path("r720")}"/><path class="line r72" d="${path("r72")}"/><path class="line" d="${path("r168")}"/>
    <circle class="end-dot" cx="${x(li)}" cy="${y(last)}" r="4"/>
    <text class="mono" x="${x(li) + 8}" y="${y(last) + 4}" style="fill:var(--text)">${fmtN(last, 1)}</text>
    <text class="mono" x="${L}" y="${H - 6}">${esc(iso(ser.t[0]).slice(5, 10))}</text><text class="mono" x="${W - R}" y="${H - 6}" text-anchor="end">${esc(iso(ser.t[n - 1]).slice(5, 10))}</text>
    <line class="crosshair" id="trendCross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/></svg></div>
    <div class="g-legend-inline"><span><i class="r72"></i>72h</span><span><i class="r168"></i>168h</span><span><i class="r720"></i>720h</span><span>보정 보도율(건/시간)</span></div>`;
}
function bindTrend(ser) {
  const wrap = $("trendWrap");
  if (!wrap) return;
  const svg = wrap.querySelector("svg");
  const n = ser.t.length, W = 540, L = 40, R = 44;
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1))));
    const cx = L + (i / (n - 1)) * (W - L - R);
    const c = $("trendCross");
    c.setAttribute("x1", cx); c.setAttribute("x2", cx); c.setAttribute("visibility", "visible");
    showTip(`<b>${esc(fmtT(ser.t[i]))} UTC</b>` + ["r72", "r168", "r720"].map((f) => `<div class="tt-row"><span>${FIELD_KO[f]}</span><span>${fmtN(ser[f][i], 1)}/h</span></div>`).join(""), e.clientX, e.clientY);
  });
  svg.addEventListener("pointerleave", () => { $("trendCross").setAttribute("visibility", "hidden"); hideTip(); });
}

function renderDetail(d) {
  const key = d.key, row = d.row, rates = d.rates || {};
  const inList = S.last ? S.last.rows.find((r) => r.key === key) : null;
  const rankTxt = inList ? `${TAB_KO[S.last.tab]} ${inList.rank}위${inList.group ? ` (${GROUP_KO[inList.group].split(" ·")[0]})` : ""}` : row ? `${TAB_KO[row.tab]} 목록에 있음` : "이 시점 목록 밖";
  const ents = d.entries || [];
  const first = ents.find((e) => e.kind === "first");
  const entry = first ? `<span class="kind first">첫 진입</span> ${esc(fmtShort(first.t))} (신규 ${first.rank}위)${ents.length > 1 ? ` · 재진입 ${ents.length - 1}회` : ""}` : "72시간 신규 Top 10 진입 없음";
  const watched = d.series && d.series.watched_from ? `<span class="t-hint">· 기록 시작 ${esc(fmtShort(d.series.watched_from))}</span>` : "";
  const r168 = rates.r168, r720 = rates.r720, mx = Math.max(r168 || 0, r720 || 0, 1e-9);
  const ratioTile = row && row.lift != null
    ? `<div class="label">lift (72h vs 168h)</div><div class="value">×${fmtN(row.lift, 2)}<small> ${fmt(row.articles_72h)}건 · ${row.outlets >= 8 ? "8+" : fmt(row.outlets)}곳</small></div>`
    : `<div class="label">ratio (168h vs 720h)</div><div class="value">${row && row.ratio != null ? fmtN(row.ratio, 2) : r168 != null && r720 != null ? fmtN((r168 + 1) / (r720 + 1), 2) : "–"}<small> ${row ? labelChip(row) : ""}</small></div>`;
  const dg = row && row.dgim;
  const dgimHtml = dg
    ? `<div class="dgim-box">
        <div class="row">최근 <b>${fmt(dg.window)}</b>개 기사(약 하루) 중 등장 기사 수</div>
        <div class="big">≈ ${fmt(Math.round(dg.estimate))}${dg.partial ? ` <span class="partial">관측 ${fmt(dg.observed)}/${fmt(dg.window)}건 · 집계 중</span>` : ""}</div>
        <div class="row">상대 오차 상한 ${Math.round(dg.error_bound * 100)}% · 추적 시작 이전 기사는 세지 않음</div></div>`
    : `<div class="dgim-box"><div class="row">이 시점 목록에 없는 키라 DGIM 값이 없습니다. DGIM은 어느 창이든 Top 200에 들거나 신규 목록에 표시된 키를 캐시를 만들 때 추적합니다 (추적 고정은 미리 계산한 캐시라 불가).</div></div>`;
  const co = d.cooccurring || { keys: [], domains: [], articles: 0 };
  const coHtml = co.keys.map(([k, c]) => `<span class="chip click" data-kw="${esc(k)}"><span class="g-kind ${esc(k[0])}">${esc(KIND_KO[k[0]])}</span>${esc(k.slice(2))} <b>${c}</b></span>`).join("") || '<span class="empty">–</span>';
  const domHtml = co.domains.map(([dm, c]) => `<span class="chip">${esc(dm)} <b>${c}</b></span>`).join("");
  const ev = d.evidence.articles || [];
  const news = ev.length ? ev.map((a, i) => {
    const u = safeUrl(a.url);
    return `<li${i < 3 ? ' class="top"' : ""}><div class="meta">#${a.idx} · ${esc(fmtT(a.ts))} · ${esc(a.domain)}</div>
      ${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(a.title || a.url)}<svg><use href="#i-external"/></svg></a>` : esc(a.title || a.url)}</li>`;
  }).join("") : '<li class="empty">저장된 근거 기사가 없습니다 (Top 200 진입 또는 신규 목록 표시 이후부터, 키마다 하루 30건까지 저장).</li>';
  const inCmp = S.compare.includes(key);
  $("detailBody").innerHTML = `
    <div class="d-title"><h2>${esc(key.slice(2))}</h2><span class="kw-rank">${esc(rankTxt)}</span></div>
    <div class="d-entry"><span class="g-kind ${esc(key[0])}">${esc(KIND_KO[key[0]])}</span>${entry}${watched}<span class="t-hint">· 근거 ${fmt(d.evidence.total)}건</span></div>
    <div class="d-tiles">
      <div class="d-tile"><div class="label">보정 보도율 168h · 720h (건/시간)</div><div class="value">${fmtN(r168, 1)}<small> / ${fmtN(r720, 1)} · 72h ${fmtN(rates.r72, 1)}</small></div>
        <div class="cmp"><div style="height:${Math.max(6, ((r720 || 0) / mx) * 100).toFixed(0)}%" title="720h ${fmtN(r720, 1)}/h"></div><div class="now" style="height:${Math.max(6, ((r168 || 0) / mx) * 100).toFixed(0)}%" title="168h ${fmtN(r168, 1)}/h"></div></div>
        <div class="cmp-lbl"><span>30일(720h)</span><span>7일(168h)</span></div></div>
      <div class="d-tile">${ratioTile}<div class="cmp-lbl"><span>${row ? `점수 ${fmtN(row.score, 1)} · 빈 구간 ${pct(row.gap)}` : "현재 목록 밖"}</span></div></div>
    </div>
    <div class="d-actions">
      <button class="btn small" data-ask="${esc(key)}"><svg><use href="#i-bot"/></svg>에이전트에게 "왜 많이 나와?" 묻기</button>
      <button class="btn small" data-cmp="${esc(key)}"><svg><use href="#i-plus"/></svg>${inCmp ? "비교에서 빼기" : "비교에 추가"}</button>
      <a class="btn small ghost" href="#" id="kwLink" title="이 키와 시점 링크"><svg><use href="#i-link"/></svg>링크</a>
    </div>
    <div class="d-sec">보도율 추이 <span class="t-hint">스냅샷 ${d.series ? d.series.t.length : 0}개 · ${esc(fmtT(d.t))}까지</span></div>
    ${d.series ? trendSvg(d.series) : ""}
    <div class="d-sec">근거 기사 <span class="t-hint">최신순 · 이 시점까지 · 위 3건이 대표 기사</span></div>
    <ul class="news">${news}</ul>
    <div class="d-sec">함께 등장한 키 <span class="t-hint">근거 기사 ${fmt(co.articles)}건 기준 · 추적 키만</span></div>
    <div class="chips">${coHtml}</div>
    <div class="chips" style="margin-top:8px">${domHtml}</div>
    <div class="d-sec">DGIM 근사 집계 <span class="t-hint">보조 기능</span></div>
    ${dgimHtml}`;
  if (d.series) bindTrend(d.series);
  $("kwLink").addEventListener("click", (e) => { e.preventDefault(); copyLink(key); });
}

async function select(key, open = true) {
  if (!key) return;
  S.selected = key;
  document.querySelectorAll("#rankBody tr[data-kw]").forEach((tr) => tr.classList.toggle("selected", tr.dataset.kw === key));
  if (open) openDrawer(true);
  try {
    renderDetail(await api("/api/gkg/key", { key, t: S.times[S.idx], full: 1, limit: 20 }));
  } catch (e) {
    $("detailBody").innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
  if (S.view === "race" && S.last) renderRace(S.last.race);
}

async function lookup(raw) {
  const q = raw.trim();
  if (!q) return;
  if (/^[POL]:/.test(q)) return select(q);
  const r = await api("/api/gkg/search", { q });
  if (!r.keys.length) { toast(`<b>${esc(q)}</b>: 기록된 키 중에 없습니다 (목록이나 Top 200에 든 적이 있는 키만 검색됩니다).`); return; }
  select(r.keys[0]);
}
let suggestT;
function suggestKeys() {
  clearTimeout(suggestT);
  suggestT = setTimeout(async () => {
    const q = $("lookupInput").value.trim();
    if (q.length < 2 || /^[POL]:/.test(q)) return;
    try {
      const r = await api("/api/gkg/search", { q });
      $("lookupList").innerHTML = r.keys.map((k) => `<option value="${esc(k)}">${esc(KIND_KO[k[0]])}</option>`).join("");
    } catch (e) { /* ignore */ }
  }, 180);
}

/* ------------------------------------------------------------------ playback */
async function tick() {
  if (S.busy) return;
  S.busy = true;
  try {
    if (S.idx >= S.times.length - 1) { togglePlay(false); return; }
    await go(S.idx + S.speed, { notify: true });
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
  if (S.playing && S.idx >= S.times.length - 1) S.playing = false;
  const b = $("btnPlay");
  b.setAttribute("aria-pressed", String(S.playing));
  b.querySelector("span").textContent = S.playing ? "일시정지" : "재생";
  setLive(S.last && S.last.idx >= S.last.n - 1 ? "finished" : S.playing ? "playing" : "paused");
  clearTimeout(S.timer);
  if (S.playing) tick();
}
async function step(dir) {
  if (S.playing || S.busy) return;
  S.busy = true;
  try { await go(S.idx + dir, { notify: dir > 0 }); } finally { S.busy = false; }
}

function fillPresets() {
  const sel = $("preset");
  const range = S.info.range;
  const evs = (S.info.events || []).map((e, i) => ({ ...e, i })).filter((e) => e.time.slice(0, 10) >= range.start && e.time.slice(0, 10) <= range.end);
  sel.innerHTML = `<option value="">— 사건 프리셋 —</option>` +
    evs.map((e) => `<option value="${e.i}">${esc(e.time.slice(5, 10))} ${esc(e.name)}${e.group === "warmup" ? " (워밍업)" : ""}</option>`).join("") +
    '<option value="custom">날짜 직접 선택…</option>';
  const cd = $("customDate");
  cd.min = range.start; cd.max = range.end;
}
async function demo(ask = true) {
  togglePlay(false);
  const sel = $("preset").value;
  const evs = S.info.events || [];
  const ev = sel !== "" && sel !== "custom" ? evs[Number(sel)]
    : evs.find((e) => e.group === "E2" && e.time.slice(0, 10) <= S.info.range.end) || evs[0];
  if (!ev) { toast("사건 목록이 없습니다."); return; }
  $("btnDemo").disabled = true;
  try {
    S.tab = "new"; syncTabs();
    await go(idxFor(Date.parse(ev.time) / 1000 + 86400, true));
    const r = await api("/api/gkg/search", { q: ev.match[0] || ev.name });
    // same rule as experiment E2: key name contains a match string, key kind in the event's types
    const ok = (k) => (!ev.types.length || ev.types.includes(k[0])) && ev.match.some((m) => k.slice(2).includes(m));
    const key = (S.last.rows.find((x) => ok(x.key)) || {}).key || r.keys.find(ok);
    toast(`사건 장면: <b>${esc(ev.name)}</b> · 첫 보도 24시간 뒤`, null, 3000);
    if (key) {
      await select(key);
      if (ask) { $("askInput").value = `${key.slice(2)} 왜 많이 나와?`; $("askForm").requestSubmit(); }
    } else toast(`이 캐시에서 '${esc(ev.match.join(", "))}' 키를 찾지 못했습니다.`);
  } finally {
    $("btnDemo").disabled = false;
  }
}

/* ------------------------------------------------------------------ settings */
function syncSettings() {
  $("cfgK").value = S.k;
  $("cfgThin").value = S.thin;
  $("cfgVariant").innerHTML = S.info.variants.map((v) => `<option value="${esc(v.name)}"${v.name === S.variant ? " selected" : ""}>${v.theta == null ? "정확 (θ 없음)" : `θ = ${v.theta}`} · ${esc(v.range.start)}~${esc(v.range.end)} · 스냅샷 ${v.snapshots}개</option>`).join("");
  $("cfgVariantHint").textContent = S.info.variants.length > 1 ? "근사 캐시를 고르면 순위표에 정확 캐시와 비교한 '정확' 열이 생깁니다" : "지금은 캐시가 하나뿐입니다";
  const e = S.info.engine_config, c = S.info.config;
  const rows = [
    ["반감기", (e.windows || []).map((w) => `${w[0]}`).join(" / ")],
    ["θ", c.theta == null ? "없음 (정확)" : c.theta],
    ["Top M (후보)", fmt(e.top_m)],
    ["엔진 스냅샷", `${e.snapshot_every_minutes}분마다 · 저장 ${c.store_every_hours}시간마다`],
    ["신규 이슈", `lift Top ${e.new_k} · 72h 기사 ≥ ${e.new_min_articles} · 언론사 ≥ ${e.new_min_outlets} · 준비 ${e.new_warmup_days}일`],
    ["반짝·꾸준", `F = ${e.trend_factor} · 준비 ${e.trend_warmup_days}일 · α = ${e.alpha}`],
    ["빈 구간 기준", pct(e.gap_limit, 0)],
    ["근거 기사", `Top ${e.evidence_top} 진입 후 · 하루 ${e.evidence_per_day_cap}건`],
    ["DGIM", `N = ${fmt(c.dgim_n)}`],
    ["캐시", `${S.info.built_utc} · ${fmt(S.info.build_seconds)}초 · peak ${fmt(S.info.peak_rss_mb)} MB`],
  ];
  $("cfgEngine").innerHTML = rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("");
}
async function applySettings() {
  const k = Number($("cfgK").value), thin = Number($("cfgThin").value), v = $("cfgVariant").value;
  if (!(k >= 5 && k <= 20)) { $("settingsError").textContent = "K는 5~20"; return; }
  if (!(thin >= 0 && thin <= 50)) { $("settingsError").textContent = "근거 얇음 기준은 0~50"; return; }
  S.k = k; S.thin = thin;
  const changed = v && v !== S.variant;
  if (changed) await switchVariant(v);
  else if (S.last) await go(S.idx);
  $("settingsDlg").close();
  toast(changed ? `캐시 변경: <b>${esc(v)}</b>` : "설정을 적용했습니다.", null, 2200);
}
async function switchVariant(v) {
  const t = S.times[S.idx];
  S.variant = v;
  S.info = await api("/api/gkg/info", { v });
  S.times = S.info.snapshots;
  buildTimeline();
  fillPresets();
  await go(idxFor(t, false));
}

/* ------------------------------------------------------------------ agent chat */
function formatAnswer(text, cites) {
  const byId = new Map((cites || []).map((c) => [String(c.idx), c]));
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[#(\d+)\]/g, (m, id) => {
    const c = byId.get(id);
    if (!c || !c.valid) return `<span class="cite bad" title="저장된 근거 기사에 없음">[#${id}]</span>`;
    const u = safeUrl(c.url);
    return u ? `<a class="cite" href="${esc(u)}" target="_blank" rel="noopener noreferrer" title="${esc(fmtT(c.ts))} · ${esc(c.domain)} · ${esc(c.title)}">[#${id}]</a>`
      : `<span class="cite" title="${esc(c.title)}">[#${id}]</span>`;
  });
  let html = "", inList = false;
  for (const raw of String(text || "").split("\n")) {
    const m = /^\s*-\s+(.*)$/.exec(raw);
    if (m) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(m[1])}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (raw.trim()) html += `<p>${inline(raw)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}
function renderSuggestions() {
  const st = S.last;
  if (!st) return;
  const top = (st.tab === "major" ? st.rows : []).slice(0, 2).map((r) => `${r.name} 왜 많이 나와?`);
  const b = st.burst.rows[0];
  const qs = [...top, "지금 가장 큰 이슈는 뭐야?"];
  if (b) qs.push(`${b.name} 왜 갑자기 늘었어?`);
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
  const pending = addBubble("bot", '<p class="empty"><span class="spinner"></span>도구를 호출해 목록과 근거 기사를 확인하는 중…</p>');
  try {
    const r = await api("/api/gkg/ask", {}, { question: q, t: S.times[S.idx], key: S.selected, v: S.variant });
    const trace = (r.trace || []).map((t) => `<span class="chip" title="${esc(JSON.stringify(t.args))}">${esc(t.tool)}(${esc(Object.values(t.args || {}).join(", "))})</span>`).join("");
    const live = /^(solar|llm) \((.*)\)$/.exec(r.mode);
    const modeLabel = live ? `${live[1] === "llm" ? "LLM" : "Solar"} 실시간 답변 · ${live[2]}`
      : r.mode === "offline" ? "오프라인 요약 (모델 미사용)" : "오프라인 요약 (모델 대체)";
    pending.innerHTML = `
      <div class="asof"><span>기준 시점 ${esc(r.as_of)}</span><span>${esc(modeLabel)}</span>
        ${r.verified ? '<span class="ok" title="인용한 기사 idx가 모두 도구 조회 결과에 있음"><svg><use href="#i-check"/></svg>근거 검증 통과</span>' : ""}</div>
      ${r.warning ? `<p class="warn"><svg><use href="#i-alert"/></svg><span>${esc(r.warning)}</span></p>` : ""}
      <div class="text">${formatAnswer(r.answer, r.citations)}</div>
      <details><summary>도구 호출 ${(r.trace || []).length}회 보기</summary><div class="trace">${trace}</div></details>`;
    if (r.key && r.key !== S.selected) select(r.key, false);
  } catch (e) {
    pending.innerHTML = `<p class="warn"><svg><use href="#i-alert"/></svg><span>${esc(e.message)}</span></p>`;
  } finally {
    btn.disabled = false;
    $("chat").scrollTop = $("chat").scrollHeight;
  }
}

/* ------------------------------------------------------------------ share link & CSV */
function linkParams(key) {
  const p = new URLSearchParams();
  p.set("t", S.times[S.idx]);
  if (S.tab !== "major") p.set("tab", S.tab);
  if (S.kind !== "all") p.set("kind", S.kind);
  if (S.view !== "table") p.set("view", S.view);
  if (key || S.selected) p.set("key", key || S.selected);
  if (S.variant && S.variant !== S.info.default_variant) p.set("v", S.variant);
  if (S.k !== 20) p.set("k", S.k);
  if (S.compare.length) S.compare.forEach((c) => p.append("cmp", c));
  return p;
}
function copyLink(key) {
  const url = `${location.origin}${location.pathname}?${linkParams(key)}`;
  navigator.clipboard?.writeText(url).then(() => toast(`링크를 복사했습니다 <span class="tt-mute">${esc(url.slice(url.indexOf("?")))}</span>`, null, 2600))
    .catch(() => toast(esc(url), null, 6000));
}
function downloadCsv() {
  const st = S.last;
  if (!st) return;
  const head = ["tab", "group", "rank", "prev_rank", "key", "kind", "score", "rate_per_hour", "rate_168h", "rate_720h", "ratio", "lift",
    "articles_72h", "outlets_72h", "evidence_articles", "label", "gap_ratio", "exact_rank", "dgim_estimate", "dgim_window"];
  const q = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const rows = st.tab === "trend" ? st.rows.filter((r) => r.rank <= Math.min(S.k, 10)) : st.rows.slice(0, S.k);
  const lines = [head.join(",")].concat(rows.map((r) => [st.tab, r.group || "", r.rank, r.prev_rank, r.key, r.kind, r.score, r.rate, r.rate_168h, r.rate_720h,
    r.ratio, r.lift, r.articles_72h, r.outlets, r.evidence, r.label, r.gap, r.exact_rank, r.dgim ? r.dgim.estimate : "", r.dgim ? r.dgim.window : ""].map(q).join(",")));
  const meta = `# Trend Pulse ${S.info.version} · ${iso(st.t)} · tab=${st.tab} kind=${st.kind} variant=${st.variant} status=${st.status} k=${S.k} · cache ${S.info.fingerprint.slice(0, 12)}`;
  const blob = new Blob(["﻿", meta, "\n", lines.join("\n"), "\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `trendpulse_gkg_${st.tab}_${st.kind}_${iso(st.t).slice(0, 13).replace("T", "_")}h.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast(`CSV 저장: <b>${esc(a.download)}</b>`, null, 2200);
}

/* ------------------------------------------------------------------ views, tabs */
function setView(v) {
  S.view = v;
  $("rankView").querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
  $("viewTable").hidden = v !== "table";
  $("viewRace").hidden = v !== "race";
  $("viewCompare").hidden = v !== "compare";
  if (!S.last) return;
  if (v === "race") { if (S.last.race) renderRace(S.last.race); else go(S.idx); }
  if (v === "compare") renderCompare();
}
function syncTabs() {
  $("tabs").querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === S.tab)));
  $("kinds").querySelectorAll("[data-kind]").forEach((x) => x.setAttribute("aria-checked", String(x.dataset.kind === S.kind)));
}
function seg(el, attr, onPick) {
  el.addEventListener("click", (e) => {
    const b = e.target.closest(`[${attr}]`);
    if (!b) return;
    el.querySelectorAll(`[${attr}]`).forEach((x) => x.setAttribute(x.getAttribute("role") === "tab" ? "aria-selected" : "aria-checked", String(x === b)));
    onPick(b.getAttribute(attr));
  });
}

/* ------------------------------------------------------------------ wiring */
function bind() {
  bindTimeline();
  $("btnPlay").addEventListener("click", () => togglePlay());
  $("btnStep").addEventListener("click", () => step(1));
  $("btnReset").addEventListener("click", () => { togglePlay(false); go(0); });
  $("btnDemo").addEventListener("click", () => demo(true));
  $("btnTheme").addEventListener("click", cycleTheme);
  $("btnShare").addEventListener("click", () => copyLink());
  $("btnCsv").addEventListener("click", downloadCsv);
  $("btnHelp").addEventListener("click", () => $("helpDlg").showModal());
  $("btnHelpClose").addEventListener("click", () => $("helpDlg").close());
  $("btnHelpOk").addEventListener("click", () => { $("helpDlg").close(); try { localStorage.setItem("tpg-help-seen", "1"); } catch (e) { /* ignore */ } });
  $("btnSettings").addEventListener("click", () => { syncSettings(); $("settingsError").textContent = ""; $("settingsDlg").showModal(); });
  $("btnSettingsClose").addEventListener("click", () => $("settingsDlg").close());
  $("btnSettingsCancel").addEventListener("click", () => $("settingsDlg").close());
  $("btnSettingsApply").addEventListener("click", () => applySettings().catch((e) => { $("settingsError").textContent = e.message; }));
  $("settingsForm").addEventListener("submit", (e) => { e.preventDefault(); applySettings(); });
  document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => { $("cfgK").value = 20; $("cfgThin").value = 0; $("cfgVariant").value = S.info.default_variant; }));
  seg($("speed"), "data-v", (v) => { S.speed = Number(v); });
  seg($("rankView"), "data-view", setView);
  seg($("tabs"), "data-tab", (v) => { S.tab = v; go(S.idx); });
  seg($("kinds"), "data-kind", (v) => { S.kind = v; go(S.idx); });
  seg($("cmpField"), "data-f", (v) => { S.cmpField = v; renderCompare(); });
  seg($("eventFilter"), "data-f", (v) => { S.eventFilter = v; if (S.last) renderEvents(S.last.events); });
  seg($("memWin"), "data-w", (v) => { S.memWin = v; renderMemory(); });
  $("preset").addEventListener("change", () => {
    const v = $("preset").value;
    $("customDate").hidden = v !== "custom";
    if (v === "" || v === "custom") return;
    const ev = S.info.events[Number(v)];
    seekTime(Date.parse(ev.time) / 1000, ev.name, true);
  });
  $("customDate").addEventListener("change", () => { const d = $("customDate").value; if (d) seekTime(dayStart(d), d, true); });
  const rowClick = (e) => {
    const tr = e.target.closest("tr[data-kw]");
    if (!tr) return;
    if (e.shiftKey) toggleCompare(tr.dataset.kw); else select(tr.dataset.kw);
  };
  $("rankBody").addEventListener("click", rowClick);
  $("rankBody").addEventListener("keydown", (e) => { if (e.key === "Enter") rowClick(e); });
  $("events").addEventListener("click", (e) => { const li = e.target.closest("li[data-kw]"); if (li) select(li.dataset.kw); });
  $("burst").addEventListener("click", (e) => { const li = e.target.closest("li[data-kw]"); if (li) select(li.dataset.kw); });
  $("detailBody").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-kw]");
    if (chip) return select(chip.dataset.kw);
    const ask = e.target.closest("[data-ask]");
    if (ask) {
      $("askInput").value = `${ask.dataset.ask.slice(2)} 왜 많이 나와?`;
      $("askForm").requestSubmit();
      if (window.innerWidth <= 1180) openDrawer(false);
      $("agentCard").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const cmp = e.target.closest("[data-cmp]");
    if (cmp) { toggleCompare(cmp.dataset.cmp); select(cmp.dataset.cmp, false); }
  });
  $("btnDrawerClose").addEventListener("click", () => openDrawer(false));
  $("drawerBackdrop").addEventListener("click", () => openDrawer(false));
  $("lookup").addEventListener("submit", (e) => { e.preventDefault(); lookup($("lookupInput").value).catch((err) => toast(esc(err.message))); });
  $("lookupInput").addEventListener("input", suggestKeys);
  $("askForm").addEventListener("submit", askAgent);
  $("suggest").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) { $("askInput").value = b.dataset.q; $("askForm").requestSubmit(); } });
  $("btnClearChat").addEventListener("click", () => { $("chat").querySelectorAll(".bubble").forEach((b) => b.remove()); $("chatEmpty").hidden = false; $("btnClearChat").hidden = true; renderSuggestions(); });
  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) || document.querySelector("dialog[open]");
    if (e.key === "Escape") { openDrawer(false); hideTip(); return; }
    if (typing) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
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
      if (S.info) { buildTimeline(); if (S.last) updateTimeline(S.last.t); }
      if (S.last) { if (S.view === "race") renderRace(S.last.race); if (S.view === "compare") renderCompare(); }
      if (!$("drawer").classList.contains("open")) $("drawerBackdrop").hidden = true;
    }, 150);
  });
}

async function init() {
  bind();
  const params = new URLSearchParams(location.search);
  if (params.get("v")) S.variant = params.get("v");
  S.info = await api("/api/gkg/info").catch(() => { S.variant = null; return api("/api/gkg/info"); });
  S.variant = S.info.variant;
  S.times = S.info.snapshots;
  if (params.get("tab") && TAB_KO[params.get("tab")]) S.tab = params.get("tab");
  if (params.get("kind") && ["all", "P", "O", "L"].includes(params.get("kind"))) S.kind = params.get("kind");
  const k = Number(params.get("k"));
  if (k >= 5 && k <= 20) S.k = k;
  S.compare = params.getAll("cmp").filter((c) => /^[POL]:/.test(c)).slice(0, 3);
  syncTabs();
  $("brandSub").textContent = `GDELT GKG 2.1 영어 기사 ${compact(S.info.articles)}건 · ${S.info.range.start} ~ ${S.info.range.end} · 6시간 스냅샷 ${S.times.length}개`;
  const lp = S.info.llm || {};
  $("agentMode").textContent = lp.enabled ? `${lp.label} · ${lp.model}${lp.host ? ` · ${lp.host.replace(/^https?:\/\//, "")}` : ""}` : "오프라인 요약 (API 키 없음)";
  if (lp.note) $("agentMode").title = lp.note;
  $("agentMode").classList.toggle("good", S.info.solar_enabled);
  buildTimeline();
  fillPresets();
  api("/api/gkg/e1").then((e1) => { S.e1 = e1; renderMemory(); }).catch(() => {});
  if (params.get("theme")) document.documentElement.dataset.theme = params.get("theme");
  if (params.get("view")) setView(params.get("view"));
  const t = Number(params.get("t"));
  await go(t ? idxFor(t, false) : S.times.length - 1);
  renderSuggestions();
  try { if (!localStorage.getItem("tpg-help-seen") && ![...params.keys()].length) $("helpDlg").showModal(); } catch (e) { /* ignore */ }
  const key = params.get("key");
  if (key) await select(key, true);
  else if (S.last.rows.length) await select(S.last.rows[0].key, false);
  const q = params.get("q");
  if (q) { $("askInput").value = q; $("askForm").requestSubmit(); }
  if (params.get("demo")) demo(params.get("ask") !== "0");
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<p class="empty" style="padding:12px 24px;color:var(--bad-ink)">초기화 실패: ${esc(e.message)}</p>`); });
