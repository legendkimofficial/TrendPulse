// Trend Pulse — 1st presentation deck generator (v0.4).
// Usage:  npm install pptxgenjs@3.12.0  →  node build.js ../TrendPulse_1차발표_v0.4.pptx
// Images in ./img are light-theme screenshots of the v0.4 dashboard at http://127.0.0.1:5000/?demo=1 (device scale 2).
const path = require("path");
const pptxgen = require("pptxgenjs");
let applyTheme = null;
try { ({ applyTheme } = require(process.env.APPLY_THEME || "./apply_theme.js")); } catch (e) { /* optional: only writes theme palette names */ }

const SHOTS = path.join(__dirname, "img") + path.sep;
const OUT = process.argv[2] || path.join(__dirname, "..", "TrendPulse_1차발표_v0.4.pptx");

// ---- palette: taken from web/static/style.css of the v0.4 dashboard
const P = {
  purple: "6250D6", purpleInk: "4A3AA7", purpleSoft: "EEEBFB", purpleDark: "9085E9",
  ink: "141622", ink2: "4B5162", muted: "7B8194", border: "E3E5ED", page: "F4F5F9", white: "FFFFFF",
  orange: "EB6834", orangeInk: "B4451A", orangeSoft: "FDEEE7", aqua: "1BAF7A",
  dark: "0E0F14", darkSurface: "15171E", darkText: "E9EAF0", darkMuted: "B4B8C6", ctx: "B9BDCA",
};
const THEME = {
  name: "Trend Pulse",
  headFontFace: "Arial", bodyFontFace: "Arial",
  colors: { dk1: P.ink, lt1: P.white, dk2: P.purpleInk, lt2: P.page,
    accent1: P.purple, accent2: P.orange, accent3: P.aqua, accent4: P.muted, accent5: P.purpleSoft, accent6: P.border,
    hlink: P.purpleInk, folHlink: P.purpleInk },
};
const MONO = "Consolas";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Trend Pulse - first presentation (v0.4)";
pres.author = "Team cloudnine";

const FOOT = { x: 0.6, y: 7.02, w: 6, h: 0.3, fontSize: 10, color: P.muted, margin: 0 };
pres.defineSlideMaster({
  title: "LIGHT", background: { color: P.white },
  objects: [
    { text: { text: "Trend Pulse  ·  Team cloudnine", options: { ...FOOT } } },
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.45, w: 12.1, h: 0.8, fontSize: 28, bold: true, color: P.ink, margin: 0, valign: "top", align: "left" } } },
  ],
  slideNumber: { x: 12.1, y: 7.02, w: 0.6, h: 0.3, fontSize: 10, color: P.muted, align: "right", margin: 0 },
});
pres.defineSlideMaster({ title: "DARK", background: { color: P.dark }, objects: [] });

// ---- helpers (fresh option objects every call: pptxgenjs mutates them)
const T = (slide, text, o) => slide.addText(text, { isTextBox: true, fontSize: 14, color: P.ink2, margin: 0, valign: "top", ...o });
const box = (slide, x, y, w, h, fill, o = {}) => slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: o.line || fill, width: 0.75 }, rectRadius: 0.12 });
const stat = (slide, x, y, w, big, small, o = {}) => {
  T(slide, big, { x, y, w, h: 0.75, fontSize: o.size || 40, bold: true, color: o.color || P.purple, fontFace: MONO });
  T(slide, small, { x, y: y + 0.78, w, h: 0.7, fontSize: 12.5, color: P.ink2 });
};
const shadow = () => ({ type: "outer", color: "000000", blur: 6, offset: 1, angle: 90, opacity: 0.18 });
// pulse-line logo: rounded purple square + polyline
function logo(slide, x, y, s, bg, fg) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: s, h: s, fill: { color: bg }, line: { color: bg, width: 0 }, rectRadius: s * 0.24 });
  const pts = [[0.12,0.56],[0.31,0.56],[0.40,0.31],[0.56,0.75],[0.65,0.56],[0.88,0.56]].map(([a,b]) => ({ x: a*s, y: b*s }));
  slide.addShape(pres.shapes.CUSTOM_GEOMETRY, { x, y, w: s, h: s, points: pts, fill: { type: "none" }, line: { color: fg, width: s * 9, cap: "round" } });
}
function pulse(slide, x, y, w, h, color, width) {
  const pts = [[0,0.55],[0.26,0.55],[0.33,0.12],[0.42,0.92],[0.48,0.40],[0.53,0.55],[1,0.55]].map(([a,b]) => ({ x: a*w, y: b*h }));
  slide.addShape(pres.shapes.CUSTOM_GEOMETRY, { x, y, w, h, points: pts, fill: { type: "none" }, line: { color, width, cap: "round" } });
}
const title = (slide, text) => slide.addText(text, { placeholder: "title" });

