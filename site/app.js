/* Checkout reviews tracker — page script.
 *
 * Every number shown is computed in Python (tracker/aggregate.py) and shipped
 * in data.json. This file only slices those arrays to the chosen time range,
 * sums them for the "signals" cards, and draws them. Review text is untrusted,
 * so it is only ever inserted with textContent, never innerHTML.
 */
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  const state = { weeks: 0, latestApp: "all", data: null, charts: [] };
  const $ = (sel) => document.querySelector(sel);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SENTS = ["positive", "neutral", "negative"];
  const SENT_LABEL = { positive: "Positive", neutral: "Neutral", negative: "Negative" };
  const NEXT_RUN_UTC = { h: 3, m: 17 };   // matches the cron in .github/workflows/tracker.yml

  // ---- small DOM helpers ------------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "style") node.setAttribute("style", v);
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const svgNS = "http://www.w3.org/2000/svg";
  function icon(kind) {
    // status icons: good = check, warning = triangle, critical = cross
    const s = document.createElementNS(svgNS, "svg");
    s.setAttribute("viewBox", "0 0 16 16");
    s.setAttribute("class", "ico");
    s.setAttribute("aria-hidden", "true");
    const color = { good: css("--good"), warning: css("--warning"), critical: css("--critical") }[kind];
    const paths = {
      good: ['<circle cx="8" cy="8" r="7" fill="C"/>', '<path d="M4.8 8.3l2.1 2.1 4.3-4.6" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'],
      warning: ['<path d="M8 1.5l7 12.5H1z" fill="C"/>', '<path d="M8 6v4" stroke="#0b0b0b" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="12" r="1" fill="#0b0b0b"/>'],
      critical: ['<circle cx="8" cy="8" r="7" fill="C"/>', '<path d="M5.5 5.5l5 5m0-5l-5 5" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'],
    }[kind];
    s.innerHTML = paths.join("").replace(/C/g, color);  // static markup, no data
    return s;
  }
  function arrow(up) {
    const s = document.createElementNS(svgNS, "svg");
    s.setAttribute("viewBox", "0 0 12 12");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = up ? '<path d="M6 10V2M2.5 5.5L6 2l3.5 3.5" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
                     : '<path d="M6 2v8M2.5 6.5L6 10l3.5-3.5" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
    return s;
  }
  const appBy = (slug) => state.data.apps.find((a) => a.slug === slug);
  const appColor = (slug) => css(`--app-${slug}`) || appBy(slug).color;
  const nf = new Intl.NumberFormat("en-IN");
  const fmtDate = (iso, opts) => new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-GB", opts || { day: "numeric", month: "short", year: "numeric" });
  const weekLabel = (iso) => fmtDate(iso, { day: "numeric", month: "short", year: "2-digit" });
  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 60) return `${Math.max(mins, 1)} min ago`;
    const h = Math.round(mins / 60);
    if (h < 48) return `${h} h ago`;
    return `${Math.round(h / 24)} days ago`;
  }
  const pct = (n, d) => (d ? Math.round((100 * n) / d) : 0);
  const sum = (arr) => arr.reduce((a, b) => a + b, 0);
  function hexA(hex, a) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  function countUp(node, target, fmt) {
    fmt = fmt || ((v) => nf.format(Math.round(v)));
    if (reduceMotion || !target) { node.textContent = fmt(target); return; }
    const t0 = performance.now(), dur = 1100;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 4);
      node.textContent = fmt(target * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---- range slicing -------------------------------------------------------------
  function sliceIndex() {
    const n = state.data.weeks.length;
    return state.weeks > 0 ? Math.max(0, n - state.weeks) : 0;
  }
  const cut = (arr) => arr.slice(sliceIndex());
  const appsWithReviews = () => state.data.apps.filter((a) => a.stored_total > 0);

  // ---- Chart.js defaults from tokens ------------------------------------------------
  function chartDefaults() {
    const C = window.Chart;
    C.defaults.font.family = css("--font") || "system-ui, sans-serif";
    C.defaults.font.size = 12;
    C.defaults.color = css("--axis");
    C.defaults.borderColor = css("--grid");
    // set fields rather than replacing the object: Chart.js resolves per-property
    // animation settings (e.g. colour tweening) through this object's scope
    if (reduceMotion) C.defaults.animation = false;
    else { C.defaults.animation.duration = 800; C.defaults.animation.easing = "easeOutQuart"; }
    C.defaults.maintainAspectRatio = false;
    const t = C.defaults.plugins.tooltip;
    t.enabled = false;              // HTML tooltip instead (see externalTooltip)
    t.external = externalTooltip;
    C.defaults.plugins.legend.display = false;   // HTML legends instead
  }
  // One HTML tooltip per chart. Label callbacks return "name\tvalue[\tdetail]".
  function externalTooltip(context) {
    const { chart, tooltip } = context;
    const parent = chart.canvas.parentNode;
    let tip = parent.querySelector(":scope > .tip");
    if (!tip) { tip = el("div", { class: "tip", "aria-hidden": "true" }); parent.append(tip); }
    if (tooltip.opacity === 0 || !tooltip.dataPoints || !tooltip.dataPoints.length) { tip.classList.remove("on"); return; }
    const rows = tooltip.body.map((b, i) => {
      const dp = tooltip.dataPoints[i];
      const [name, value, detail] = b.lines.join(" ").split("\t");
      const sw = (dp && dp.dataset.swatch) || tooltip.labelColors[i].backgroundColor;
      return el("div", { class: "tip-row" }, el("i", { style: `background:${sw}` }), el("span", null, name), el("b", null, value || ""), detail ? el("em", null, detail) : null);
    });
    tip.replaceChildren(el("div", { class: "tip-title" }, (tooltip.title || []).join(" ")), ...rows);
    tip.classList.add("on");
    const pw = parent.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = chart.canvas.offsetLeft + tooltip.caretX + 14;
    if (x + tw > pw - 4) x = chart.canvas.offsetLeft + tooltip.caretX - tw - 14;
    x = Math.max(4, x);
    let y = chart.canvas.offsetTop + tooltip.caretY - th / 2;
    y = Math.max(0, Math.min(y, parent.clientHeight - th));
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  }
  // vertical hairline that snaps to the hovered week
  const crosshair = {
    id: "crosshair",
    afterDatasetsDraw(chart) {
      const active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      const x = active[0].element.x;
      const { top, bottom } = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = css("--baseline");
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();
    },
  };
  function axes(extraY) {
    return {
      x: {
        grid: { display: false },
        border: { color: css("--baseline") },
        ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7, color: css("--axis"), padding: 6 },
      },
      y: Object.assign({
        beginAtZero: true,
        grid: { color: css("--grid") },
        border: { display: false },
        ticks: { precision: 0, color: css("--axis"), maxTicksLimit: 5, padding: 8 },
      }, extraY || {}),
    };
  }
  function addChart(canvas, config) {
    const c = new window.Chart(canvas, config);
    state.charts.push(c);
    return c;
  }
  function legend(container, items, shape) {
    container.replaceChildren(...items.map((it) =>
      el("span", null, el("i", { class: shape === "line" ? "key-line" : "key-rect", style: `background:${it.color}` }), it.label)));
  }
  function table(container, head, rows) {
    const thead = el("thead", null, el("tr", null, head.map((h, i) => el("th", { class: i ? "num" : null }, h))));
    const tbody = el("tbody", null, rows.map((r) => el("tr", null, r.map((v, i) => el("td", { class: i ? "num" : null }, v)))));
    container.replaceChildren(el("table", null, thead, tbody));
  }

  // ---- status pill --------------------------------------------------------------------
  function renderStatus() {
    const runs = state.data.runs;
    const box = $("#status");
    const last = runs.last;
    if (!last) { box.textContent = "No runs yet"; return; }
    const hoursSinceOk = runs.last_ok_at ? (Date.now() - new Date(runs.last_ok_at)) / 36e5 : Infinity;
    let kind = "good", label = "Healthy";
    if (last.status === "alert" && !last.simulate) { kind = "critical"; label = "Needs attention"; }
    else if (hoursSinceOk > 36) { kind = "warning"; label = "Stale"; }
    box.replaceChildren(
      el("span", { class: "pulse", style: `--pulse:${css("--" + kind)}` }, icon(kind)),
      el("strong", null, label),
      el("span", { class: "when" }, `· updated ${ago(runs.last_ok_at || last.started_at)}`));
  }

  // ---- hero numbers ---------------------------------------------------------------------
  function nextRun() {
    const now = new Date();
    const n = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), NEXT_RUN_UTC.h, NEXT_RUN_UTC.m));
    if (n <= now) n.setUTCDate(n.getUTCDate() + 1);
    const mins = Math.max(0, Math.round((n - now) / 60000));
    return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
  }
  function renderHero() {
    const d = state.data;
    const total = d.apps.reduce((s, a) => s + a.stored_total, 0);
    const firstWeek = d.weeks.find((w, i) => d.apps.some((a) => d.series[a.slug].total[i] > 0)) || d.weeks[0];
    const mix = d.tagging || {};
    const runs = d.runs || {};
    const runCount = runs.count || (runs.recent || []).filter((r) => !r.simulate).length;
    const stat = (k, v, s, opts) => {
      const vNode = el("span", null, "0");
      const node = el("div", { class: "hero-stat" }, el("div", { class: "k" }, k), el("div", { class: "v" }, vNode, opts && opts.unit ? el("small", null, opts.unit) : null), el("div", { class: "s", title: s }, s));
      if (typeof v === "number") countUp(vNode, v); else vNode.textContent = v;
      if (opts && opts.id) vNode.id = opts.id;
      return node;
    };
    const fromStars = mix.stars || 0, byRules = mix.rules || 0;
    $("#hero-stats").replaceChildren(
      stat("Reviews tracked", total, `posted since ${fmtDate(firstWeek, { month: "short", year: "numeric" })}`),
      stat("Tagged by AI", mix.llm || 0, `${fromStars} rating-only from stars${byRules ? `, ${byRules} by rules` : ""}`),
      stat("Runs so far", runCount, runs.first_at ? `on GitHub Actions since ${fmtDate(runs.first_at.slice(0, 10))}` : "on GitHub Actions"),
      stat("Next run in", nextRun(), "every day at 08:47 IST", { id: "next-run" }));
  }

  // ---- app cards ------------------------------------------------------------------------
  function sparkline(values, color, id) {
    const W = 300, H = 48, n = values.length;
    const max = Math.max(1, ...values);
    const pts = values.map((v, i) => [n > 1 ? (i / (n - 1)) * W : 0, H - 2 - (v / max) * (H - 6)]);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
    const area = `${line}L${W},${H}L0,${H}Z`;
    const s = document.createElementNS(svgNS, "svg");
    s.setAttribute("viewBox", `0 0 ${W} ${H}`);
    s.setAttribute("preserveAspectRatio", "none");
    s.setAttribute("class", "spark");
    s.setAttribute("aria-hidden", "true");
    // numbers and a palette colour only: no data strings
    s.innerHTML = `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`
      + `<path d="${area}" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.75" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
    return s;
  }
  function renderCards() {
    const d = state.data;
    const colors = { positive: css("--sent-positive"), neutral: css("--sent-neutral"), negative: css("--sent-negative") };
    $("#app-cards").replaceChildren(...d.apps.map((a, idx) => {
      const c = appColor(a.slug);
      const head = el("div", { class: "card-head" },
        el("span", { class: "mono", "aria-hidden": "true" }, a.vendor.slice(0, 1)),
        el("div", { class: "card-title" }, el("a", { href: a.store_url, title: a.name }, a.name), el("span", null, `by ${a.vendor}`)));
      const card = el("article", { class: "card reveal", style: `--c:${c};transition-delay:${idx * 80}ms` }, head);
      if (!a.stored_total) {
        card.append(
          el("div", { class: "empty-card" },
            el("div", { class: "radar", "aria-hidden": "true" }, el("i"), el("i"), el("i"), el("b")),
            el("div", null, el("strong", null, a.listed_total === 0 ? "No reviews on the App Store yet" : "No reviews collected yet")),
            el("div", { style: "font-size:12.5px;color:var(--muted);max-width:240px" }, "The tracker checks every morning and will pick up the first one automatically.")),
          el("div", { class: "card-foot" }, el("a", { href: a.reviews_url }, "Open the App Store listing ↗")));
        return card;
      }
      const rating = a.listed_rating !== null ? a.listed_rating : a.avg_rating;
      const negShare = pct(a.last_90d_negative, a.last_90d);
      const total = a.stored_total;
      const firstWeek = d.weeks.find((w, i) => d.series[a.slug].total[i] > 0) || d.weeks[0];
      card.append(
        el("div", { class: "rating-row" },
          el("div", { class: "rating-big" }, rating.toFixed(1), el("small", null, "/ 5")),
          el("div", { style: "text-align:right" },
            el("span", { class: "star-meter", role: "img", "aria-label": `${rating.toFixed(1)} out of 5 stars` }, "★★★★★",
              el("span", { style: `width:${(rating / 5) * 100}%` }, "★★★★★")),
            el("div", { class: "rating-cap" }, `App Store rating · ${nf.format(a.listed_total ?? total)} reviews`))),
        el("div", { class: "mini-stats" },
          el("div", { class: "mini" }, el("div", { class: "k" }, "Reviews tracked"), el("div", { class: "v" }, nf.format(total))),
          el("div", { class: "mini" }, el("div", { class: "k" }, "Last 90 days"),
            el("div", { class: "v" }, String(a.last_90d), a.last_90d ? el("small", null, ` · ${negShare}% neg.`) : null))),
        el("div", null,
          el("div", { class: "sentbar", role: "img", "aria-label": SENTS.map((s) => `${pct(a.sentiments[s], total)}% ${s}`).join(", ") },
            SENTS.filter((s) => a.sentiments[s]).map((s) => el("i", { style: `width:${(100 * a.sentiments[s]) / total}%;background:${colors[s]}` }))),
          el("div", { class: "sentbar-key" }, SENTS.map((s) =>
            el("span", null, el("i", { class: "dot", style: `background:${colors[s]}` }), el("b", null, `${pct(a.sentiments[s], total)}%`), SENT_LABEL[s].toLowerCase())))),
        el("div", { class: "spark-wrap" },
          el("div", { class: "k" }, el("span", null, "Reviews per week"), el("span", null, `since ${fmtDate(firstWeek, { month: "short", year: "numeric" })}`)),
          sparkline(d.series[a.slug].total.slice(d.weeks.indexOf(firstWeek)), c, `sp-${idx}`)),
        el("div", { class: "card-foot" }, `Newest review ${fmtDate(a.latest_date)} · `, el("a", { href: a.reviews_url }, "all reviews ↗")));
      return card;
    }));
  }

  // ---- signals: computed insights ---------------------------------------------------------
  function yearly(slug) {
    const d = state.data, s = d.series[slug], by = {};
    d.weeks.forEach((w, i) => {
      const y = w.slice(0, 4);
      by[y] = by[y] || { neg: 0, total: 0 };
      by[y].neg += s.negative[i];
      by[y].total += s.total[i];
    });
    return Object.entries(by).filter(([, v]) => v.total >= 3).map(([y, v]) => ({ y, ...v, share: v.total ? v.neg / v.total : 0 }));
  }
  function renderInsights() {
    const d = state.data;
    const thisYear = d.today.slice(0, 4);
    const cards = [];
    for (const a of appsWithReviews()) {
      const ys = yearly(a.slug).slice(-3);
      if (ys.length < 2) continue;
      const cur = ys[ys.length - 1], prev = ys[ys.length - 2];
      const diff = Math.round(100 * cur.share) - Math.round(100 * prev.share);
      const maxShare = Math.max(0.01, ...ys.map((y) => y.share));
      cards.push(el("article", { class: "insight reveal", style: `transition-delay:${cards.length * 80}ms` },
        el("div", { class: "insight-k" }, el("i", { class: "dot", style: `background:${appColor(a.slug)}` }), `${a.vendor} · share of negative reviews`),
        el("div", null,
          el("div", { class: "insight-v" }, `${pct(cur.neg, cur.total)}%`, el("small", null, `in ${cur.y}${cur.y === thisYear ? " so far" : ""}`)),
          el("p", { class: "insight-t", style: "margin-top:8px" },
            diff ? el("span", { class: `delta ${diff > 0 ? "up" : "down"}` }, arrow(diff > 0), `${Math.abs(diff)} pts vs ${prev.y}`) : el("span", { class: "delta" }, `same as ${prev.y}`),
            ` ${cur.neg} of ${cur.total} reviews`)),
        el("div", { class: "ybars", role: "img", "aria-label": ys.map((y) => `${y.y}: ${pct(y.neg, y.total)}% negative`).join(", ") },
          ys.map((y) => el("div", { class: "ybar" + (y === cur ? " now" : "") },
            el("span", { class: "val" }, `${pct(y.neg, y.total)}%`),
            el("span", { class: "col", style: `height:${Math.max(3, (y.share / maxShare) * 58)}px` }),
            el("span", { class: "yr" }, y.y))))));
    }
    // why merchants complain: negative written reviews by theme, all apps, all time
    const themes = Object.keys(d.themes).filter((t) => t !== "general");
    const negBy = themes.map((t) => ({ t, n: sum(d.apps.map((a) => sum(d.series[a.slug].themes_negative[t] || []))) }))
      .filter((x) => x.n > 0).sort((x, y) => y.n - x.n);
    const negAll = sum(d.apps.map((a) => sum(Object.values(d.series[a.slug].themes_negative).map(sum))));
    if (negBy.length) {
      const top = negBy[0], top4 = negBy.slice(0, 4);
      cards.push(el("article", { class: "insight reveal", style: `transition-delay:${cards.length * 80}ms` },
        el("div", { class: "insight-k" }, "Top reason for negative reviews · all apps"),
        el("div", null,
          el("div", { class: "insight-v", style: "font-size:30px;letter-spacing:-0.03em" }, d.themes[top.t].label),
          el("p", { class: "insight-t", style: "margin-top:8px" }, `${top.n} of ${negAll} negative written reviews (${pct(top.n, negAll)}%)`)),
        el("ul", { class: "tlist" }, top4.map((x) => el("li", null,
          el("span", null, d.themes[x.t].label), el("b", null, String(x.n)),
          el("span", { class: "bar" }, el("i", { style: `width:${(100 * x.n) / top.n}%` })))))));
    }
    $("#insights").replaceChildren(...cards);
    if (!cards.length) $("#signals").hidden = true;
  }

  // ---- reviews per week ------------------------------------------------------------------
  function renderVolume() {
    const d = state.data;
    const labels = cut(d.weeks);
    const apps = appsWithReviews();
    const empty = d.apps.filter((a) => !a.stored_total).map((a) => ({ label: `${a.vendor} (no reviews yet)`, color: css("--baseline") }));
    legend($("#vol-legend"), apps.map((a) => ({ label: a.vendor, color: appColor(a.slug) })).concat(empty), "line");
    addChart($("#vol-chart"), {
      type: "line",
      data: {
        labels,
        datasets: apps.map((a) => {
          const c = appColor(a.slug);
          return {
            label: a.vendor,
            swatch: c,
            data: cut(d.series[a.slug].total),
            borderColor: c,
            backgroundColor: (ctx) => {
              const area = ctx.chart.chartArea;
              if (!area) return "transparent";
              const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
              g.addColorStop(0, hexA(c, 0.16));
              g.addColorStop(1, hexA(c, 0));
              return g;
            },
            fill: "origin",
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBorderWidth: 2,
            pointHoverBackgroundColor: c,
            pointHoverBorderColor: css("--surface"),
            tension: 0.35,
            cubicInterpolationMode: "monotone",
            borderJoinStyle: "round",
            borderCapStyle: "round",
          };
        }),
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: axes(),
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => `Week of ${fmtDate(labels[items[0].dataIndex])}`,
              label: (item) => `${item.dataset.label}\t${item.formattedValue}`,
            },
          },
        },
      },
      plugins: [crosshair, weekTicks(labels)],
    });
    const rows = [];
    labels.forEach((w, i) => {
      const vals = apps.map((a) => cut(d.series[a.slug].total)[i]);
      if (sum(vals)) rows.push([weekLabel(w), ...vals.map(String)]);
    });
    table($("#vol-table"), ["Week of", ...apps.map((a) => a.vendor)], rows.reverse());
  }
  // format category ticks as dates without needing a date adapter
  function weekTicks(labels) {
    return {
      id: "weekTicks",
      beforeInit(chart) {
        chart.options.scales.x.ticks.callback = (v, i) => weekLabel(labels[i]);
      },
    };
  }

  // ---- sentiment small multiples ----------------------------------------------------------------
  function renderSentiment() {
    const d = state.data;
    const labels = cut(d.weeks);
    const colors = { positive: css("--sent-positive"), neutral: css("--sent-neutral"), negative: css("--sent-negative") };
    legend($("#sent-legend"), SENTS.map((s) => ({ label: SENT_LABEL[s], color: colors[s] })), "rect");
    const wrap = $("#sent-multiples");
    wrap.replaceChildren();
    const rows = [];
    for (const a of d.apps) {
      const title = el("h3", null, el("span", { class: "dot", style: `background:${appColor(a.slug)}` }), a.vendor);
      const s = d.series[a.slug];
      const inRange = sum(cut(s.total));
      if (!a.stored_total || !inRange) {
        wrap.append(el("div", { class: "multiple" }, title,
          el("div", { class: "empty-note" }, a.stored_total ? "No reviews in this range." : "No reviews on the App Store yet.")));
        continue;
      }
      const canvas = el("canvas", { role: "img", "aria-label": `Stacked weekly sentiment for ${a.vendor}` });
      wrap.append(el("div", { class: "multiple" }, title, el("div", { class: "chart" }, canvas)));
      addChart(canvas, {
        type: "bar",
        data: {
          labels,
          datasets: SENTS.map((k) => ({
            label: SENT_LABEL[k],
            data: cut(s[k]),
            backgroundColor: colors[k],
            borderColor: css("--surface-2"),
            borderWidth: { top: 2, right: 0, bottom: 0, left: 0 },
            borderSkipped: false,
            borderRadius: 2,
            maxBarThickness: 22,
            categoryPercentage: 0.9,
            barPercentage: 1,
          })),
        },
        options: {
          interaction: { mode: "index", intersect: false },
          // borderWidth is a per-side object here (the 2px gap between stacked
          // segments); Chart.js can't tween objects, so leave it out of the tween
          animations: { numbers: { type: "number", properties: ["x", "y", "base", "width", "height"] } },
          scales: (() => { const sc = axes(); sc.x.stacked = true; sc.y.stacked = true; sc.x.ticks.maxTicksLimit = 4; sc.y.grid.color = css("--grid"); return sc; })(),
          plugins: {
            tooltip: {
              filter: (item) => item.raw > 0,
              callbacks: {
                title: (items) => `${a.vendor} · week of ${fmtDate(labels[items[0].dataIndex])}`,
                label: (item) => `${item.dataset.label}\t${item.raw}`,
              },
            },
          },
        },
        plugins: [weekTicks(labels)],
      });
      labels.forEach((w, i) => {
        const v = SENTS.map((k) => cut(s[k])[i]);
        if (sum(v)) rows.push({ w, order: d.apps.indexOf(a), cells: [weekLabel(w), a.vendor, ...v.map(String)] });
      });
    }
    // newest week first, apps in their usual order within a week
    rows.sort((x, y) => (x.w === y.w ? x.order - y.order : x.w < y.w ? 1 : -1));
    table($("#sent-table"), ["Week of", "App", "Positive", "Neutral", "Negative"], rows.map((r) => r.cells));
    // second column is text, keep it left-aligned
    $("#sent-table").querySelectorAll("tr > :nth-child(2)").forEach((c) => c.classList.remove("num"));
  }

  // ---- theme mix ----------------------------------------------------------------------------------
  function renderThemes() {
    const d = state.data;
    const themeKeys = Object.keys(d.themes);
    const apps = appsWithReviews().filter((a) => sum(cut(d.series[a.slug].total)) > 0);
    const counts = {}, negs = {}, totals = {};
    for (const a of apps) {
      const s = d.series[a.slug];
      counts[a.slug] = Object.fromEntries(themeKeys.map((t) => [t, sum(cut(s.themes[t]))]));
      negs[a.slug] = Object.fromEntries(themeKeys.map((t) => [t, sum(cut(s.themes_negative[t]))]));
      totals[a.slug] = sum(Object.values(counts[a.slug]));
    }
    const share = (slug, t) => (totals[slug] ? (100 * counts[slug][t]) / totals[slug] : 0);
    // rank themes by average share across apps; "general" always last
    const order = themeKeys.slice().sort((x, y) => {
      if (x === "general") return 1;
      if (y === "general") return -1;
      const m = (t) => sum(apps.map((a) => share(a.slug, t))) / Math.max(apps.length, 1);
      return m(y) - m(x);
    });
    legend($("#theme-legend"), apps.map((a) => ({ label: `${a.vendor} (${totals[a.slug]} written)`, color: appColor(a.slug) })), "rect");
    const skipped = apps.map((a) => `${sum(cut(d.series[a.slug].no_text))} for ${a.vendor}`).join(", ");
    $("#theme-note").textContent = apps.length ? `Rating-only reviews have no text to classify, so they are left out here (${skipped}).` : "";
    const wrap = $("#theme-chart-wrap");
    wrap.style.height = `${Math.max(220, order.length * (apps.length * 16 + 20) + 40)}px`;
    if (!apps.length) {
      wrap.replaceChildren(el("div", { class: "empty-note" }, "No reviews in this range."));
    } else {
      if (!wrap.querySelector("canvas")) wrap.replaceChildren(el("canvas", { id: "theme-chart", role: "img", "aria-label": "Bar chart of theme share per app" }));
      addChart(wrap.querySelector("canvas"), {
        type: "bar",
        data: {
          labels: order.map((t) => d.themes[t].label),
          datasets: apps.map((a) => ({
            label: a.vendor,
            data: order.map((t) => Math.round(share(a.slug, t) * 10) / 10),
            backgroundColor: appColor(a.slug),
            borderRadius: 4,
            borderSkipped: "start",
            maxBarThickness: 14,
            categoryPercentage: 0.8,
            barPercentage: 0.9,
            slug: a.slug,
          })),
        },
        options: {
          indexAxis: "y",
          interaction: { mode: "nearest", axis: "y", intersect: false },
          scales: {
            x: { beginAtZero: true, grid: { color: css("--grid") }, border: { display: false },
                 ticks: { callback: (v) => `${v}%`, color: css("--axis"), maxTicksLimit: 6 } },
            y: { grid: { display: false }, border: { color: css("--baseline") }, ticks: { color: css("--ink-2"), font: { size: 12.5, weight: "500" }, padding: 8 } },
          },
          plugins: {
            tooltip: {
              callbacks: {
                title: (items) => items[0].label,
                label: (item) => {
                  const slug = item.dataset.slug, t = order[item.dataIndex];
                  return `${item.dataset.label}\t${Math.round(share(slug, t))}%\t${counts[slug][t]} reviews, ${negs[slug][t]} negative`;
                },
              },
            },
          },
        },
      });
    }
    table($("#theme-table"), ["Theme", ...apps.flatMap((a) => [`${a.vendor} share`, `${a.vendor} reviews`, `${a.vendor} negative`])],
      order.map((t) => [d.themes[t].label, ...apps.flatMap((a) => [`${Math.round(share(a.slug, t))}%`, String(counts[a.slug][t]), String(negs[a.slug][t])])]));
    $("#theme-defs").replaceChildren(...themeKeys.flatMap((t) => [el("dt", null, d.themes[t].label), el("dd", null, d.themes[t].definition)]));
  }

  // ---- latest reviews --------------------------------------------------------------------------------
  function stars(n) {
    return el("span", { class: "stars", "aria-label": `${n} out of 5 stars` }, "★".repeat(n), el("span", { class: "off" }, "★".repeat(5 - n)));
  }
  function renderLatestFilter() {
    const wrap = $("#latest-filter");
    const opts = [{ slug: "all", label: "All apps" }].concat(state.data.apps.map((a) => ({ slug: a.slug, label: a.vendor, color: appColor(a.slug) })));
    wrap.replaceChildren(...opts.map((o) => el("button", {
      type: "button", "aria-pressed": String(state.latestApp === o.slug),
      onclick: () => { state.latestApp = o.slug; save(); renderLatestFilter(); renderLatest(); },
    }, o.color ? el("span", { class: "dot", style: `background:${o.color}` }) : null, o.label)));
  }
  function initial(name) {
    const m = /[\p{L}\p{N}]/u.exec(name || "");
    return m ? m[0].toUpperCase() : "?";
  }
  function renderLatest() {
    const d = state.data;
    const sentColor = { positive: css("--sent-positive"), neutral: css("--sent-neutral"), negative: css("--sent-negative") };
    const list = d.latest.filter((r) => state.latestApp === "all" || r.app === state.latestApp).slice(0, 20);
    const box = $("#latest");
    if (!list.length) {
      const a = appBy(state.latestApp);
      box.replaceChildren(el("li", { class: "empty-note" }, a && a.listed_total === 0 ? `${a.name} has no reviews on the App Store yet.` : "No reviews yet."));
      return;
    }
    box.replaceChildren(...list.map((r) => {
      const a = appBy(r.app);
      const c = appColor(r.app);
      const text = el("p", { class: "review-text" + (r.text ? "" : " empty") }, r.text || "Rating only, no written review.");
      const item = el("li", { class: "review", style: `--c:${c}` },
        el("div", { class: "review-top" },
          el("span", { class: "avatar", "aria-hidden": "true" }, initial(r.store)),
          el("div", { class: "who" }, el("strong", null, r.store || "Merchant"),
            el("span", null, [r.country, fmtDate(r.date)].filter(Boolean).join(" · "))),
          el("span", { class: "app-badge" }, el("span", { class: "dot", style: `background:${c}` }), a.vendor)),
        stars(r.rating),
        text);
      if (r.text && r.text.length > 260) {
        text.classList.add("clamp");
        const btn = el("button", { class: "more", type: "button", onclick: () => { text.classList.toggle("clamp"); btn.textContent = text.classList.contains("clamp") ? "Show more" : "Show less"; } }, "Show more");
        item.append(btn);
      }
      item.append(el("div", { class: "review-foot" },
        el("div", { class: "chips" },
          el("span", { class: "chip", title: r.tag_reason || null }, el("i", { class: "key-rect", style: `background:${sentColor[r.sentiment] || css("--baseline")}` }), SENT_LABEL[r.sentiment] || "Untagged"),
          el("span", { class: "chip" }, (d.themes[r.theme] || {}).label || "Untagged"),
          el("span", { class: "chip chip-src" }, r.tag_source === "llm" ? "AI tag" : r.tag_source === "rules" ? "rules tag" : "from stars")),
        el("a", { class: "open", href: r.url, target: "_blank", rel: "noopener" }, "View on Shopify ↗")));
      return item;
    }));
  }

  // ---- evaluation ------------------------------------------------------------------------------------
  function renderEval() {
    const ev = state.data.evaluation;
    const box = $("#eval");
    if (!ev) {
      box.replaceChildren(el("div", { class: "callout" },
        el("h3", null, "The hand-labelled check is on its way"),
        el("div", null, "A person tags 30 reviews blind, and every run scores the tracker against those labels."),
        el("ol", { class: "steps" },
          el("li", null, "Open the labelling page: 30 reviews, with the tracker's tags hidden."),
          el("li", null, "Tag each one, then download hand_labels.csv."),
          el("li", null, "Commit it as eval/hand_labels.csv. The next run shows agreement here.")),
        el("a", { class: "btn-primary", href: "label.html" }, "Open the labelling page →")));
      return;
    }
    const themes = state.data.themes;
    const themeLabel = (k) => (themes[k] || {}).label || k;
    const base = ev.rules_baseline;
    const tile = (label, m, b) => el("div", { class: "tile eval-tile" },
      el("div", { class: "stat-label" }, label),
      el("div", { class: "stat-value" }, `${Math.round(m.pct)}%`, el("small", null, ` ${m.agree} of ${m.n || ev.n}`)),
      el("div", { class: "meter", role: "img", "aria-label": `Tracker ${Math.round(m.pct)}%, keyword rules ${Math.round(b.pct)}%` },
        el("i", { style: `width:${m.pct}%` }), el("b", { style: `left:${b.pct}%`, title: `Keyword rules alone: ${Math.round(b.pct)}%` })),
      el("div", { class: "meter-cap" }, `Keyword rules alone: ${Math.round(b.pct)}%`));
    const sentDot = (k) => el("i", { class: "dot", style: `background:${css("--sent-" + k) || css("--baseline")}` });
    const verdict = (who, tags, other) => el("div", { class: "verdict-row" },
      el("span", { class: "who" }, who),
      el("span", { class: "chip" + (other && tags.sentiment !== other.sentiment ? " chip-diff" : "") }, sentDot(tags.sentiment), SENT_LABEL[tags.sentiment] || tags.sentiment),
      el("span", { class: "chip" + (other && tags.theme !== other.theme ? " chip-diff" : "") }, themeLabel(tags.theme)));
    const highlights = ev.highlight || [];
    const heading = highlights.length >= 2 ? "Two cases it got wrong" : highlights.length ? "The case it got wrong" : "No disagreements in this sample";
    const parts = [
      el("div", { class: "eval-grid" },
        tile("Sentiment agrees with me", ev.tracker.sentiment, base.sentiment),
        tile("Theme agrees with me", ev.tracker.theme, base.theme),
        tile("Both agree", ev.tracker.both, base.both)),
      el("p", { class: "sub" },
        `${ev.n} reviews labelled by hand on ${fmtDate(ev.labelled_on)}. Tracker tags as stored (${ev.tracker_sources}); the marker on each bar is the keyword rules alone, to show what the AI adds.`),
      el("h3", { class: "miss-head" }, heading),
    ];
    const misses = el("div", { class: "misses" });
    for (const m of highlights) {
      const a = appBy(m.app);
      const c = appColor(m.app);
      const reason = (m.tracker.reason || "").trim().replace(/\.$/, "");
      misses.append(el("article", { class: "miss", style: `--c:${c}` },
        el("div", { class: "review-meta" },
          el("span", { class: "app-badge" }, el("span", { class: "dot", style: `background:${c}` }), a ? a.vendor : m.app),
          stars(m.rating),
          el("a", { class: "open", href: m.url, target: "_blank", rel: "noopener" }, "View on Shopify ↗")),
        el("q", null, !m.text ? "(no text)" : m.text.length > 420 ? m.text.slice(0, 400).trimEnd() + "…" : m.text),
        el("div", { class: "verdicts" },
          verdict("Me", m.human, null),
          verdict(m.tracker.source === "llm" ? "Tracker (AI)" : m.tracker.source === "rules" ? "Tracker (rules)" : "Tracker", m.tracker, m.human)),
        reason ? el("p", { class: "why" }, el("strong", null, "Its reason: "), `“${reason}”`) : null,
        m.note ? el("p", { class: "why" }, el("strong", null, "My note: "), m.note) : null));
    }
    if (highlights.length) parts.push(misses);
    parts.push(el("p", { class: "sub", style: "margin-top:14px" }, el("a", { href: "https://github.com/Krishna-1812/shopify-checkout-reviews-tracker/blob/main/eval/REPORT.md" }, "Full evaluation report with every disagreement ↗")));
    box.replaceChildren(...parts);
  }

  // ---- health ---------------------------------------------------------------------------------------------
  function renderHealth() {
    const runs = state.data.runs;
    const box = $("#health");
    const last = runs.last;
    if (!last) { box.textContent = "No runs recorded yet."; return; }
    const strip = el("div", { class: "runs-strip", "aria-label": "Last 30 runs, oldest first" },
      runs.recent.map((r) => {
        const color = r.status === "ok" ? css("--good") : css("--critical");
        const title = `${fmtDate(r.at.slice(0, 10))} ${r.at.slice(11, 16)} UTC: ${r.status === "ok" ? "healthy" : "alert"}${r.simulate ? " (simulated test)" : ""}, ${r.new ?? 0} new`;
        const style = r.simulate ? `background:transparent;box-shadow:inset 0 0 0 2px ${color}` : `background:${color}`;
        return r.url ? el("a", { href: r.url, title, style, "aria-label": title }) : el("span", { title, style });
      }));
    const t = last.tagging || {};
    const mix = state.data.tagging;
    const tile = (k, v, s) => el("div", { class: "tile" }, el("div", { class: "stat-label" }, k), el("div", { class: "v" }, v), s ? el("div", { class: "s" }, s) : null);
    const parts = [
      el("div", { class: "health-tiles" },
        tile("Last run", ago(last.started_at), last.run_url ? el("a", { href: last.run_url }, "View logs ↗") : null),
        tile("Last healthy run", runs.last_ok_at ? ago(runs.last_ok_at) : "never", null),
        tile("New reviews last run", String(last.totals ? last.totals.new : 0), last.totals ? `${nf.format(last.totals.stored)} stored in total` : null),
        tile("AI tagger", t.llm_status || "–", t.llm_model || (t.llm_status === "not needed" ? "no new written reviews to tag" : null))),
      el("div", { class: "stat-label" }, `Last ${runs.recent.length} recorded runs, oldest first${runs.recent.some((r) => r.simulate) ? " (hollow = simulated alert test)" : ""}. Hover for details.`), strip,
    ];
    if (last.problems && last.problems.length) {
      parts.push(el("ul", { class: "problems" }, last.problems.map((p) =>
        el("li", null, icon(p.level === "error" ? "critical" : "warning"),
          el("span", null, `${p.level === "error" ? "Alert" : "Note"}: ${p.app ? appBy(p.app).vendor + ": " : ""}${p.message}`)))));
    }
    const rows = state.data.apps.map((a) => {
      const r = (last.apps || {})[a.slug] || {};
      return [a.vendor, String(r.declared_total ?? "–"), String(r.parsed ?? "–"), String(r.new ?? "–"), String(r.stored_total ?? "–"), `${r.mode || "–"}, ${r.pages_fetched ?? 0} page${(r.pages_fetched ?? 0) === 1 ? "" : "s"}`];
    });
    const tw = el("div", { class: "table-wrap" });
    table(tw, ["App", "Listed on Shopify", "Parsed this run", "New", "Stored", "Crawl"], rows);
    parts.push(tw);
    parts.push(el("p", { class: "sub", style: "margin-top:12px" }, "How every stored review was tagged: " +
      Object.entries(mix).map(([k, v]) => `${v} ${k === "llm" ? "by AI" : k === "rules" ? "by keyword rules" : k === "stars" ? "from stars (no text)" : k}`).join(", ") + "."));
    box.replaceChildren(...parts);
  }

  // ---- motion & navigation -----------------------------------------------------------------------------------
  let revealer = null;
  function observeReveals() {
    const nodes = document.querySelectorAll(".reveal:not(.in)");
    if (reduceMotion || !("IntersectionObserver" in window)) { nodes.forEach((n) => n.classList.add("in")); return; }
    if (!revealer) {
      revealer = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          e.target.classList.add("in");
          revealer.unobserve(e.target);
          // replay the chart entrance once the panel is actually on screen
          state.charts.filter((c) => e.target.contains(c.canvas)).forEach((c) => { c.reset(); c.update(); });
        }
      }, { threshold: 0.08, rootMargin: "0px 0px -4% 0px" });
    }
    nodes.forEach((n) => revealer.observe(n));
  }
  function trackSections() {
    if (!("IntersectionObserver" in window)) return;
    const links = [...document.querySelectorAll(".toc a")];
    const byId = Object.fromEntries(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { links.forEach((a) => a.classList.remove("active")); byId[e.target.id].classList.add("active"); }
      }
    }, { rootMargin: "-35% 0px -60% 0px" });
    Object.keys(byId).forEach((id) => { const s = document.getElementById(id); if (s) io.observe(s); });
  }

  // ---- orchestration ---------------------------------------------------------------------------------------
  function save() {
    try { localStorage.setItem("crt-view", JSON.stringify({ weeks: state.weeks, latestApp: state.latestApp })); } catch (e) { /* storage blocked */ }
  }
  function restore() {
    try {
      const v = JSON.parse(localStorage.getItem("crt-view") || "null");
      if (v) { state.weeks = Number(v.weeks) || 0; state.latestApp = v.latestApp || "all"; }
    } catch (e) { /* storage blocked */ }
  }
  function renderCharts() {
    state.charts.forEach((c) => c.destroy());
    state.charts = [];
    chartDefaults();
    renderVolume();
    renderSentiment();
    renderThemes();
    const n = state.data.weeks.length;
    const first = state.data.weeks[sliceIndex()];
    $("#range-note").textContent = `Showing ${fmtDate(first)} to today (${n - sliceIndex()} weeks; the current week is partial).`;
    document.querySelectorAll("#range button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.weeks) === state.weeks)));
  }
  function renderAll() {
    renderStatus();
    renderHero();
    renderCards();
    renderInsights();
    renderCharts();
    renderLatestFilter();
    renderLatest();
    renderEval();
    renderHealth();
    observeReveals();
  }

  document.querySelectorAll("#range button").forEach((b) =>
    b.addEventListener("click", () => { state.weeks = Number(b.dataset.weeks); save(); renderCharts(); }));
  setInterval(() => { const n = $("#next-run"); if (n) n.textContent = nextRun(); }, 30000);

  restore();
  observeReveals();
  trackSections();
  fetch("data.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
    .then((data) => {
      state.data = data;
      if (!window.Chart) throw new Error("the chart library did not load");
      const go = () => renderAll();
      // draw once the web font is in, so canvas text uses it too
      if (document.fonts && document.fonts.ready) return Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 1200))]).then(go);
      go();
    })
    .catch((err) => {
      $("#status").replaceChildren(icon("critical"), el("strong", null, "Could not load data"), el("span", null, String(err.message || err)));
      document.querySelectorAll(".reveal").forEach((n) => n.classList.add("in"));
      if (state.data) { renderCards(); renderLatestFilter(); renderLatest(); renderEval(); renderHealth(); }
    });
})();
