// PulseFlow Q4 forecast dashboard.
// Reads data/forecast.json (written by pulse_flow.py) and does the what-if math live in the browser.

const WEIGHTS = { leadership: 0.2, commercial: 0.2, contractual: 0.6 };   // README Rule 2
const PROMOTE_DEFAULT = { leadership: 3, commercial: 3, contractual: 3 }; // README: promoted deals start at 3/3/3
const MAX_TOP8 = 8;

let data;        // forecast.json, never modified
let baseline;    // starting what-if state, so Reset can restore it
let state;       // current what-if state

// ---------- Formatting ----------
const money = (n) => "$" + Math.round(n).toLocaleString("en-US");
const moneyShort = (n) => {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${Math.round(abs / 1e3)}K`;
  return `${sign}$${Math.round(abs)}`;
};
const signed = (n) => (n >= 0 ? "+" : "-") + moneyShort(Math.abs(n));
const pct = (p) => `${Math.round(p * 100)}%`;
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- Forecast math (mirrors pulse_flow.py) ----------
function compositeGpa(s) {
  // Round to 2 decimals BEFORE the tier lookup, same as the pipeline (avoids 3.1999... dropping a tier)
  const raw = WEIGHTS.leadership * s.leadership + WEIGHTS.commercial * s.commercial + WEIGHTS.contractual * s.contractual;
  return Math.round(raw * 100) / 100;
}

function gpaToProbability(gpa) {
  if (gpa >= 3.2) return 0.75;
  if (gpa >= 2.4) return 0.5;
  if (gpa >= 1.6) return 0.3;
  return 0.1;
}

function scoresEqual(a, b) {
  return a && b && a.leadership === b.leadership && a.commercial === b.commercial && a.contractual === b.contractual;
}

// Works out one deal's numbers for a given state.
function evaluateDeal(deal, s) {
  const useAlternate = s.useOlderRows && deal.alternate;
  const acv = useAlternate ? deal.alternate.annual_acv : deal.annual_acv;
  const stage = useAlternate ? deal.alternate.stage : deal.stage;
  const stageProbability = useAlternate ? deal.alternate.stage_probability : deal.stage_probability;
  const inTop8 = s.top8.includes(deal.id);
  const excluded = s.excludePastDue && deal.past_due;

  let probability = stageProbability;
  let gpa = null;
  let vpOverride = false;
  if (inTop8) {
    const scores = s.scores[deal.id];
    gpa = compositeGpa(scores);
    probability = gpaToProbability(gpa);
    // Honor the VP's hand-typed probability only while the VP's own scores are untouched
    const original = deal.top8;
    if (s.honorVp && original && scoresEqual(scores, original) && original.vp_probability !== original.probability) {
      probability = original.vp_probability;
      vpOverride = true;
    }
  }
  return { deal, acv, stage, probability, gpa, inTop8, excluded, vpOverride, ev: excluded ? 0 : acv * probability };
}

function computeTotals(s) {
  const rows = data.deals.map((d) => evaluateDeal(d, s));
  const sum = (list) => list.reduce((t, r) => t + r.ev, 0);
  const top8 = sum(rows.filter((r) => r.inTop8));
  const core = sum(rows.filter((r) => !r.inTop8 && r.deal.segment === "Core"));
  const carebridge = sum(rows.filter((r) => !r.inTop8 && r.deal.segment === "CareBridge"));
  const total = top8 + core + carebridge;
  return { rows, top8, core, carebridge, total, variance: total - data.meta.board_target };
}

// ---------- State ----------
function initialState() {
  const top8Deals = data.deals.filter((d) => d.top8).sort((a, b) => a.top8.rank - b.top8.rank);
  const scores = {};
  top8Deals.forEach((d) => {
    scores[d.id] = { leadership: d.top8.leadership, commercial: d.top8.commercial, contractual: d.top8.contractual };
  });
  return { top8: top8Deals.map((d) => d.id), scores, honorVp: false, useOlderRows: false, excludePastDue: false,
           product: "All", rep: "All" };
}

const clone = (o) => JSON.parse(JSON.stringify(o));

// ---------- Rendering: KPI banner ----------
function renderBanner(t) {
  const target = data.meta.board_target;
  document.getElementById("kpi-total").textContent = moneyShort(t.total);
  const verdict = document.getElementById("kpi-verdict");
  verdict.className = "verdict " + (t.variance >= 0 ? "good" : "bad");
  verdict.textContent = t.variance >= 0
    ? `✓ ${moneyShort(t.variance)} above the ${moneyShort(target)} target`
    : `✕ ${moneyShort(-t.variance)} short of the ${moneyShort(target)} target`;

  const baseTotal = computeTotals(baseline).total;
  const delta = document.getElementById("kpi-delta");
  delta.hidden = Math.abs(t.total - baseTotal) < 1;
  delta.textContent = `${signed(t.total - baseTotal)} vs baseline`;

  document.getElementById("kpi-top8").textContent = moneyShort(t.top8);
  document.getElementById("kpi-core").textContent = moneyShort(t.core);
  document.getElementById("kpi-carebridge").textContent = moneyShort(t.carebridge);
  const variance = document.getElementById("kpi-variance");
  variance.textContent = signed(t.variance);
  variance.className = "tile-number " + (t.variance >= 0 ? "good" : "bad");

  // Path-to-target bar: scale to whichever is bigger, the total or the target
  const scale = Math.max(t.total, target) * 1.05;
  const track = document.getElementById("path-track");
  track.querySelectorAll(".seg").forEach((el) => el.remove());
  const segments = [
    ["s1", "Top 8 strategic deals", t.top8],
    ["s2", "Longtail · Core Salesforce", t.core],
    ["s3", "Longtail · CareBridge", t.carebridge],
  ];
  const tooltip = document.getElementById("path-tooltip");
  segments.forEach(([cls, label, value], i) => {
    const seg = document.createElement("div");
    seg.className = `seg ${cls}` + (i === segments.length - 1 ? " last" : "");
    seg.style.width = `${(value / scale) * 100}%`;
    seg.setAttribute("aria-label", `${label}: ${money(value)}`);
    seg.addEventListener("mousemove", (e) => {
      const box = track.getBoundingClientRect();
      tooltip.hidden = false;
      tooltip.textContent = `${label}: ${money(value)}`;
      tooltip.style.left = `${e.clientX - box.left}px`;
    });
    seg.addEventListener("mouseleave", () => { tooltip.hidden = true; });
    track.insertBefore(seg, document.getElementById("path-target"));
  });
  document.getElementById("path-target").style.left = `${(target / scale) * 100}%`;
}

// ---------- Rendering: judgment-call toggles ----------
function renderCalls(t) {
  const impact = (key) => computeTotals({ ...state, [key]: !state[key] }).total - t.total;
  const overrides = data.deals.filter((d) => d.top8 && d.top8.vp_probability !== d.top8.probability);
  const alternates = data.deals.filter((d) => d.alternate);
  const pastDue = data.deals.filter((d) => d.past_due);

  const calls = [];
  if (overrides.length) calls.push({
    key: "honorVp",
    title: `Honor VP override on ${overrides.map((d) => d.top8.shorthand).join(", ")}`,
    body: overrides.map((d) => `Formula says ${pct(d.top8.probability)} (score ${d.top8.gpa.toFixed(2)}); VP typed ${pct(d.top8.vp_probability)}.`).join(" "),
  });
  if (alternates.length) calls.push({
    key: "useOlderRows",
    title: `Use the older Salesforce row for ${alternates.map((d) => d.health_system).join(", ")}`,
    body: alternates.map((d) => `Exported twice: ${d.stage} ${moneyShort(d.annual_acv)} (newer) vs ${d.alternate.stage} ${moneyShort(d.alternate.annual_acv)}.`).join(" "),
  });
  if (pastDue.length) calls.push({
    key: "excludePastDue",
    title: `Exclude ${pastDue.length} past-due deals`,
    body: `Still open, but their close dates passed before ${data.meta.as_of}.`,
  });

  document.getElementById("call-list").innerHTML = calls.map((c) => {
    const change = impact(c.key);
    const verb = state[c.key] ? "Turning off" : "Turning on";
    return `<label class="call">
      <input type="checkbox" data-call="${c.key}" ${state[c.key] ? "checked" : ""}>
      <span><strong>${escapeHtml(c.title)}</strong>
      <p>${escapeHtml(c.body)}</p>
      <p class="impact">${verb}: ${signed(change)}</p></span>
    </label>`;
  }).join("") || `<p class="label">No judgment calls this week.</p>`;
}

// ---------- Rendering: Deal Room ----------
function matchesFilter(deal) {
  return (state.product === "All" || deal.product === state.product) && (state.rep === "All" || deal.sales_rep === state.rep);
}

function dealCell(r) {
  const d = r.deal;
  const badges = [];
  if (d.past_due) badges.push(`<span class="badge warn" title="Close date ${d.close_date} has passed">Past due</span>`);
  if (r.inTop8 && !d.top8) badges.push(`<span class="badge info">Promoted</span>`);
  if (r.vpOverride) badges.push(`<span class="badge warn">VP override</span>`);
  if (d.alternate && state.useOlderRows) badges.push(`<span class="badge warn">Older row</span>`);
  if (d.billing === "Monthly") badges.push(`<span class="badge info" title="HubSpot amount was monthly; shown ×12">Monthly ×12</span>`);
  return `<td><span class="deal-name">${escapeHtml(d.health_system)}</span>${badges.join("")}
    <span class="deal-meta">${escapeHtml(d.id)} · ${escapeHtml(d.source)} · closes ${escapeHtml(d.close_date)}</span></td>`;
}

function scoreSelect(dealId, pillar) {
  const value = state.scores[dealId][pillar];
  const original = data.deals.find((d) => d.id === dealId).top8;
  const edited = original ? original[pillar] !== value : false;
  const options = [1, 2, 3, 4].map((n) => `<option ${n === value ? "selected" : ""}>${n}</option>`).join("");
  return `<td class="num"><select class="score ${edited ? "edited" : ""}" data-deal="${dealId}" data-pillar="${pillar}" aria-label="${pillar} score">${options}</select></td>`;
}

function renderDealRoom(t) {
  const byId = Object.fromEntries(t.rows.map((r) => [r.deal.id, r]));
  const top8Rows = state.top8.map((id) => byId[id]).filter((r) => matchesFilter(r.deal));
  const longtail = t.rows.filter((r) => !r.inTop8 && matchesFilter(r.deal)).sort((a, b) => b.ev - a.ev);
  const top8Full = state.top8.length >= MAX_TOP8;

  document.getElementById("top8-body").innerHTML = top8Rows.map((r) => `
    <tr class="${r.excluded ? "excluded" : ""}">
      ${dealCell(r)}
      <td>${escapeHtml(r.deal.product)}</td><td>${escapeHtml(r.deal.sales_rep)}</td>
      <td class="num">${money(r.acv)}</td>
      ${scoreSelect(r.deal.id, "leadership")}${scoreSelect(r.deal.id, "commercial")}${scoreSelect(r.deal.id, "contractual")}
      <td class="num">${r.gpa.toFixed(2)}</td>
      <td class="num">${pct(r.probability)}</td>
      <td class="num"><strong>${money(r.ev)}</strong></td>
      <td><button class="btn small" data-demote="${r.deal.id}">Move to longtail</button></td>
    </tr>`).join("") + subtotalRow(top8Rows, 9, 2);

  document.getElementById("longtail-body").innerHTML = longtail.map((r) => `
    <tr class="${r.excluded ? "excluded" : ""}">
      ${dealCell(r)}
      <td>${escapeHtml(r.deal.product)}</td><td>${escapeHtml(r.deal.sales_rep)}</td>
      <td>${escapeHtml(r.stage)}</td>
      <td class="num">${money(r.acv)}</td>
      <td class="num">${pct(r.probability)}</td>
      <td class="num"><strong>${money(r.ev)}</strong></td>
      <td><button class="btn small" data-promote="${r.deal.id}" ${top8Full ? "disabled title='Top 8 is full. Move a deal out first.'" : ""}>Add to Top 8</button></td>
    </tr>`).join("") + subtotalRow(longtail, 6, 1);

  const filtered = state.product !== "All" || state.rep !== "All";
  const visible = [...top8Rows, ...longtail];
  document.getElementById("filter-note").textContent = filtered
    ? `Showing ${visible.length} deals worth ${money(visible.reduce((s, r) => s + r.ev, 0))} expected. The banner always shows the full forecast.`
    : `${t.rows.length} active Q4 deals`;
}

function subtotalRow(rows, labelSpan, tailSpan) {
  const ev = rows.reduce((s, r) => s + r.ev, 0);
  return `<tr class="subtotal"><td colspan="${labelSpan}">Subtotal · ${rows.length} deals</td><td class="num">${money(ev)}</td><td colspan="${tailSpan - 1}"></td></tr>`;
}

// ---------- Rendering: What Changed (static, from the pipeline) ----------
function renderMovements() {
  const m = data.movements;
  const totalAcv = (list, key = "annual_acv") => list.reduce((s, d) => s + d[key], 0);
  const advanced = m.stage_moves.filter((d) => d.direction === "advanced").length;
  const net = totalAcv(m.acv_changes, "delta");

  const tiles = [
    ["New deals", m.new.length, `${moneyShort(totalAcv(m.new))} annual value added`],
    ["Slipped to Q1-2027", m.slipped.length, `${moneyShort(totalAcv(m.slipped))} out of Q4`],
    ["Closed lost", m.closed_lost.length, `${moneyShort(totalAcv(m.closed_lost))} lost`],
    ["Stage moves", m.stage_moves.length, `${advanced} advanced · ${m.stage_moves.length - advanced} regressed`],
    ["Amount changes", m.acv_changes.length, `${signed(net)} net annual value`],
  ];
  document.getElementById("move-tiles").innerHTML = tiles.map(([label, count, detail]) =>
    `<div class="move-tile"><p class="label">${label}</p><p class="tile-number">${count}</p><p class="detail">${detail}</p></div>`).join("");

  const table = (title, headers, rows) => `
    <h3>${title}</h3>
    <div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th class="${h.endsWith("$") ? "num" : ""}">${h.replace("$", "")}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("") || `<tr><td colspan="${headers.length}">None this week</td></tr>`}</tbody></table></div>`;
  const name = (d) => `<td><span class="deal-name">${escapeHtml(d.health_system)}</span><span class="deal-meta">${escapeHtml(d.id)} · ${escapeHtml(d.sales_rep)}</span></td>`;

  document.getElementById("move-tables").innerHTML = [
    table("New deals", ["Deal", "Product", "Stage", "Annual value$"],
      m.new.map((d) => `<tr>${name(d)}<td>${escapeHtml(d.product)}</td><td>${escapeHtml(d.stage)}</td><td class="num">${money(d.annual_acv)}</td></tr>`)),
    table("Slipped out of Q4", ["Deal", "Product", "Close date moved", "Annual value$"],
      m.slipped.map((d) => `<tr>${name(d)}<td>${escapeHtml(d.product)}</td><td>${escapeHtml(d.from_close)} → ${escapeHtml(d.to_close)} (${escapeHtml(d.to_period)})</td><td class="num">${money(d.annual_acv)}</td></tr>`)),
    table("Closed lost", ["Deal", "Product", "Was in stage", "Annual value$"],
      m.closed_lost.map((d) => `<tr>${name(d)}<td>${escapeHtml(d.product)}</td><td>${escapeHtml(d.from_stage)}</td><td class="num">${money(d.annual_acv)}</td></tr>`)),
    table("Stage moves on active Q4 deals", ["Deal", "Product", "Movement", "Annual value$"],
      m.stage_moves.map((d) => `<tr>${name(d)}<td>${escapeHtml(d.product)}</td><td><span class="${d.direction === "advanced" ? "up" : "down"}">${d.direction === "advanced" ? "▲ Advanced" : "▼ Regressed"}</span> ${escapeHtml(d.from_stage)} → ${escapeHtml(d.stage)}</td><td class="num">${money(d.annual_acv)}</td></tr>`)),
    table("Amount changes on active Q4 deals", ["Deal", "Product", "Last week$", "This week$", "Change$"],
      m.acv_changes.map((d) => `<tr>${name(d)}<td>${escapeHtml(d.product)}</td><td class="num">${money(d.from_acv)}</td><td class="num">${money(d.annual_acv)}</td><td class="num ${d.delta >= 0 ? "up" : "down"}">${signed(d.delta)}</td></tr>`)),
  ].join("");
}

// ---------- Rendering: Data Checks (static, from the pipeline) ----------
let auditStep = "All";
function renderAudit() {
  const log = data.audit_log;
  const steps = ["All", ...new Set(log.map((e) => e.step))];
  document.getElementById("checks-count").textContent = log.length;
  document.getElementById("check-filters").innerHTML = steps.map((s) => {
    const n = s === "All" ? log.length : log.filter((e) => e.step === s).length;
    return `<button class="chip" data-step="${s}" aria-pressed="${s === auditStep}">${s} (${n})</button>`;
  }).join("");
  document.getElementById("audit-body").innerHTML = log
    .filter((e) => auditStep === "All" || e.step === auditStep)
    .map((e) => `<tr><td><span class="step-pill">${escapeHtml(e.step)}</span></td><td>${escapeHtml(e.source)}</td>
      <td>${escapeHtml(e.record)}</td><td>${escapeHtml(e.issue)}</td><td>${escapeHtml(e.resolution)}</td><td>${escapeHtml(e.impact || "")}</td></tr>`)
    .join("");
}

// ---------- Render everything that depends on what-if state ----------
function render() {
  const totals = computeTotals(state);
  renderBanner(totals);
  renderCalls(totals);
  renderDealRoom(totals);
}

// ---------- Events ----------
function wireEvents() {
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset.pillar) state.scores[el.dataset.deal][el.dataset.pillar] = Number(el.value);
    if (el.dataset.call) state[el.dataset.call] = el.checked;
    if (el.id === "filter-product") state.product = el.value;
    if (el.id === "filter-rep") state.rep = el.value;
    render();
  });

  document.addEventListener("click", (e) => {
    const el = e.target.closest("button");
    if (!el) return;
    if (el.dataset.demote) {
      state.top8 = state.top8.filter((id) => id !== el.dataset.demote);
    } else if (el.dataset.promote && state.top8.length < MAX_TOP8) {
      const id = el.dataset.promote;
      state.top8.push(id);
      if (!state.scores[id]) state.scores[id] = { ...PROMOTE_DEFAULT };
    } else if (el.id === "reset") {
      state = clone(baseline);
      document.getElementById("filter-product").value = "All";
      document.getElementById("filter-rep").value = "All";
    } else if (el.dataset.step) {
      auditStep = el.dataset.step;
      renderAudit();
      return;
    } else if (el.dataset.tab) {
      document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t === el));
      document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== el.dataset.tab; });
      return;
    } else {
      return;
    }
    render();
  });
}

function fillFilters() {
  const unique = (key) => [...new Set(data.deals.map((d) => d[key]))].sort();
  const add = (id, values) => {
    document.getElementById(id).insertAdjacentHTML("beforeend", values.map((v) => `<option>${escapeHtml(v)}</option>`).join(""));
  };
  add("filter-product", unique("product"));
  add("filter-rep", unique("sales_rep"));
}

// ---------- Start ----------
fetch("data/forecast.json")
  .then((response) => response.json())
  .then((json) => {
    data = json;
    baseline = initialState();
    state = clone(baseline);
    document.getElementById("as-of").textContent =
      `Data as of ${data.meta.as_of} · Board target ${money(data.meta.board_target)}`;
    fillFilters();
    renderMovements();
    renderAudit();
    wireEvents();
    render();
  })
  .catch(() => {
    document.querySelector("main").innerHTML =
      `<p class="panel-intro">Couldn't load data/forecast.json. Run <code>python pulse_flow.py</code>, then serve this folder with <code>python -m http.server</code>.</p>`;
  });