// Speaker notes = the English script (docs/1차발표_대본_v0.4.md). [optional] sentences can be skipped if time is short.
const NOTES = [
  "0:00-0:15\nHello everyone. We are team cloudnine, and this is Trend Pulse. In one sentence: Trend Pulse reads a stream of news headlines and tells you what the news is talking about right now, without a list of keywords.",
  "0:15-0:50\nThere are two easy ways to find hot topics. One: write a list of keywords and count them. Simple, but somebody has to write the list. Nobody had the word \"scalia\" on a list before Justice Scalia died. Two: count every word, forever. That finds everything, but the memory never stops growing. So we want three things: new topics with no list, a small memory, and a clear picture of what we lose by keeping it small.",
  "0:50-1:35\nThis is how it works. It is the exponentially decaying window from week four. When a headline arrives, we split it into words and two-word phrases, like \"supreme court\". Every day, all scores shrink a little. After seven days a score is half of what it was. Words in the new headline get one point. The important step is the last one. When a score falls below 0.5, we delete the word. That is where we save memory. And we can make a promise: a deleted word loses at most one point. To check this, we also run an exact version that never deletes anything. It is only there to measure our errors.",
  "1:35-2:00\nOur data is about two hundred thousand HuffPost headlines, from 2012 to 2022. We replay them in date order, one day at a time. Two things to remember. The data has dates but no time of day, so this is a replay, not live news. And after 2018, HuffPost publishes only about five headlines a day. This matters later.",
  "2:00-2:50\nLet me show the dashboard. This is February 16, 2016, three days after Justice Scalia died. On the left is the Top 20. \"scalia\" is at rank 12, and \"supreme court\" is at rank 8. Nobody told the system about this event. Now look at the small \"almost equal\" sign next to \"gop\" and \"scalia\". It means the two scores are so close that saving memory may have swapped them. And it did: the exact ranks are 12 and 11. [optional] We do not hide our errors; we mark them. On the right are the bursts: words growing much faster than usual. \"scalia\" is growing two times faster than normal.",
  "2:50-3:30\nSo, what do we lose by saving memory? Left is our ranking, right is the exact version. We store 93 percent fewer keywords. Still, 99.9 percent of the time, the Top 20 has the same members as the exact version. The order is exactly the same in 89 percent of the snapshots. The other 11 percent are small swaps between neighbours with almost equal scores, like gop and scalia. We mark every one of them. [optional] In about 38 thousand pairs, we missed none.",
  "3:30-4:05\nDoes it find real events? We took 26 real events, like Hurricane Sandy, Brexit and the Notre-Dame fire, and asked: does the word enter the Top 20 within seven days? Our first version faded scores by article count and found 16 of 26. When we changed it to fade by day, it found 23. The difference comes from the thin years after 2018, where old news stays in the ranking for months. Ten of the events were written down before the test, so we could not cheat. [optional] And the burst list is even faster: 24 of 26.",
  "4:05-4:30\nWhat is not solved yet? After 2018, many Top 20 words have only one or two headlines behind them. We now show that count and grey out thin rows, but the problem is still there. Also, this is only HuffPost, not the whole world. Before the second presentation, we will test our explaining agent with the real API, test on random dates, and try longer phrases.",
  "4:30-4:40\nThat is Trend Pulse: small memory, new topics, and we can say exactly what we got wrong. Thank you. We are happy to take questions.",
];
let noteIdx = 0;
const origAddSlide = pres.addSlide.bind(pres);
pres.addSlide = (opts) => { const s = origAddSlide(opts); if (NOTES[noteIdx]) s.addNotes(NOTES[noteIdx]); noteIdx++; return s; };

