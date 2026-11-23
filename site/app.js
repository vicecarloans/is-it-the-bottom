// Is it the bottom? Reads the daily JSON and draws the page.
// Data comes straight from the repo, so the page never needs a redeploy for new data.
// Add ?data=<folder>/ to the URL to read from somewhere else (for example a local copy).

const DEFAULT_DATA = "https://raw.githubusercontent.com/vicecarloans/is-it-the-bottom/main/data/";
const PARAMS = new URLSearchParams(location.search);
const DATA = PARAMS.get("data") || DEFAULT_DATA;
// Add ?day=YYYY-MM-DD to show the saved result of a past day instead of today's.
const DAY = /^\d{4}-\d{2}-\d{2}$/.test(PARAMS.get("day") || "") ? PARAMS.get("day") : null;

function dayLink(day) {
  const params = new URLSearchParams(location.search);
  if (day) params.set("day", day); else params.delete("day");
  const query = params.toString();
  return location.pathname + (query ? `?${query}` : "");
}
const SVG = "http://www.w3.org/2000/svg";

const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const monthFmt = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const fmtDate = (iso) => (iso ? dateFmt.format(new Date(iso + "T00:00:00Z")) : "unknown");

const LEVELS = { "No": "no", "Getting closer": "closer", "Looks like a bottom": "bottom" };

async function getJSON(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

// The daily job runs on weekdays at 22:30 UTC and needs a few minutes to finish.
// Returns the date (YYYY-MM-DD, UTC) of the latest run that should exist by `now`.
// The newest result the page should have by now: today's once the 9:00 am New York run
// has had time to finish (GitHub can start it late, so allow until noon), otherwise the
// previous weekday's.
function lastExpectedRun(now) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
    }).formatToParts(now).map((p) => [p.type, p.value]),
  );
  const day = new Date(Date.UTC(+parts.year, +parts.month - 1, +parts.day));
  if (+parts.hour < 12) day.setUTCDate(day.getUTCDate() - 1);
  while (day.getUTCDay() === 0 || day.getUTCDay() === 6) day.setUTCDate(day.getUTCDate() - 1);
  return day.toISOString().slice(0, 10);
}

const SESSIONS = { before_open: "before the open", during_trading: "during trading", after_close: "after the close" };

// ---- Hero -----------------------------------------------------------------------------

