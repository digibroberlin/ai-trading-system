// Dashboard (nur lesend): holt die Momentaufnahme und aktualisiert sie per WebSocket. Keine fremden Bibliotheken.
"use strict";

const SVG = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
const fmt = (v, digits = 2) => {
  const n = num(v);
  return n === null || Number.isNaN(n) ? "–" : n.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
};
const pct = (v, digits = 2) => (num(v) === null ? "–" : fmt(v, digits) + " %");
const usd = (v, digits = 2) => (num(v) === null ? "–" : fmt(v, digits) + " $");
const when = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");
const signClass = (v) => (num(v) > 0 ? "pos" : num(v) < 0 ? "neg" : "");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function replace(id, ...nodes) {
  const target = $(id);
  target.replaceChildren(...nodes);
}

function table(headers, rows, textColumns = 1) {  // die ersten `textColumns` Spalten sind Text (links), der Rest Zahlen
  if (!rows.length) return el("p", { class: "empty" }, "keine");
  const cls = (i) => (i < textColumns ? "text" : "");
  const head = el("tr", {}, headers.map((h, i) => el("th", { class: cls(i) }, h)));
  const body = rows.map((cells) => el("tr", {}, cells.map((c, i) => el("td", { class: cls(i) }, c === null || c === undefined ? "–" : c))));
  return el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, body)));
}

function widthOf(node, percent) {  // ueber das DOM gesetzt: die Sicherheitsrichtlinie verbietet Stil-Attribute im HTML
  node.style.width = percent + "%";
  return node;
}

function colored(text, value) {
  return el("span", { class: signClass(value) }, text);
}

// ------------------------------------------------------------------ Diagramme (SVG)

function lineChart(id, points, key, color, fill, invert) {
  const box = $(id);
  box.replaceChildren();
  if (points.length < 2) { box.append(el("p", { class: "empty" }, "noch zu wenige Punkte")); return; }
  const w = box.clientWidth || 600, h = box.clientHeight || 200, pad = { l: 64, r: 8, t: 8, b: 18 };
  const ys = points.map((p) => num(p[key]));
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (invert) { lo = 0; hi = Math.max(hi, 0.01); }
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = (i) => pad.l + (i / (points.length - 1)) * (w - pad.l - pad.r);
  const y = (v) => (invert ? pad.t + ((v - lo) / (hi - lo)) * (h - pad.t - pad.b) : pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b));
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const line = ys.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const base = invert ? y(lo) : y(lo);
  const area = document.createElementNS(SVG, "polygon");
  area.setAttribute("points", `${x(0)},${base} ${line} ${x(ys.length - 1)},${base}`);
  area.setAttribute("fill", fill);
  const path = document.createElementNS(SVG, "polyline");
  path.setAttribute("points", line);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", color);
  path.setAttribute("stroke-width", "1.6");
  path.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(area, path);
  const label = (text, tx, ty, anchor) => {
    const t = document.createElementNS(SVG, "text");
    t.setAttribute("x", tx); t.setAttribute("y", ty); t.setAttribute("text-anchor", anchor);
    t.textContent = text; svg.append(t);
  };
  const unit = invert ? " %" : "";
  label(fmt(invert ? lo : hi, 0) + unit, pad.l - 6, pad.t + 10, "end");
  label(fmt(invert ? hi : lo, invert ? 1 : 0) + unit, pad.l - 6, h - pad.b, "end");
  label(when(points[0].time), pad.l, h - 4, "start");
  label(when(points[points.length - 1].time), w - pad.r, h - 4, "end");
  box.append(svg);
}

// ------------------------------------------------------------------ Abschnitte

function renderStatus(s) {
  const halted = s.status.trading_halted;
  const chips = [
    el("span", { class: "chip " + (halted ? "alarm" : "okay") }, halted ? `NOT-AUS AKTIV (${s.status.halt_reason || "?"})` : "Handel frei (Paper)"),
    el("span", { class: "chip" }, el("b", {}, "Echtgeld: "), "deaktiviert"),
  ];
  if (s.status.strategy) chips.push(el("span", { class: "chip" }, el("b", {}, "Strategie: "), s.status.strategy));
  if (s.status.start) chips.push(el("span", { class: "chip" }, el("b", {}, "Zeitraum: "), `${when(s.status.start)} – ${when(s.status.end)}`));
  if (s.status.dataset) chips.push(el("span", { class: "chip" }, el("b", {}, "Datensatz: "), s.status.dataset));
  const placeholders = s.status.placeholders || [];
  chips.push(el("span", { class: "chip" }, el("b", {}, "KIBO/YUZU: "), placeholders.length ? "Platzhalter " + placeholders.join(", ") : "regelbasiert"));
  if (s.status.valuation_complete === false) chips.push(el("span", { class: "chip alarm" }, "Bewertung unvollständig"));
  if (s.costs && s.costs.budget && s.costs.budget.blocked) chips.push(el("span", { class: "chip alarm" }, "KI-Budget gesperrt"));
  replace("status", ...chips);
}