// =============================================================== 1 Title
pres.addSection({ title: "Opening" });
{
  const s = pres.addSlide({ masterName: "DARK", sectionTitle: "Opening" });
  pulse(s, 7.0, 4.3, 7.0, 2.4, "272449", 10);
  logo(s, 0.9, 1.55, 0.95, P.purpleDark, P.dark);
  T(s, "Trend Pulse", { x: 0.9, y: 2.7, w: 10, h: 1.1, fontSize: 54, bold: true, color: P.white });
  T(s, "Finding what the news is talking about, without a keyword list", { x: 0.9, y: 3.75, w: 9.5, h: 0.6, fontSize: 22, color: P.darkMuted });
  T(s, [
    { text: "Introduction to Big Data  ·  Group project  ·  First presentation", options: { breakLine: true } },
    { text: "Team cloudnine:  노상현 · 김현서 · 김태형 · 이현호", options: { breakLine: true } },
    { text: "Prototype v0.4  ·  November 2026" },
  ], { x: 0.9, y: 5.35, w: 9, h: 1.3, fontSize: 14, color: P.darkMuted, paraSpaceAfter: 6 });
}

// =============================================================== 2 The question
pres.addSection({ title: "Why" });
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Why" });
  title(s, "What is the news talking about right now?");
  T(s, "Two easy answers, and why we did not take them", { x: 0.6, y: 1.2, w: 10, h: 0.4, fontSize: 15, color: P.muted });

  const cardY = 1.95, cardH = 3.0, cw = 5.75;
  [[0.6, "Keep a list of keywords and count them",
      "Simple and tiny. But somebody has to write the list first.",
      "Nobody had \u201cscalia\u201d on a list before February 13, 2016. A list only finds topics you already expected."],
   [7.0, "Count every word, forever",
      "Finds everything. But the memory never stops growing.",
      "By 2016 the exact count holds about 43,000 words. Most of them are from years ago and will never be hot again."]
  ].forEach(([x, h, l1, l2]) => {
    box(s, x, cardY, cw, cardH, P.page);
    T(s, h, { x: x + 0.4, y: cardY + 0.35, w: cw - 0.8, h: 0.8, fontSize: 19, bold: true, color: P.ink });
    T(s, l1, { x: x + 0.4, y: cardY + 1.2, w: cw - 0.8, h: 0.6, fontSize: 14.5, color: P.ink2 });
    T(s, l2, { x: x + 0.4, y: cardY + 1.95, w: cw - 0.8, h: 0.9, fontSize: 14.5, color: P.orangeInk });
  });
  box(s, 0.6, 5.35, 12.15, 1.15, P.purpleSoft);
  T(s, [
    { text: "What we want: ", options: { bold: true, color: P.purpleInk } },
    { text: "find new topics with no list, keep the memory small, and know exactly what we lose by keeping it small.", options: { color: P.ink } },
  ], { x: 1.0, y: 5.35, w: 11.4, h: 1.15, fontSize: 17, valign: "middle" });
}

// =============================================================== 3 How it works
pres.addSection({ title: "How" });
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "How" });
  title(s, "How a word gets its score");
  T(s, "An exponentially decaying window with pruning (week 4), applied to every word and two-word phrase in a headline", { x: 0.6, y: 1.2, w: 12, h: 0.4, fontSize: 15, color: P.muted });

  const steps = [
    ["1", "A headline arrives", "Split it into words and two-word phrases, such as \u201csupreme court\u201d."],
    ["2", "Everything fades", "All scores shrink a little every day. After 7 days a score is half of what it was."],
    ["3", "New words get +1", "Each word in the new headline gets one point. Hot words climb; quiet words sink."],
    ["4", "Forget the small ones", "A score below \u03b8 = 0.5 is deleted. This step is where the memory is saved."],
  ];
  const sx = 0.6, sy = 1.95, sw = 2.85, sh = 2.25, gap = 0.25;
  steps.forEach(([n, h, body], i) => {
    const x = sx + i * (sw + gap);
    box(s, x, sy, sw, sh, P.white, { line: P.border });
    s.addShape(pres.shapes.OVAL, { x: x + 0.3, y: sy + 0.3, w: 0.42, h: 0.42, fill: { color: P.purple }, line: { color: P.purple, width: 0 } });
    T(s, n, { x: x + 0.3, y: sy + 0.3, w: 0.42, h: 0.42, fontSize: 13, bold: true, color: P.white, align: "center", valign: "middle", fontFace: MONO });
    T(s, h, { x: x + 0.3, y: sy + 0.88, w: sw - 0.6, h: 0.4, fontSize: 15.5, bold: true, color: P.ink });
    T(s, body, { x: x + 0.3, y: sy + 1.3, w: sw - 0.6, h: 0.9, fontSize: 12.5, color: P.ink2 });
  });

  const by = 4.5, bh = 2.1;
  box(s, 0.6, by, 5.95, bh, P.purpleSoft);
  T(s, "What we can promise", { x: 1.0, y: by + 0.3, w: 5.2, h: 0.4, fontSize: 15.5, bold: true, color: P.purpleInk });
  T(s, [
    { text: "A deleted word can lose at most  ", options: { color: P.ink } },
    { text: "\u03b8 / (1 \u2212 \u03b8) = 1 point", options: { fontFace: MONO, color: P.purpleInk, bold: true } },
    { text: ".  So when the screen says 20.2, the true score is between 20.2 and 21.2. Nothing is lost silently.", options: { color: P.ink } },
  ], { x: 1.0, y: by + 0.75, w: 5.2, h: 1.25, fontSize: 13.5 });
  box(s, 6.8, by, 5.95, bh, P.page);
  T(s, "How we check ourselves", { x: 7.2, y: by + 0.3, w: 5.2, h: 0.4, fontSize: 15.5, bold: true, color: P.ink });
  T(s, "Next to the small version we run an exact version that never deletes anything. It is only there to measure our errors. We also use DGIM as a helper, to count one chosen word over the last N articles.", { x: 7.2, y: by + 0.75, w: 5.2, h: 1.25, fontSize: 13.5, color: P.ink2 });
}