function renderHero(latest) {
  const answer = document.getElementById("answer");
  answer.textContent = latest.verdict;
  answer.dataset.level = LEVELS[latest.verdict] || "none";

  const dd = latest.drawdown;
  let summary;
  if (!dd.in_drawdown) {
    summary = dd.pct === null
      ? "Price data is missing today, so the drawdown could not be measured."
      : `The S&P 500 is ${Math.abs(dd.pct).toFixed(1)}% below its high, short of the 10% fall that opens the window.`;
  } else {
    summary = `${latest.count} of the ${latest.total} conditions hold, with the S&P 500 ${Math.abs(dd.pct).toFixed(1)}% below its ${fmtDate(dd.peak_date)} high.`;
  }
  if (latest.base_verdict && latest.base_verdict !== latest.verdict) {
    summary += ` The five conditions alone say "${latest.base_verdict}".`;
  }
  document.getElementById("summary").textContent = summary;

  const override = latest.override || {};
  if (override.capped) {
    const cap = document.getElementById("cap");
    cap.textContent = `Held at "Getting closer" at most: ${override.reasons.join("; ")}.`;
    cap.hidden = false;
  }

  const catalyst = latest.catalyst || {};
  const news = latest.digest || {};
  if (catalyst.active && catalyst.event) {
    const event = catalyst.event;
    const left = catalyst.window_trading_days - catalyst.trading_days_since;
    const box = document.getElementById("catalyst");
    box.append(
      el("p", {}, el("strong", { text: "Jensen Huang is NVIDIA's CEO again." })),
      el("blockquote", { text: `“${event.quote}”` }),
      el("p", {},
        "From ",
        el("a", { href: event.url, text: `NVIDIA's SEC filing of ${fmtDate(event.date)}` }),
        catalyst.lifted_verdict
          ? `. This moves the verdict up one level for ${left} more trading days.`
          : ". It moves the verdict up one level once the market is in a drawdown.",
      ),
    );
    box.hidden = false;
  } else {
    // Remembered across runs, so the note stays while the story drops out of the day's headlines.
    const seen = catalyst.watch || (news.huang_return_reported && news.huang_note
      ? { note: news.huang_note, first_seen: latest.as_of, last_seen: latest.as_of } : null);
    if (seen) {
      const when = seen.last_seen === latest.as_of
        ? (seen.first_seen === latest.as_of ? "" : ` First reported ${fmtDate(seen.first_seen)}.`)
        : ` Last seen in headlines ${fmtDate(seen.last_seen)}.`;
      const watch = document.getElementById("watch");
      watch.textContent = `Watching: ${seen.note}${when} This changes nothing until NVIDIA confirms it in an SEC filing.`;
      watch.hidden = false;
    }
  }

  if (DAY) {
    const viewing = document.getElementById("viewing");
    viewing.append(`You're looking at the result for ${fmtDate(latest.as_of)}. `, el("a", { href: dayLink(null), text: "See today's result" }), ".");
    viewing.hidden = false;
  }

  const expected = lastExpectedRun(new Date());
  if (!DAY && latest.as_of < expected) {
    const stale = document.getElementById("stale");
    stale.textContent = `This page shows data from ${fmtDate(latest.as_of)}. The daily update did not run on ${fmtDate(expected)}, so the verdict may be out of date.`;
    stale.hidden = false;
  }

  document.getElementById("caveat").textContent =
    (latest.session && SESSIONS[latest.session]
      ? `Updated ${SESSIONS[latest.session]} on ${fmtDate(latest.as_of)}` +
        (latest.session === "before_open" && latest.drawdown.data_date ? `, using the ${fmtDate(latest.drawdown.data_date)} close.` : ".")
      : `Updated for ${fmtDate(latest.as_of)}.`) + (latest.backtested ? "" : " These rules have not been tested against history yet.");
  document.title = `${latest.verdict} · Is it the bottom?`;
}

// ---- Depth chart ------------------------------------------------------------------------

function renderDepth(latest, history) {
  const rows = latest.market || [];
  const figure = document.getElementById("depth");
  const dd = latest.drawdown;
  document.getElementById("fall-lede").textContent = dd.pct === null
    ? "No price data today."
    : `The line shows how far the S&P 500 sat below its high of the past year, each day for two years. The five checks only count below the dashed line.`;
  if (rows.length < 2) return;

  const W = 760, H = 280, left = 44, right = 64, top = 16, bottom = 28;
  const minDd = Math.min(-20, ...rows.map((r) => r[2]));
  const floor = Math.floor(minDd / 5) * 5;
  const x = (i) => left + (i / (rows.length - 1)) * (W - left - right);
  const y = (v) => top + (v / floor) * (H - top - bottom);

  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-labelledby": "depth-caption" });

  const grid = svg("g", { class: "axis" });
  for (let v = 0; v >= floor; v -= 5) {
    grid.append(svg("line", { class: v === 0 ? "zero" : "grid", x1: left, x2: W - right, y1: y(v), y2: y(v) }));
    const label = svg("text", { x: left - 8, y: y(v) + 4, "text-anchor": "end" });
    label.textContent = v === 0 ? "High" : `${v}%`;
    grid.append(label);
  }
  let lastMonth = null;
  rows.forEach((r, i) => {
    const month = r[0].slice(0, 7);
    if (month !== lastMonth && ["01", "04", "07", "10"].includes(month.slice(5))) {
      const t = svg("text", { x: x(i), y: H - 6, "text-anchor": "middle" });
      t.textContent = month.endsWith("01") ? month.slice(0, 4) : monthFmt.format(new Date(r[0] + "T00:00:00Z"));
      grid.append(t);
    }
    lastMonth = month;
  });
  chart.append(grid);

  const threshold = dd.threshold_pct ?? -10;
  chart.append(svg("rect", { class: "zone", x: left, y: y(threshold), width: W - left - right, height: y(floor) - y(threshold) }));
  chart.append(svg("line", { class: "line-10", x1: left, x2: W - right, y1: y(threshold), y2: y(threshold) }));
  const zoneLabel = svg("text", { class: "label-10", x: W - right - 6, y: y(threshold) - 8, "text-anchor": "end" });
  zoneLabel.textContent = "Below this line the checks count";

  const points = rows.map((r, i) => `${x(i).toFixed(1)},${y(r[2]).toFixed(1)}`);
  chart.append(svg("path", { class: "area", d: `M${x(0)},${y(0)} L${points.join(" L")} L${x(rows.length - 1)},${y(0)} Z` }));
  const trace = svg("path", { class: "trace", d: `M${points.join(" L")}` });
  chart.append(trace);
  chart.append(zoneLabel); // drawn after the line, with a halo, so the line never crosses it

  const byDate = new Map(rows.map((r, i) => [r[0], i]));
  for (const signal of history.signals || []) {
    const i = byDate.get(signal.date);
    if (i === undefined) continue;
    const cx = x(i), cy = y(rows[i][2]);
    const mark = svg("path", { class: "signal", d: `M${cx},${cy + 8} l-6,10 h12 z` });
    const title = svg("title");
    title.textContent = `All five held on ${fmtDate(signal.date)}`;
    mark.append(title);
    chart.append(mark);
  }

  const last = rows[rows.length - 1];
  const lx = x(rows.length - 1), ly = y(last[2]);
  chart.append(svg("circle", { class: "now", cx: lx, cy: ly, r: 4 }));
  const nowLabel = svg("text", { class: "now-label", x: lx + 8, y: ly + 4 });
  nowLabel.textContent = `${last[2].toFixed(1)}%`;
  chart.append(nowLabel);

  document.getElementById("depth-caption").textContent =
    `S&P 500 drawdown from its one-year high over two years. Latest: ${last[2].toFixed(1)}% on ${fmtDate(last[0])}.`;
  figure.append(chart);

  const length = trace.getTotalLength();
  trace.style.strokeDasharray = length;
  trace.style.setProperty("--len", length);
  trace.classList.add("draw");
}