function renderTiles(s) {
  const p = s.portfolio, m = s.metrics;
  const net = s.costs && s.costs.net ? s.costs.net.net_after_all_costs : null;
  const tiles = [
    ["Kapital", usd(p.equity)], ["Netto-Ergebnis", colored(usd(p.net_result), p.net_result)],
    ["Rendite", colored(pct(p.return_pct), p.return_pct)], ["Netto nach allen Kosten (abgeschl. Trades)", colored(usd(net), net)],
    ["max. Drawdown", pct(m.max_drawdown_pct)], ["Trades", String(m.trades)], ["Gewinnrate", pct(m.win_rate_pct, 1)],
    ["Profit-Faktor", fmt(m.profit_factor)], ["Sharpe", fmt(m.sharpe)], ["Offene Positionen", String(p.open_positions)],
    ["Offenes Risiko", usd(p.open_risk)], ["Gebühren", usd(m.total_fees)],
  ];
  replace("tiles", ...tiles.map(([label, value]) => el("div", { class: "tile" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value))));
}

function renderCharts(s) {
  $("curve-note").textContent = s.equity.length ? `${s.equity.length} Punkte` : "";
  lineChart("equity-chart", s.equity, "equity", getComputedStyle(document.documentElement).getPropertyValue("--chart"), getComputedStyle(document.documentElement).getPropertyValue("--chart-fill"), false);
  lineChart("drawdown-chart", s.equity, "drawdown_pct", getComputedStyle(document.documentElement).getPropertyValue("--dd"), getComputedStyle(document.documentElement).getPropertyValue("--dd-fill"), true);
}

function renderPositions(s) {
  replace("positions", table(["Instrument", "Seite", "Menge", "Einstieg", "Stop", "Ziel", "Hebel", "Risiko"],
    s.positions.map((p) => [p.instrument, p.side, fmt(p.quantity, 4), fmt(p.entry_price), fmt(p.stop_loss), fmt(p.take_profit), fmt(p.leverage, 1) + "×", usd(p.risk_at_open)]), 2));
}

function renderAgents(s) {
  const order = ["MOMO", "RIN", "SORA", "KIBO", "YUZU", "AKI"];
  const names = [...new Set([...order.filter((a) => s.agents[a]), ...Object.keys(s.agents)])];
  replace("agents", table(["Agent", "Einschätzungen", "Zustimmung", "Ablehnung"],
    names.map((a) => [a, String(s.agents[a].opinions), s.agents[a].approved, s.agents[a].rejected])));
  const reasons = Object.entries(s.blocked.by_reason || {});
  const max = Math.max(1, ...reasons.map(([, n]) => n));
  replace("blocked", reasons.length ? el("div", { class: "bars" }, reasons.map(([r, n]) =>
    el("div", { class: "bar" }, el("span", {}, r), el("div", { class: "track" }, widthOf(el("div", { class: "fill" }), (n / max) * 100)), el("span", { class: "n" }, String(n))))) : el("p", { class: "empty" }, "keine"));
}

function kv(pairs, totalLabel) {
  return el("div", { class: "kv" }, pairs.map(([k, v]) => [el("div", { class: k === totalLabel ? "total" : "" }, k), el("div", { class: k === totalLabel ? "total" : "" }, v)]));
}