// =============================================================== 4 Data
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "How" });
  title(s, "Ten years of HuffPost headlines, replayed day by day");
  stat(s, 0.6, 1.5, 3.6, "209,480", "headlines, January 2012 to September 2022, in date order");
  stat(s, 4.6, 1.5, 3.6, "2 kinds", "of keys: single words and two-word phrases");
  stat(s, 8.6, 1.5, 4.1, "26 events", "real-world events used as test cases, 10 of them chosen before the test");

  box(s, 0.6, 3.35, 12.15, 2.0, P.page);
  T(s, "Headlines per month, and where the 26 test events sit on the timeline", { x: 1.0, y: 3.6, w: 11, h: 0.35, fontSize: 13, bold: true, color: P.ink });
  s.addImage({ path: SHOTS + "timeline.png", x: 0.95, y: 4.05, w: 11.45, h: 11.45 * 136 / 2792 });
  T(s, [
    { text: "\u25c7 orange", options: { color: P.orangeInk, bold: true } }, { text: " = events we had from the start       " },
    { text: "\u25c7 grey", options: { color: P.ink2, bold: true } }, { text: " = events registered before this round       " },
    { text: "the purple area is the number of headlines per month", options: {} },
  ], { x: 1.0, y: 4.8, w: 11.5, h: 0.4, fontSize: 12.5, color: P.ink2 });

  box(s, 0.6, 5.6, 12.15, 1.15, P.purpleSoft);
  T(s, [
    { text: "Two things to keep in mind. ", options: { bold: true, color: P.purpleInk } },
    { text: "The data has dates but no time of day, so this is a replay of the past, not live collection. And after 2018 the stream is thin, which changes some results later.", options: { color: P.ink } },
  ], { x: 1.0, y: 5.6, w: 11.4, h: 1.15, fontSize: 14.5, valign: "middle" });
}

// =============================================================== 5 Dashboard
pres.addSection({ title: "Demo" });
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Demo" });
  title(s, "February 16, 2016: three days after Justice Scalia died");
  T(s, "The v0.4 dashboard, paused at that date. Left: the Top 20. Right: words that are growing faster than usual.", { x: 0.6, y: 1.2, w: 12, h: 0.4, fontSize: 15, color: P.muted });
  const iw = 6.9, ih = iw * 1290 / 1764;
  s.addImage({ path: SHOTS + "rank_top13.png", x: 0.6, y: 1.75, w: iw, h: ih, shadow: shadow() });
  const bx = 7.85, bw = 4.9, bh = bw * 408 / 935;
  T(s, "Bursts: words growing faster than usual", { x: bx, y: 1.75, w: bw, h: 0.3, fontSize: 12, bold: true, color: P.orangeInk });
  s.addImage({ path: SHOTS + "burst_top4.png", x: bx, y: 2.1, w: bw, h: bh, shadow: shadow() });

  const notes = [
    ["\u2248", "\u201cgop\u201d and \u201cscalia\u201d have almost the same score. Saving memory may have swapped them, and it did: the exact ranks (\uc815\ud655) are 12 and 11."],
    ["\uadfc\uac70", "Evidence: how many headlines in the last 11 days contain the word. A rank with only one or two headlines is greyed out."],
    ["\u00d72.2", "\u201cscalia\u201d and \u201csupreme court\u201d are growing about two times faster than usual. Bursts often show an event before the Top 20 does."],
  ];
  let ny = 2.1 + bh + 0.35;
  notes.forEach(([k, v]) => {
    T(s, k, { x: bx, y: ny, w: 0.75, h: 0.75, fontSize: 13, bold: true, color: P.purpleInk, fontFace: MONO });
    T(s, v, { x: bx + 0.8, y: ny, w: bw - 0.8, h: 0.75, fontSize: 11, color: P.ink2 });
    ny += 0.82;
  });
}