// ---- Checklist --------------------------------------------------------------------------

function statusOf(cond) {
  if (cond.met === null) return { state: "unknown", text: "No data" };
  if (cond.stale) return { state: "unknown", text: "Stale" };
  return cond.met ? { state: "met", text: "Holds" } : { state: "missed", text: "Not yet" };
}

function sparkline(spark, state) {
  const wrap = el("div", { class: "spark" });
  if (!spark || !spark.points || spark.points.length < 2) return wrap;
  const W = 150, H = 44, pad = 4;
  const values = spark.points.map((p) => p[1]);
  const lo = Math.min(...values, spark.threshold), hi = Math.max(...values, spark.threshold);
  const span = hi - lo || 1;
  const x = (i) => (i / (values.length - 1)) * W;
  const y = (v) => pad + (1 - (v - lo) / span) * (H - 2 * pad);

  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" });
  chart.append(svg("line", { class: "s-threshold", x1: 0, x2: W, y1: y(spark.threshold), y2: y(spark.threshold) }));
  chart.append(svg("polyline", { class: "s-line", points: values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ") }));
  const color = state === "met" ? "var(--met)" : state === "missed" ? "var(--missed)" : "var(--unknown)";
  chart.append(svg("circle", { class: "s-end", cx: x(values.length - 1), cy: y(values[values.length - 1]), r: 4, fill: color }));
  wrap.append(chart, el("p", { class: "spark-label", text: `${spark.label}; dashed line is the threshold` }));
  return wrap;
}

function checkMeta(cond) {
  const when = cond.data_date ? fmtDate(cond.data_date) : null;
  const dated = when && !(cond.detail || "").includes(when) ? `Data from ${when}` : null;
  return [cond.detail, dated].filter(Boolean).join(". ") + ".";
}

// Most checks have one sparkline; margin debt adds the weekly futures reading under it.
function sparkGroup(cond, state) {
  const extra = (cond.spark && cond.spark.also) || [];
  if (!extra.length) return sparkline(cond.spark, state);
  const group = el("div", { class: "spark spark-group" });
  for (const spark of [cond.spark, ...extra]) {
    const one = sparkline(spark, state);
    one.className = "spark-item";
    group.append(one);
  }
  return group;
}

function newsNote(cond, news) {
  if (!news) return null;
  const text = cond.key === "fed" ? news.fed_note : cond.key === "sector" ? news.sector_note : "";
  return text ? el("p", { class: "check-news", text: `In the news: ${text}` }) : null;
}

function renderChecks(latest, explainers) {
  const list = document.getElementById("checks");
  const counted = latest.drawdown.in_drawdown;
  document.getElementById("checks-lede").textContent = counted
    ? "The market has fallen far enough for these to count."
    : "Shown for reference. They start to count once the S&P 500 is 10% below its high.";

  for (const cond of latest.conditions) {
    const status = statusOf(cond);
    const info = explainers[cond.key] || {};
    const id = `explain-${cond.key}`;
    const details = el("details", {},
      el("summary", { text: "What this measures" }),
      el("p", { text: info.measures || "" }),
      el("p", { text: info.why || "" }),
      el("p", { class: "check-meta", text: `Rule: ${cond.threshold}.` }),
    );
    details.id = id;
    const row = el("li", { class: "check" },
      el("span", { class: "status", "data-state": status.state, text: status.text }),
      el("div", {},
        el("p", { class: "check-name", text: cond.name }),
        el("p", { class: "check-value", text: cond.value }),
        el("p", { class: "check-meta", text: checkMeta(cond) }),
        newsNote(cond, latest.digest),
      ),
      sparkGroup(cond, status.state),
      details,
    );
    list.append(row);
  }

  const errors = Object.entries(latest.errors || {});
  if (errors.length) {
    const box = document.getElementById("problems");
    box.textContent = "Some data could not be loaded today: " + errors.map(([source, message]) => `${source} (${message})`).join("; ") + ".";
    box.hidden = false;
  }
}

// ---- News -------------------------------------------------------------------------------

const TONES = {
  hawkish: "Fed tone in the news: leaning toward higher rates.",
  dovish: "Fed tone in the news: leaning toward lower rates.",
  neutral: "Fed tone in the news: neither raising nor cutting.",
};

function renderNews(latest) {
  const news = latest.digest;
  if (!news || !news.summary) return;
  const host = document.getElementById("news");
  host.append(el("p", { class: "news-summary", text: news.summary }));
  if (TONES[news.fed_tone]) host.append(el("p", { class: "news-fed", text: TONES[news.fed_tone] }));
  if (news.sources && news.sources.length) {
    const list = el("ul", { class: "news-sources" });
    for (const source of news.sources) {
      list.append(el("li", {},
        el("a", { href: source.url, rel: "noopener", text: source.title }),
        el("span", { text: ` ${source.source}, ${fmtDate(source.date)}` }),
      ));
    }
    host.append(list);
  }
  document.getElementById("news-section").hidden = false;
}

// ---- Signals ----------------------------------------------------------------------------

function renderSignals(latest, history) {
  const host = document.getElementById("signals");
  const signals = history.signals || [];
  const days = history.days || [];
  if (!signals.length) {
    const since = days.length ? ` since tracking began on ${fmtDate(days[0].date)}` : "";
    host.append(el("p", { class: "empty", text: `No day has met all five conditions${since}.` }));
    return;
  }
  const rows = latest.market || [];
  const byDate = new Map(rows.map((r, i) => [r[0], i]));
  const change = (i, ahead) => {
    if (i === undefined || i + ahead >= rows.length) return null;
    return (rows[i + ahead][1] / rows[i][1] - 1) * 100;
  };
  const cell = (v) => {
    if (v === null) return el("td", { class: "num", text: "not yet" });
    return el("td", { class: `num ${v >= 0 ? "up" : "down"}`, text: `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` });
  };
  const table = el("table", {},
    el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: "Signal date" }),
      el("th", { scope: "col", class: "num", text: "1 month later" }),
      el("th", { scope: "col", class: "num", text: "3 months" }),
      el("th", { scope: "col", class: "num", text: "6 months" }),
    )),
  );
  const body = el("tbody");
  for (const signal of [...signals].reverse()) {
    const i = byDate.get(signal.date);
    body.append(el("tr", {}, el("td", { text: fmtDate(signal.date) }), cell(change(i, 21)), cell(change(i, 63)), cell(change(i, 126))));
  }
  table.append(body);
  host.append(table);
}