function renderCosts(s) {
  const c = s.costs || {}, ai = c.ai || {}, net = c.net, budget = c.budget;
  const blocks = [];
  if (net) {
    blocks.push(el("div", {}, el("h3", {}, "Nettoergebnis"), kv([
      ["Brutto (vor Gebühren/Funding)", usd(net.gross_pnl)], ["Börsengebühren", usd(net.trading_fees)], ["Funding", usd(net.funding)],
      ["Handelsergebnis netto", usd(net.trading_net_pnl)], ["KI-Kosten", usd(net.ai_cost, 4)], ["Server", usd(net.server_cost)],
      ["Marktdaten", usd(net.market_data_cost)], ["Sonstiges", usd(net.other_cost)], ["Nach ALLEN Kosten", colored(usd(net.net_after_all_costs), net.net_after_all_costs)],
    ], "Nach ALLEN Kosten")));
  }
  blocks.push(el("div", {}, el("h3", {}, "KI-Nutzung"), kv([
    ["Analysen gesamt", String(ai.analyses ?? 0)], ["davon bezahlt", String(ai.paid_analyses ?? 0)], ["Regel-Entscheidungen", String(ai.rule_decisions ?? 0)],
    ["Cache-Treffer", String(ai.cache_hits ?? 0)], ["KI-Kosten gesamt", usd(ai.total_cost, 4)], ["je Signal", usd(ai.cost_per_signal, 4)],
  ])));
  const byAgent = Object.entries(ai.cost_by_agent || {});
  const byModel = Object.entries(ai.cost_by_model || {});
  blocks.push(el("div", {}, el("h3", {}, "KI-Kosten je Agent / Modell"),
    table(["Agent / Modell", "Kosten"], [...byAgent.map(([a, v]) => [a, usd(v, 4)]), ...byModel.map(([m, v]) => [m, usd(v, 4)])])));
  const byMonth = Object.entries(ai.cost_by_month || {});
  blocks.push(el("div", {}, el("h3", {}, "KI-Kosten je Monat / Budget"), table(["Monat", "Kosten"], byMonth.map(([m, v]) => [m, usd(v, 4)])),
    budget ? kv([["heute verbraucht", usd(budget.spent_today, 4)], ["Monat verbraucht", usd(budget.spent_month, 4)], ["heute noch frei", usd(budget.remaining_today, 4)],
      ["Grenze Tag / Monat", `${usd(budget.limits.daily_usd)} / ${usd(budget.limits.monthly_usd)}`], ["Status", budget.blocked ? "GESPERRT" : "frei"]])
      : el("p", { class: "empty" }, "kein KI-Budget angeschlossen (Standard 0 = keine bezahlten Aufrufe)")));
  replace("costs", el("div", { class: "grid2" }, blocks));
}

function renderStrategies(s) {
  $("strategies-panel").hidden = !s.strategies.length;
  replace("strategies", table(["Strategie", "Rendite", "max. DD", "Trades", "Gewinnrate", "Profit-Faktor", "Gebühren"],
    s.strategies.map((r) => [r.strategy, colored(pct(r.return_pct), r.return_pct), pct(r.max_drawdown_pct), String(r.trades), pct(num(r.win_rate) * 100, 1), fmt(r.profit_factor), usd(r.total_fees)])));
}

function renderTrades(s) {
  $("trades-note").textContent = s.trades.length ? `letzte ${s.trades.length}` : "";
  replace("trades", table(["Geschlossen", "Instrument", "Seite", "Strategie", "Grund", "Einstieg", "Ausstieg", "Netto", "R"],
    [...s.trades].reverse().map((t) => [when(t.closed_at), t.instrument, t.side, t.strategy, t.exit_reason, fmt(t.entry_price), fmt(t.exit_price), colored(usd(t.net_pnl), t.net_pnl), fmt(t.r_multiple)]), 5));
}

function renderErrors(s) {
  replace("errors", s.errors.length ? el("div", {}, [...s.errors].reverse().map((e) => el("div", { class: "err" }, `${when(e.time)}  ${e.type}  ${JSON.stringify(e.detail).slice(0, 300)}`))) : el("p", { class: "empty" }, "keine"));
}

function render(s) {
  document.querySelectorAll(".mode").forEach((m) => m.classList.toggle("active", m.dataset.mode === s.mode));
  renderStatus(s); renderTiles(s); renderCharts(s); renderPositions(s); renderAgents(s);
  renderCosts(s); renderStrategies(s); renderTrades(s); renderErrors(s);
  $("generated").textContent = "Stand " + when(s.generated_at) + " · Version " + s.version;
}

// ------------------------------------------------------------------ Verbindung

let last = null;

function setConn(ok, text) {
  const c = $("conn");
  c.className = "conn " + (ok ? "ok" : "bad");
  c.textContent = text;
}

async function load() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    last = await res.json();
    render(last);
    setConn(true, "verbunden");
  } catch (e) {
    setConn(false, "nicht erreichbar");
  }
}

function live() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => setConn(true, "live");
  ws.onmessage = (msg) => { last = JSON.parse(msg.data); render(last); };
  ws.onclose = () => { setConn(false, "getrennt – neuer Versuch …"); setTimeout(live, 3000); };
}

window.addEventListener("resize", () => { if (last) renderCharts(last); });
load().then(live);
