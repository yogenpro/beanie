const palette = ["primary", "teal", "indigo", "orange", "purple", "cyan", "pink", "lime", "azure", "yellow"];
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });
const axis = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 });

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function color(key) {
  let hash = 0;
  for (const char of key) hash = (Math.imul(hash, 31) + char.codePointAt(0)) >>> 0;
  return `var(--tblr-${palette[hash % palette.length]})`;
}

function groupMonths(timeline, interval, ending) {
  const groups = new Map();
  for (const [month, rows, href] of timeline) {
    const key = interval === "year" ? month.slice(0, 4)
      : interval === "quarter" ? `${month.slice(0, 4)} Q${Math.ceil(Number(month.slice(5)) / 3)}` : month;
    if (!groups.has(key)) groups.set(key, { key, rows: [], href: interval === "month" ? href : null });
    const group = groups.get(key);
    if (ending) group.rows = rows;
    else group.rows.push(...rows);
  }
  return [...groups.values()];
}

function chartBars(panel, data, state, view) {
  const worth = view === "worth";
  const root = view === "income-flow" ? data.roots[0] : view === "expense-flow" ? data.roots[1] : null;
  const invert = view === "net" || view === "income-flow";
  const groups = groupMonths(data.timeline, state.interval, worth);
  const bars = groups.map(group => {
    const segments = new Map();
    for (const [account, currency, amount] of group.rows) {
      if (currency !== state.currency || (root && account.split(":")[0] !== root)) continue;
      const key = state.bars === "stacked" ? account : "";
      segments.set(key, (segments.get(key) ?? 0) + Number(amount) * (invert ? -1 : 1));
    }
    let positive = 0;
    let negative = 0;
    const pieces = [];
    for (const [account, amount] of [...segments].sort(([a], [b]) => a.localeCompare(b))) {
      if (!amount) continue;
      const offset = amount > 0 ? positive : negative;
      pieces.push({ account, amount, offset });
      if (amount > 0) positive += amount;
      else negative += amount;
    }
    return { ...group, pieces, positive, negative };
  });
  if (!bars.some(bar => bar.pieces.length)) return false;

  const positive = Math.max(...bars.map(bar => bar.positive));
  const negative = Math.max(...bars.map(bar => -bar.negative));
  const top = negative && !positive ? 0 : (negative ? 2 : 4);
  const span = Math.max(positive / (top || 1), negative / (4 - top || 1));
  const scale = 10 ** Math.floor(Math.log10(span));
  const step = [1, 2, 2.5, 5, 10].map(n => n * scale).find(n => n >= span);
  const frame = element("div", "chart-frame");
  const labels = element("div", "chart-axis small text-secondary");
  for (let tick = top; tick >= top - 4; tick--) {
    const label = element("span", "", (step >= 1000 ? axis : number).format(tick * step));
    label.title = `${number.format(tick * step)} ${state.currency}`;
    labels.append(label);
  }
  const plot = element("div", "chart-plot");
  plot.style.setProperty("--zero-fraction", String(top / 4));
  const grid = element("div", "report-bar-grid");
  const labelStride = Math.max(1, Math.ceil(bars.length / 12));
  for (const [index, bar] of bars.entries()) {
    const column = element("div", "report-bar-column");
    const track = element("div", "report-bar-track");
    for (const piece of bar.pieces) {
      const href = piece.account ? data.accounts[piece.account] : bar.href;
      const mark = element(href ? "a" : "span", "report-bar");
      if (href) mark.href = href;
      const caption = `${bar.key} · ${piece.account || state.label} · ${number.format(piece.amount)} ${state.currency}`;
      mark.title = caption;
      mark.setAttribute("aria-label", caption);
      if (!href) mark.setAttribute("role", "img");
      mark.style.height = `${Math.abs(piece.amount) / (step * 4) * 100}%`;
      mark.style.top = `${top * 25 - (piece.offset + Math.max(0, piece.amount)) / (step * 4) * 100}%`;
      mark.style.background = piece.account ? color(piece.account)
        : `var(--tblr-${piece.amount < 0 ? "orange" : "primary"})`;
      track.append(mark);
    }
    const onMobile = index % (labelStride * 2) === 0;
    column.append(track, element("span", `report-bar-label small text-secondary${onMobile ? "" : " d-none d-sm-block"}`,
      index % labelStride === 0 ? bar.key : ""));
    grid.append(column);
  }
  plot.append(grid);
  frame.append(labels, plot);
  panel.append(frame);
  return true;
}