// ---- Daily log -------------------------------------------------------------------------

const SHORT_NAMES = { vix: "VIX", fed: "Fed", margin: "Margin debt", sector: "Sector", nvda: "NVIDIA" };
const LOG_ROWS = 20;

function renderLog(latest, history) {
  const host = document.getElementById("log");
  const days = [...(history.days || [])].reverse();
  if (!days.length) {
    host.append(el("p", { class: "empty", text: "No runs saved yet." }));
    return;
  }
  const table = el("table", { class: "log" },
    el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: "Date" }),
      el("th", { scope: "col", text: "Verdict" }),
      el("th", { scope: "col", text: "Conditions held" }),
      el("th", { scope: "col", class: "num", text: "S&P 500 vs high" }),
    )),
  );
  const body = el("tbody");
  days.forEach((day, i) => {
    const held = Object.entries(day.met || {}).filter(([, met]) => met).map(([key]) => SHORT_NAMES[key] || key);
    const date = el("span", {},
      day.archived ? el("a", { href: dayLink(day.date), text: fmtDate(day.date) }) : fmtDate(day.date),
      day.session && day.session !== "after_close" ? el("span", { class: "which", text: ` (${SESSIONS[day.session]})` }) : "",
    );
    let verdict = day.verdict;
    if (day.catalyst) verdict += " (Huang +1)";
    if (day.capped) verdict += " (capped)";
    const row = el("tr", {},
      el("td", {}, date),
      el("td", { class: "verdict", text: verdict }),
      el("td", { class: "count" }, `${day.count} of 5`, held.length ? el("span", { class: "which", text: ` (${held.join(", ")})` }) : ""),
      el("td", { class: "num", text: day.drawdown_pct === null ? "no data" : `${day.drawdown_pct.toFixed(1)}%` }),
    );
    if (day.date === latest.as_of) row.setAttribute("aria-current", "date");
    if (i >= LOG_ROWS) row.hidden = true;
    body.append(row);
  });
  table.append(body);
  host.append(table);
  if (days.length > LOG_ROWS) {
    const more = el("button", { type: "button", class: "log-more", text: `Show all ${days.length} days` });
    more.addEventListener("click", () => {
      body.querySelectorAll("tr[hidden]").forEach((row) => { row.hidden = false; });
      more.remove();
    });
    host.append(more);
  }
}