// =============================================================== 6 Cost of saving memory
pres.addSection({ title: "Results" });
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Results" });
  title(s, "What do we lose by saving memory?");
  T(s, "Left: our ranking. Right: the exact version that never deletes. Same day as the demo.", { x: 0.6, y: 1.2, w: 12, h: 0.4, fontSize: 15, color: P.muted });
  const ih = 5.1, iw = ih * 1764 / 1430;
  s.addImage({ path: SHOTS + "view_compare.png", x: 0.6, y: 1.75, w: iw, h: ih, shadow: shadow() });

  const rx = 7.5, rw = 5.25;
  stat(s, rx, 1.7, rw, "\u221292.9%", "stored keywords: 2,854 instead of 40,265 on average (\u03b8 = 0.5)");
  stat(s, rx, 3.25, rw, "99.9%", "of the time the Top 20 has the same members as the exact version");
  stat(s, rx, 4.8, rw, "89%", "of snapshots have exactly the same order. The rest differ by swaps like gop \u2194 scalia", { color: P.orangeInk });
  box(s, rx, 6.2, rw, 0.68, P.purpleSoft);
  T(s, "Swaps only happen between words with almost equal scores. We mark them with \u2248, and in 37,905 neighbour pairs we missed none.", { x: rx + 0.2, y: 6.2, w: rw - 0.4, h: 0.68, fontSize: 11, color: P.ink, valign: "middle" });
}

// =============================================================== 7 Real events
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Results" });
  title(s, "Does it find real events?");
  T(s, "Found = the event's word enters the Top 20 within 7 days of the event", { x: 0.6, y: 1.2, w: 12, h: 0.4, fontSize: 15, color: P.muted });

  s.addChart(pres.charts.BAR, [
    { name: "Fade by article count (v0.2)", labels: ["All 26 events", "10 events chosen in advance"], values: [16, 3] },
    { name: "Fade by day, 7-day half-life (v0.3 and later)", labels: ["All 26 events", "10 events chosen in advance"], values: [23, 9] },
  ], {
    x: 0.6, y: 1.75, w: 6.6, h: 4.9, barDir: "col", barGrouping: "clustered", barGapWidthPct: 60,
    chartColors: [P.ctx, P.purple],
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 13, dataLabelColor: P.ink, dataLabelFontFace: "+mn-lt", dataLabelFontBold: true,
    catAxisLabelColor: P.ink2, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
    valAxisHidden: true, valAxisMaxVal: 26, valAxisMinVal: 0, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showLegend: true, legendPos: "b", legendFontSize: 11, legendColor: P.ink2, legendFontFace: "+mn-lt",
    showTitle: false,
  });

  const rx = 7.6, rw = 5.15;
  T(s, "Why counting days works better", { x: rx, y: 1.75, w: rw, h: 0.4, fontSize: 15.5, bold: true, color: P.ink });
  T(s, "After 2018 HuffPost publishes about 5 headlines a day. If the score fades per 1,000 articles, that is months of old news still sitting in the Top 20. Fading per day lets a new event show up in 0 to 2 days.", { x: rx, y: 2.2, w: rw, h: 1.5, fontSize: 13, color: P.ink2 });
  T(s, "Ten events were written down before we ran the test, so we could not pick the ones that happen to work.", { x: rx, y: 3.65, w: rw, h: 0.8, fontSize: 13, color: P.ink2 });
  box(s, rx, 4.6, rw, 2.05, P.purpleSoft);
  T(s, "Bursts are even faster", { x: rx + 0.3, y: 4.85, w: rw - 0.6, h: 0.4, fontSize: 14.5, bold: true, color: P.purpleInk });
  T(s, [
    { text: "24 of 26", options: { fontFace: MONO, bold: true, color: P.purpleInk } },
    { text: " events appear in the burst list within 7 days, and in ", options: {} },
    { text: "17", options: { fontFace: MONO, bold: true, color: P.purpleInk } },
    { text: " cases they appear there before they reach the Top 20.", options: {} },
  ], { x: rx + 0.3, y: 5.3, w: rw - 0.6, h: 1.2, fontSize: 13, color: P.ink });
}

