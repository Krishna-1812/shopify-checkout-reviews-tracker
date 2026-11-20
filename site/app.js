/* Checkout reviews tracker — page script.
 *
 * Every number shown is computed in Python (tracker/aggregate.py) and shipped
 * in data.json. This file only slices those arrays to the chosen time range
 * and draws them. Review text is untrusted, so it is only ever inserted with
 * textContent, never innerHTML.
 */
(function () {
  "use strict";

  const state = { weeks: 0, latestApp: "all", data: null, charts: [] };
  const $ = (sel) => document.querySelector(sel);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const SENTS = ["positive", "neutral", "negative"];
  const SENT_LABEL = { positive: "Positive", neutral: "Neutral", negative: "Negative" };

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
    C.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
    C.defaults.font.size = 12;
    C.defaults.color = css("--axis");
    C.defaults.borderColor = css("--grid");
    C.defaults.animation = false;
    C.defaults.maintainAspectRatio = false;
    const t = C.defaults.plugins.tooltip;
    t.backgroundColor = css("--surface");
    t.titleColor = css("--ink-2");
    t.bodyColor = css("--ink");
    t.borderColor = css("--border");
    t.borderWidth = 1;
    t.padding = 10;
    t.cornerRadius = 8;
    t.titleFont = { weight: "normal", size: 12 };
    t.bodyFont = { weight: "600", size: 12.5 };
    t.boxPadding = 4;
    C.defaults.plugins.legend.display = false;   // HTML legends instead
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
        ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7, color: css("--axis") },
      },
      y: Object.assign({
        beginAtZero: true,
        grid: { color: css("--grid") },
        border: { display: false },
        ticks: { precision: 0, color: css("--axis"), maxTicksLimit: 5 },
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
    box.replaceChildren(icon(kind), el("strong", null, label),
      el("span", null, `· updated ${ago(runs.last_ok_at || last.started_at)}`));
  }

  // ---- app cards ------------------------------------------------------------------------
  function renderCards() {
    const wrap = $("#app-cards");
    wrap.replaceChildren(...state.data.apps.map((a) => {
      const head = el("div", { class: "card-head" },
        el("span", { class: "dot", style: `background:${appColor(a.slug)}` }),
        el("a", { href: a.store_url, title: a.name }, a.name));
      if (!a.stored_total) {
        return el("article", { class: "card" }, head,
          el("p", { class: "empty-note" },
            a.listed_total === 0
              ? "No reviews on the Shopify App Store yet. The tracker checks every day and will pick up the first one."
              : "No reviews collected yet."),
          el("div", { class: "card-foot" }, el("a", { href: a.reviews_url }, "Open the App Store listing ↗")));
      }
      const negShare = pct(a.last_90d_negative, a.last_90d);
      return el("article", { class: "card" }, head,
        el("div", { class: "stats" },
          el("div", null, el("div", { class: "stat-label" }, "App Store rating"),
            el("div", { class: "stat-value" }, a.listed_rating !== null ? a.listed_rating.toFixed(1) : "–", el("small", null, " / 5"))),
          el("div", null, el("div", { class: "stat-label" }, "Reviews tracked"),
            el("div", { class: "stat-value" }, nf.format(a.stored_total))),
          el("div", null, el("div", { class: "stat-label" }, "Negative"),
            el("div", { class: "stat-value" }, `${pct(a.sentiments.negative, a.stored_total)}%`, el("small", null, " all time")))),
        el("div", { class: "card-foot" },
          a.last_90d
            ? `Last 90 days: ${a.last_90d} review${a.last_90d === 1 ? "" : "s"}, ${negShare}% negative · newest ${fmtDate(a.latest_date)}`
            : `No reviews in the last 90 days · newest ${fmtDate(a.latest_date)}`));
    }));
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
        datasets: apps.map((a) => ({
          label: a.vendor,
          data: cut(d.series[a.slug].total),
          borderColor: appColor(a.slug),
          backgroundColor: appColor(a.slug),
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBorderWidth: 2,
          pointHoverBorderColor: css("--surface"),
          tension: 0,
          borderJoinStyle: "round",
          borderCapStyle: "round",
        })),
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: axes(),
        plugins: {
          tooltip: {
            usePointStyle: true,
            callbacks: {
              title: (items) => `Week of ${fmtDate(labels[items[0].dataIndex])}`,
              label: (item) => ` ${item.formattedValue}  ${item.dataset.label}`,
              labelPointStyle: () => ({ pointStyle: "line", rotation: 0 }),
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
            borderColor: css("--surface"),
            borderWidth: { top: 2, right: 0, bottom: 0, left: 0 },
            borderSkipped: false,
            maxBarThickness: 24,
            categoryPercentage: 0.9,
            barPercentage: 1,
          })),
        },
        options: {
          interaction: { mode: "index", intersect: false },
          scales: (() => { const sc = axes(); sc.x.stacked = true; sc.y.stacked = true; sc.x.ticks.maxTicksLimit = 4; return sc; })(),
          plugins: {
            tooltip: {
              filter: (item) => item.raw > 0,
              callbacks: {
                title: (items) => `${a.vendor} · week of ${fmtDate(labels[items[0].dataIndex])}`,
                label: (item) => ` ${item.raw}  ${item.dataset.label}`,
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
    legend($("#theme-legend"), apps.map((a) => ({ label: `${a.vendor} (${totals[a.slug]} written reviews)`, color: appColor(a.slug) })), "rect");
    const skipped = apps.map((a) => `${sum(cut(d.series[a.slug].no_text))} for ${a.vendor}`).join(", ");
    $("#theme-note").textContent = apps.length ? `Rating-only reviews have no text to classify, so they are left out here (${skipped}).` : "";
    const wrap = $("#theme-chart-wrap");
    wrap.style.height = `${Math.max(220, order.length * (apps.length * 16 + 18) + 40)}px`;
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
            y: { grid: { display: false }, border: { color: css("--baseline") }, ticks: { color: css("--ink-2") } },
          },
          plugins: {
            tooltip: {
              callbacks: {
                title: (items) => items[0].label,
                label: (item) => {
                  const slug = item.dataset.slug, t = order[item.dataIndex];
                  return ` ${Math.round(share(slug, t))}%  ${item.dataset.label} · ${counts[slug][t]} reviews, ${negs[slug][t]} negative`;
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
      const text = el("p", { class: "review-text" + (r.text ? "" : " empty") }, r.text || "Rating only, no written review.");
      const item = el("li", { class: "review" },
        el("div", { class: "review-meta" },
          el("span", { class: "review-app" }, el("span", { class: "dot", style: `background:${appColor(r.app)}` }), a.vendor),
          stars(r.rating), el("span", null, fmtDate(r.date)),
          el("span", null, [r.store, r.country].filter(Boolean).join(" · "))),
        text,
        el("div", { class: "chips" },
          el("span", { class: "chip", title: r.tag_reason || null }, el("i", { class: "key-rect", style: `background:${sentColor[r.sentiment] || css("--baseline")}` }), SENT_LABEL[r.sentiment] || "Untagged"),
          el("span", { class: "chip" }, (d.themes[r.theme] || {}).label || "Untagged"),
          el("span", { class: "chip chip-src" }, r.tag_source === "llm" ? "AI tag" : r.tag_source === "rules" ? "rules tag" : "from stars"),
          el("a", { class: "open", href: r.url, target: "_blank", rel: "noopener" }, "View on Shopify ↗")));
      if (r.text && r.text.length > 320) {
        text.classList.add("clamp");
        const btn = el("button", { class: "more", type: "button", onclick: () => { text.classList.toggle("clamp"); btn.textContent = text.classList.contains("clamp") ? "Show more" : "Show less"; } }, "Show more");
        text.after(btn);
      }
      return item;
    }));
  }

  // ---- evaluation ------------------------------------------------------------------------------------
  function renderEval() {
    const ev = state.data.evaluation;
    const box = $("#eval");
    if (!ev) {
      box.replaceChildren(el("div", { class: "callout" },
        "The hand-labelled check has not been recorded yet. To run it:",
        el("ol", null,
          el("li", null, "Open the ", el("a", { href: "label.html" }, "labelling page"), " (30 reviews, tracker tags hidden) and tag each one."),
          el("li", null, "Download the CSV and commit it as eval/hand_labels.csv."),
          el("li", null, "The next run computes agreement and shows it here."))));
      return;
    }
    const tile = (label, m) => el("div", { class: "tile" }, el("div", { class: "stat-label" }, label),
      el("div", { class: "stat-value" }, `${Math.round(m.pct)}%`, el("small", null, ` ${m.agree} of ${ev.n}`)));
    const base = ev.rules_baseline;
    const parts = [
      el("div", { class: "eval-grid" },
        tile("Sentiment agrees with me", ev.tracker.sentiment),
        tile("Theme agrees with me", ev.tracker.theme),
        tile("Both agree", ev.tracker.both)),
      el("p", { class: "sub" },
        `Tracker tags as they are stored (${ev.tracker_sources}). For comparison, the keyword rules alone agree on sentiment ${Math.round(base.sentiment.pct)}% and theme ${Math.round(base.theme.pct)}% of the time. Labelled ${fmtDate(ev.labelled_on)}.`),
      el("h3", { style: "margin:14px 0 4px" }, "Two cases it got wrong"),
    ];
    for (const m of ev.highlight || []) {
      const a = appBy(m.app);
      parts.push(el("div", { class: "miss" },
        el("div", { class: "review-meta" },
          el("span", { class: "review-app" }, el("span", { class: "dot", style: `background:${appColor(m.app)}` }), a ? a.vendor : m.app),
          stars(m.rating), el("a", { href: m.url, target: "_blank", rel: "noopener" }, "View on Shopify ↗")),
        el("q", null, !m.text ? "(no text)" : m.text.length > 420 ? m.text.slice(0, 400).trimEnd() + "…" : m.text),
        el("div", { class: "verdict" }, `Me: ${m.human.sentiment} / ${state.data.themes[m.human.theme].label}. Tracker: ${m.tracker.sentiment} / ${state.data.themes[m.tracker.theme].label}.`),
        m.note ? el("div", { class: "verdict" }, m.note) : null));
    }
    parts.push(el("p", { class: "sub" }, el("a", { href: "https://github.com/Krishna-1812/shopify-checkout-reviews-tracker/blob/main/eval/REPORT.md" }, "Full evaluation report with every disagreement ↗")));
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
    const parts = [
      el("div", { class: "health-row" },
        el("span", null, "Last run ", el("strong", null, ago(last.started_at)), last.run_url ? [" · ", el("a", { href: last.run_url }, "logs ↗")] : null),
        el("span", null, "Last healthy run ", el("strong", null, runs.last_ok_at ? ago(runs.last_ok_at) : "never")),
        el("span", null, "New reviews last run ", el("strong", null, String(last.totals ? last.totals.new : 0))),
        el("span", null, "AI tagger ", el("strong", null, `${t.llm_status || "–"}`), t.llm_model ? ` (${t.llm_model})` : null)),
      el("div", { class: "stat-label" }, "Last 30 runs (hollow = simulated alert test)"), strip,
    ];
    if (last.problems && last.problems.length) {
      parts.push(el("ul", { class: "problems" }, last.problems.map((p) =>
        el("li", null, icon(p.level === "error" ? "critical" : "warning"),
          el("span", null, `${p.level === "error" ? "Alert" : "Note"}: ${p.app ? appBy(p.app).vendor + ": " : ""}${p.message}`)))));
    }
    const rows = state.data.apps.map((a) => {
      const r = (last.apps || {})[a.slug] || {};
      return [a.vendor, String(r.declared_total ?? "–"), String(r.parsed ?? "–"), String(r.new ?? "–"), String(r.stored_total ?? "–"), `${r.mode || "–"}, ${r.pages_fetched ?? 0} page(s)`];
    });
    const tw = el("div", { class: "table-wrap" });
    table(tw, ["App", "Listed on Shopify", "Parsed this run", "New", "Stored", "Crawl"], rows);
    parts.push(tw);
    parts.push(el("p", { class: "sub" }, "How every stored review was tagged: " +
      Object.entries(mix).map(([k, v]) => `${v} ${k === "llm" ? "by AI" : k === "rules" ? "by keyword rules" : k === "stars" ? "from stars (no text)" : k}`).join(", ") + "."));
    box.replaceChildren(...parts);
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
    $("#range-note").textContent = `${fmtDate(first)} to today · ${n - sliceIndex()} weeks · current week is partial`;
    document.querySelectorAll("#range button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.weeks) === state.weeks)));
  }
  function renderAll() {
    renderStatus();
    renderCards();
    renderCharts();
    renderLatestFilter();
    renderLatest();
    renderEval();
    renderHealth();
    $("#generated").textContent = `Page built ${new Date(state.data.generated_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })} from ${nf.format(state.data.apps.reduce((s, a) => s + a.stored_total, 0))} stored reviews.`;
  }

  document.querySelectorAll("#range button").forEach((b) =>
    b.addEventListener("click", () => { state.weeks = Number(b.dataset.weeks); save(); renderCharts(); }));
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.data && renderAll());
  }

  restore();
  fetch("data.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
    .then((data) => {
      state.data = data;
      if (!window.Chart) throw new Error("the chart library did not load");
      renderAll();
    })
    .catch((err) => {
      $("#status").replaceChildren(icon("critical"), el("strong", null, "Could not load data"), el("span", null, String(err.message || err)));
      if (state.data) { renderCards(); renderLatestFilter(); renderLatest(); renderEval(); renderHealth(); }
    });
})();