// ---- How this works ---------------------------------------------------------------------

function renderExplainers(explainers) {
  const host = document.getElementById("explainers");
  for (const info of Object.values(explainers)) {
    host.append(el("h3", { text: info.name }), el("p", { text: info.measures }), el("p", { text: info.why }));
  }
}

async function main() {
  const explainers = await getJSON("explainers.json").catch(() => ({}));
  renderExplainers(explainers);
  try {
    const [latest, history] = await Promise.all([
      getJSON(DAY ? `${DATA}days/${DAY}.json` : `${DATA}latest.json`),
      getJSON(`${DATA}history.json`).catch(() => ({ days: [], signals: [] })),
    ]);
    renderHero(latest);
    renderDepth(latest, history);
    renderChecks(latest, explainers);
    renderNews(latest);
    if (DAY) document.getElementById("news-title").textContent = "News that day";
    renderLog(latest, history);
    renderSignals(latest, history);
    document.getElementById("generated").textContent =
      `Last run ${new Date(latest.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}.`;
  } catch (error) {
    document.getElementById("answer").textContent = "No data yet";
    document.getElementById("summary").textContent = DAY
      ? `There is no saved result for ${fmtDate(DAY)}. Results are saved for weekdays from the first date in the daily log.`
      : "Today's data could not be loaded. The daily job may not have run yet; try again after the US market close.";
    console.error(error);
  }
}

main();

// Theme picker. "auto" follows the system setting; the choice is remembered in this browser only.
(function themePicker() {
  const root = document.documentElement;
  const buttons = document.querySelectorAll("[data-theme-choice]");
  const show = (choice) => buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice)));
  let current = root.dataset.theme || "auto";
  show(current);
  buttons.forEach((b) => b.addEventListener("click", () => {
    current = b.dataset.themeChoice;
    if (current === "auto") delete root.dataset.theme; else root.dataset.theme = current;
    try { if (current === "auto") localStorage.removeItem("theme"); else localStorage.setItem("theme", current); } catch (e) { /* storage unavailable */ }
    show(current);
  }));
})();