const svgNamespace = "http://www.w3.org/2000/svg";
function svgElement(name, attrs) {
  const node = document.createElementNS(svgNamespace, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function hierarchy(rows, root, currency) {
  const top = { key: root, children: new Map() };
  for (const [account, unit, amount] of rows) {
    const parts = account.split(":");
    if (parts[0] !== root || unit !== currency || !Number(amount)) continue;
    let node = top;
    for (const part of parts.slice(1)) {
      if (!node.children.has(part)) node.children.set(part, { key: `${node.key}:${part}`, children: new Map() });
      node = node.children.get(part);
    }
    node.amount = Number(amount);
  }
  function weight(node) {
    const children = [...node.children.values()].map(weight);
    if (children.length && node.amount) children.push({ key: node.key, weight: Math.abs(node.amount), total: node.amount, children: [] });
    const total = children.length ? children.reduce((sum, child) => sum + child.total, 0) : node.amount ?? 0;
    return {
      key: node.key,
      total,
      weight: children.length ? children.reduce((sum, child) => sum + child.weight, 0) : Math.abs(total),
      children: children.sort((a, b) => b.weight - a.weight),
    };
  }
  return weight(top);
}

function hierarchyChart(panel, data, state, root) {
  const tree = hierarchy(data.rows, root, state.currency);
  if (!tree.weight) return false;
  const width = Math.max(200, Math.min(panel.clientWidth || 900, 900));
  const height = 300;
  const circular = state.layout === "sunburst";
  const svg = svgElement("svg", {
    viewBox: circular ? "0 0 300 300" : `0 0 ${width} ${height}`,
    class: "report-hierarchy",
    "aria-label": `${root} · ${state.layout} · ${state.currency}`,
  });
  function mark(node, shape, shade, label) {
    const link = data.accounts[node.key];
    const group = svgElement(link ? "a" : "g", link ? { href: link } : {});
    const caption = `${node.key} · ${number.format(node.total)} ${state.currency}`;
    group.setAttribute("aria-label", caption);
    const title = svgElement("title", {});
    title.textContent = caption;
    shape.setAttribute("fill", shade);
    group.append(title, shape);
    if (label) group.append(label);
    svg.append(group);
  }
  function label(node, x, y, available) {
    if (available < 40) return null;
    const text = svgElement("text", { x, y });
    const name = node.key.split(":").at(-1);
    const chars = Math.max(3, Math.floor((available - 14) / 7));
    text.textContent = name.length > chars ? name.slice(0, chars - 1) + "…" : name;
    return text;
  }
  function rect(node, x, y, w, h, shade) {
    mark(node, svgElement("rect", { x, y, width: w, height: h }), shade, h > 22 ? label(node, x + 7, y + 17, w) : null);
  }
  function treemap(nodes, x, y, w, h, shade) {
    if (!nodes.length) return;
    if (nodes.length === 1) {
      const node = nodes[0];
      const fill = shade ?? color(node.key);
      if (node.children.length && w > 48 && h > 44) {
        rect(node, x, y, w, 22, fill);
        treemap(node.children, x, y + 22, w, h - 22, fill);
      } else rect(node, x, y, w, h, fill);
      return;
    }
    const total = nodes.reduce((sum, node) => sum + node.weight, 0);
    let index = 0;
    let fraction = 0;
    do { fraction += nodes[index++].weight / total; } while (index < nodes.length - 1 && fraction < .5);
    if (w >= h) {
      const left = w * fraction;
      treemap(nodes.slice(0, index), x, y, left, h, shade);
      treemap(nodes.slice(index), x + left, y, w - left, h, shade);
    } else {
      const top = h * fraction;
      treemap(nodes.slice(0, index), x, y, w, top, shade);
      treemap(nodes.slice(index), x, y + top, w, h - top, shade);
    }
  }
  const depth = node => node.children.length ? 1 + Math.max(...node.children.map(depth)) : 1;
  const levels = depth(tree);
  function bands(node, start, end, level, shade) {
    const fill = shade ?? color(node.key);
    if (circular) {
      const ring = 145 / levels;
      const inner = Math.max(0.1, level * ring);
      const outer = (level + 1) * ring;
      const a = start * Math.PI * 2 - Math.PI / 2;
      const b = Math.min(end, start + .999999) * Math.PI * 2 - Math.PI / 2;
      const point = (radius, angle) => [150 + radius * Math.cos(angle), 150 + radius * Math.sin(angle)];
      const large = end - start > .5 ? 1 : 0;
      const d = `M ${point(outer, a)} A ${outer} ${outer} 0 ${large} 1 ${point(outer, b)} L ${point(inner, b)} A ${inner} ${inner} 0 ${large} 0 ${point(inner, a)} Z`;
      mark(node, svgElement("path", { d }), fill, null);
    } else {
      rect(node, start * width, level * height / levels, (end - start) * width, height / levels, fill);
    }
    let cursor = start;
    for (const child of node.children) {
      const next = cursor + (end - start) * child.weight / node.weight;
      bands(child, cursor, next, level + 1, level ? fill : null);
      cursor = next;
    }
  }
  if (state.layout === "treemap") treemap(tree.children.length ? tree.children : [tree], 0, 0, width, height, null);
  else bands(tree, 0, 1, 0, null);
  panel.append(svg);
  return true;
}

function views(data, interval) {
  const period = {month: "Monthly", quarter: "Quarterly", year: "Yearly"}[interval];
  const names = ({
    income: [["net", "Net Profit"], ["income-flow", `Income (${period})`], ["expense-flow", `Expenses (${period})`]],
    balance: [["worth", "Net Worth"]],
    trial: [],
  })[data.report];
  return [...names, ...data.roots.map(root => [`tree:${root}`, root])];
}

for (const report of document.querySelectorAll("[data-statement]")) {
  const panel = report.querySelector("[data-chart-panel]");
  const tabs = report.querySelector("[data-chart-views]");
  const tables = report.querySelector("[data-statement-tables]");
  const defaultTables = tables.innerHTML;
  const status = report.querySelector("[data-report-status]");
  const selectors = Object.fromEntries(["period", "valuation", "interval", "bars", "layout", "currency"]
    .map(key => [key, report.querySelector(`[data-${key}]`)]));
  const params = new URLSearchParams(location.search);
  let view = params.get("chart");
  let data;
  let generation = 0;
  let renderedMode = "cost";
  const cache = new Map();
  for (const key of ["valuation", "interval", "bars", "layout"]) {
    if ([...selectors[key].options].some(option => option.value === params.get(key))) selectors[key].value = params.get(key);
  }
  function updateURL() {
    history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  }
  function render() {
    if (!data) return;
    const choices = views(data, selectors.interval.value);
    if (!choices.some(([key]) => key === view)) view = choices[0][0];
    const hierarchyRoot = view.startsWith("tree:") ? view.slice(5) : null;
    const source = hierarchyRoot ? [["", data.rows]] : data.timeline;
    const activeRoot = hierarchyRoot ?? (view === "income-flow" ? data.roots[0]
      : view === "expense-flow" ? data.roots[1] : null);
    const present = new Set(source.flatMap(([, values]) => values
      .filter(([account]) => !activeRoot || account.split(":")[0] === activeRoot)
      .map(([, currency]) => currency)));
    const currencies = [...new Set([...data.currencies, ...present])].filter(currency => present.has(currency));
    const previousCurrency = params.get("currency");
    selectors.currency.replaceChildren(...currencies.map(currency => new Option(currency, currency)));
    if (currencies.includes(previousCurrency)) selectors.currency.value = previousCurrency;
    const state = Object.fromEntries(Object.entries(selectors).map(([key, select]) => [key, select?.value]));
    state.label = choices.find(([key]) => key === view)[1];
    for (const key of ["interval", "bar", "layout", "currency"]) {
      const control = report.querySelector(`[data-${key}-control]`);
      control.hidden = key === "currency" ? !currencies.length
        : key === "layout" ? !hierarchyRoot : !!hierarchyRoot;
    }
    selectors.currency.disabled = currencies.length < 2;
    panel.replaceChildren();
    const rendered = state.currency && (hierarchyRoot
      ? hierarchyChart(panel, data, state, hierarchyRoot) : chartBars(panel, data, state, view));
    if (!rendered) panel.append(element("p", "text-secondary mb-0", "No values for this view."));
    tabs.replaceChildren(...choices.map(([key, label]) => {
      const button = element("button", `nav-link${key === view ? " active" : ""}`, label);
      button.type = "button";
      button.dataset.chartView = key;
      button.setAttribute("aria-pressed", String(key === view));
      return button;
    }));
  }
  async function load() {
    const request = ++generation;
    const mode = selectors.valuation.value;
    const source = selectors.valuation.selectedOptions[0].dataset.source;
    status.hidden = true;
    panel.setAttribute("aria-busy", "true");
    try {
      if (!cache.has(source)) {
        const response = await fetch(source);
        if (!response.ok) throw new Error("Report unavailable");
        cache.set(source, await response.json());
      }
      if (request !== generation) return;
      data = cache.get(source);
      tables.innerHTML = data.tables ?? defaultTables;
      renderedMode = mode;
      render();
    } catch {
      if (request !== generation) return;
      selectors.valuation.value = renderedMode;
      params.set("valuation", renderedMode);
      updateURL();
      status.textContent = "Unable to load this report view.";
      status.hidden = false;
    } finally {
      if (request === generation) panel.removeAttribute("aria-busy");
    }
  }
  report.querySelector("[data-report-controls]").hidden = false;
  report.querySelector("[data-charts]").hidden = false;
  selectors.period?.addEventListener("change", () => {
    const url = new URL(selectors.period.value, location.href);
    url.search = params;
    location.assign(url);
  });
  for (const key of ["valuation", "interval", "bars", "layout", "currency"]) {
    selectors[key].addEventListener("change", () => {
      params.set(key, selectors[key].value);
      updateURL();
      if (key === "valuation") load();
      else render();
    });
  }
  tabs.addEventListener("click", event => {
    const button = event.target.closest("[data-chart-view]");
    if (!button) return;
    view = button.dataset.chartView;
    params.set("chart", view);
    updateURL();
    render();
    tabs.querySelector("[aria-pressed=true]")?.focus();
  });
  let panelWidth = panel.clientWidth;
  new ResizeObserver(() => {
    if (panel.clientWidth === panelWidth) return;
    panelWidth = panel.clientWidth;
    if (view?.startsWith("tree:")) render();
  }).observe(panel);
  load();
}