// =============================================================== 8 Open problems
pres.addSection({ title: "Next" });
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Next" });
  title(s, "What is not solved yet");
  const cy = 1.5, ch = 4.6, cw = 5.95;
  box(s, 0.6, cy, cw, ch, P.page);
  T(s, "Open problems", { x: 1.0, y: cy + 0.35, w: cw - 0.8, h: 0.4, fontSize: 17, bold: true, color: P.ink });
  T(s, [
    { text: "Thin evidence after 2018. ", options: { bold: true, color: P.ink } },
    { text: "About 3 of 4 words in the Top 20 have two or fewer headlines in the last week. v0.4 now shows the evidence count and greys out thin rows, but the ranking itself is still thin.", options: { breakLine: true } },
    { text: "Only HuffPost. ", options: { bold: true, color: P.ink } },
    { text: "The Top 20 is what one news site wrote about, not what the whole world cared about.", options: { breakLine: true } },
    { text: "The explaining agent is untested with the real model. ", options: { bold: true, color: P.ink } },
    { text: "The \u201cwhy is this trending?\u201d assistant works in offline mode and checks every article it cites, but we have not run it with the real Solar API yet.", options: {} },
  ], { x: 1.0, y: cy + 0.9, w: cw - 0.8, h: 3.5, fontSize: 14.5, color: P.ink2, paraSpaceAfter: 12 });

  box(s, 6.8, cy, cw, ch, P.purpleSoft);
  T(s, "Until the second presentation", { x: 7.2, y: cy + 0.35, w: cw - 0.8, h: 0.4, fontSize: 17, bold: true, color: P.purpleInk });
  T(s, [
    { text: "Run the agent with the real API and grade its answers as a team.", options: { bullet: true, breakLine: true } },
    { text: "Test on random dates, not only on famous events, so the test is fairer.", options: { bullet: true, breakLine: true } },
    { text: "Try phrases of three or more words, such as \u201cblack lives matter\u201d.", options: { bullet: true, breakLine: true } },
    { text: "Tune the burst detector for the thin years after 2018.", options: { bullet: true, breakLine: true } },
    { text: "Decide together: is \u201ctwo headlines\u201d the right line for thin evidence?", options: { bullet: true } },
  ], { x: 7.2, y: cy + 0.9, w: cw - 0.8, h: 3.5, fontSize: 14.5, color: P.ink, paraSpaceAfter: 12 });
  T(s, "The algorithm and the numbers did not change in v0.4. This round was about the dashboard: timeline, evidence column, exact comparison, keyword details.", { x: 0.6, y: 6.3, w: 12.15, h: 0.5, fontSize: 12.5, color: P.muted });
}

// =============================================================== 9 Closing
{
  const s = pres.addSlide({ masterName: "DARK", sectionTitle: "Next" });
  pulse(s, -0.5, 0.6, 8.5, 2.4, "272449", 10);
  logo(s, 0.9, 3.0, 0.7, P.purpleDark, P.dark);
  T(s, "Thank you", { x: 0.9, y: 3.85, w: 8, h: 0.9, fontSize: 40, bold: true, color: P.white });
  T(s, "Small memory, new topics, and we can say exactly what we got wrong.", { x: 0.9, y: 4.75, w: 10, h: 0.5, fontSize: 18, color: P.darkMuted });
  T(s, [
    { text: "Code, experiments and this deck:  ", options: { color: P.darkMuted } },
    { text: "github.com/legendkimofficial/TrendPulse", options: { color: P.purpleDark, fontFace: MONO } },
  ], { x: 0.9, y: 5.75, w: 11, h: 0.4, fontSize: 14 });
  T(s, "Trend Pulse  \u00b7  Team cloudnine", { x: 0.9, y: 6.2, w: 8, h: 0.35, fontSize: 12, color: P.darkMuted });
}

(async () => {
  await pres.writeFile({ fileName: OUT });
  if (applyTheme) await applyTheme(OUT, THEME);
  console.log("written", OUT);
})().catch(e => { console.error(e); process.exit(1); });
