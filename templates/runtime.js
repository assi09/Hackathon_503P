/* Generic explainer runtime: builds controls from a parameter spec, runs the
   generated compute(p) function, and draws generic widgets. Contains no
   paper-specific content; everything specific comes from the embedded spec. */
(function () {
  "use strict";
  var SPEC = JSON.parse(document.getElementById("spec-data").textContent);
  var CODE = window.__explainer || {};
  var DIGITS = SPEC.digits || 3;
  var state = {};
  var controlsEl = document.getElementById("controls");
  var viewsEl = document.getElementById("views");
  var checksEl = document.getElementById("live-checks");
  var errorEl = document.getElementById("lab-error");
  var NS = "http://www.w3.org/2000/svg";
  var PALETTE = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed", "#0891b2"];

  /* ---------- helpers ---------- */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function isNum(v) { return typeof v === "number"; }
  function fmt(v, digits) {
    var d = digits == null ? DIGITS : digits;
    if (v === null || v === undefined) return "—";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (Array.isArray(v)) return v.map(function (x) { return fmt(x, d); }).join(", ");
    if (!isNum(v)) return esc(v);
    if (!isFinite(v)) return v > 0 ? "∞" : (v < 0 ? "−∞" : "undefined");
    if (v === 0) return "0";
    var a = Math.abs(v);
    if (Number.isInteger(v) && a < 1e7) return String(v).replace("-", "−");
    if (a >= 1e5 || a < Math.pow(10, -d)) return v.toExponential(2).replace("-", "−");
    return v.toFixed(d).replace("-", "−");
  }
  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (html != null) e.innerHTML = html;
    return e;
  }
  function paramById(id) {
    for (var i = 0; i < SPEC.params.length; i++) if (SPEC.params[i].id === id) return SPEC.params[i];
    return null;
  }
  function dim(v, st) {
    if (typeof v === "string") return Math.max(1, Math.round(Number((st || state)[v]) || 1));
    return Math.max(1, Math.round(Number(v) || 1));
  }
  function clamp(x, p) {
    if (!isFinite(x)) x = 0;
    if (p.min != null && x < p.min) x = p.min;
    if (p.max != null && x > p.max) x = p.max;
    return x;
  }
  function fillValue(p, arr) {
    if (p.fill != null) return p.fill;
    if (arr && arr.length) return arr[arr.length - 1];
    return p.min != null ? p.min : 0;
  }
  function resizeVector(p, arr, n) {
    arr = Array.isArray(arr) ? arr.slice(0, n) : [];
    var f = fillValue(p, arr);
    while (arr.length < n) arr.push(f);
    return arr;
  }
  function resizeMatrix(p, m, rows, cols) {
    m = Array.isArray(m) ? m.slice(0, rows) : [];
    var f = p.fill != null ? p.fill : 0;
    for (var i = 0; i < rows; i++) {
      var row = Array.isArray(m[i]) ? m[i].slice(0, cols) : [];
      while (row.length < cols) row.push(f);
      m[i] = row;
    }
    return m;
  }
  function normalizeShapes(st) {
    SPEC.params.forEach(function (p) {
      if (p.type === "vector") st[p.id] = resizeVector(p, st[p.id], dim(p.length, st));
      if (p.type === "matrix") st[p.id] = resizeMatrix(p, st[p.id], dim(p.rows, st), dim(p.cols, st));
    });
    return st;
  }
  function defaults() {
    var st = {};
    SPEC.params.forEach(function (p) { st[p.id] = clone(p["default"]); });
    return normalizeShapes(st);
  }
  function run(st) { return CODE.compute(clone(st)); }
  function lookup(r, key) {
    if (Array.isArray(key)) return key;
    if (typeof key !== "string") return key;
    if (r && Object.prototype.hasOwnProperty.call(r, key)) return r[key];
    if (Object.prototype.hasOwnProperty.call(state, key)) return state[key];
    return undefined;
  }
  function entryLabel(p, i, j) {
    if (j != null) return (p.symbol || p.id) + "[" + (i + 1) + "," + (j + 1) + "]";
    if (p.labels && p.labels[i] != null) return esc(p.labels[i]);
    return (p.symbol || p.id) + "<sub>" + (i + 1) + "</sub>";
  }

  /* ---------- controls ---------- */
  function numberInput(p, value, onSet, label) {
    var inp = el("input", { type: "number", step: p.step != null ? p.step : "any", "aria-label": label });
    if (p.min != null) inp.min = p.min;
    if (p.max != null) inp.max = p.max;
    inp.value = value;
    inp.addEventListener("change", function () {
      var v = clamp(parseFloat(inp.value), p);
      inp.value = v;
      onSet(v);
    });
    return inp;
  }
  function buildControls() {
    controlsEl.innerHTML = "";
    SPEC.params.forEach(function (p) {
      var box = el("div", { "class": "ctl ctl-" + p.type, "data-param": p.id });
      var head = el("div", { "class": "ctl-head" });
      head.appendChild(el("span", { "class": "ctl-label", id: "lbl-" + p.id }, p.label || p.id));
      box.appendChild(head);
      if (p.type === "number") {
        var range = el("input", { type: "range", "aria-labelledby": "lbl-" + p.id, id: "ctl-" + p.id });
        range.min = p.min; range.max = p.max; range.step = p.step != null ? p.step : "any";
        range.value = state[p.id];
        var num = numberInput(p, state[p.id], function (v) {
          state[p.id] = v; range.value = v; onParamChange(p);
        }, (p.id) + " value");
        range.addEventListener("input", function () {
          state[p.id] = clamp(parseFloat(range.value), p); num.value = state[p.id]; onParamChange(p);
        });
        head.appendChild(num);
        box.appendChild(range);
      } else if (p.type === "toggle") {
        var cb = el("input", { type: "checkbox", id: "ctl-" + p.id, "aria-labelledby": "lbl-" + p.id });
        cb.checked = !!state[p.id];
        cb.addEventListener("change", function () { state[p.id] = cb.checked; onParamChange(p); });
        var sw = el("label", { "class": "switch" });
        sw.appendChild(cb);
        sw.appendChild(el("span", { "class": "switch-text" }, cb.checked ? "on" : "off"));
        cb.addEventListener("change", function () { sw.lastChild.textContent = cb.checked ? "on" : "off"; });
        head.appendChild(sw);
      } else if (p.type === "select") {
        var sel = el("select", { id: "ctl-" + p.id, "aria-labelledby": "lbl-" + p.id });
        (p.options || []).forEach(function (o) {
          var opt = el("option", null, esc(o.label != null ? o.label : o.value));
          opt.value = JSON.stringify(o.value);
          if (JSON.stringify(o.value) === JSON.stringify(state[p.id])) opt.selected = true;
          sel.appendChild(opt);
        });
        sel.addEventListener("change", function () { state[p.id] = JSON.parse(sel.value); onParamChange(p); });
        box.appendChild(sel);
      } else if (p.type === "vector") {
        var grid = el("div", { "class": "vec-grid" });
        state[p.id].forEach(function (v, i) {
          var cell = el("label", { "class": "vec-cell" });
          cell.appendChild(el("span", null, entryLabel(p, i)));
          cell.appendChild(numberInput(p, v, function (nv) { state[p.id][i] = nv; onParamChange(p); },
            p.id + " entry " + (i + 1)));
          grid.appendChild(cell);
        });
        box.appendChild(grid);
      } else if (p.type === "matrix") {
        var m = state[p.id];
        var table = el("table", { "class": "mat-input" });
        var thead = "<tr><th></th>";
        for (var c = 0; c < m[0].length; c++) thead += "<th>" + (p.col_labels && p.col_labels[c] != null ? esc(p.col_labels[c]) : "c" + (c + 1)) + "</th>";
        table.innerHTML = thead + "</tr>";
        m.forEach(function (row, i) {
          var tr = el("tr");
          tr.appendChild(el("th", null, p.row_labels && p.row_labels[i] != null ? esc(p.row_labels[i]) : "r" + (i + 1)));
          row.forEach(function (v, j) {
            var td = el("td");
            td.appendChild(numberInput(p, v, function (nv) { state[p.id][i][j] = nv; onParamChange(p); },
              p.id + " row " + (i + 1) + " column " + (j + 1)));
            tr.appendChild(td);
          });
          table.appendChild(tr);
        });
        var wrap = el("div", { "class": "scroll-x" });
        wrap.appendChild(table);
        box.appendChild(wrap);
      }
      if (p.help) box.appendChild(el("div", { "class": "ctl-help" }, p.help));
      controlsEl.appendChild(box);
    });
  }
  function dependsOnShape(id) {
    return SPEC.params.some(function (q) {
      return q.length === id || q.rows === id || q.cols === id;
    });
  }
  var pending = false;
  function onParamChange(p) {
    if (p && dependsOnShape(p.id)) { normalizeShapes(state); buildControls(); }
    if (!pending) { pending = true; requestAnimationFrame(function () { pending = false; render(); }); }
  }

  /* ---------- SVG primitives ---------- */
  function svg(w, h) {
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 " + w + " " + h);
    s.setAttribute("class", "chart");
    s.setAttribute("role", "img");
    return s;
  }
  function add(parent, tag, attrs, text) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    parent.appendChild(e);
    return e;
  }
  function plain(v, d) { return fmt(v, d).replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">"); }
  function niceTicks(lo, hi, n) {
    if (lo === hi) { lo -= 1; hi += 1; }
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / n)));
    var err = span / n / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var ticks = [], t = Math.ceil(lo / step - 1e-9) * step;
    for (; t <= hi + step * 1e-9; t += step) ticks.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    return ticks;
  }
  function tickLabel(t) {
    var a = Math.abs(t);
    if (a !== 0 && (a >= 1e5 || a < 1e-3)) return t.toExponential(1);
    return String(parseFloat(t.toPrecision(6))).replace("-", "−");
  }
  function finite(arr) { return arr.filter(function (x) { return isNum(x) && isFinite(x); }); }

  /* ---------- widgets ---------- */
  function seriesList(v) {
    if (v.series) return v.series;
    if (v.values) return [{ key: v.values, label: v.value_label || "" }];
    return [];
  }
  function bars(v, r, box) {
    var series = seriesList(v).map(function (s) { return { s: s, data: lookup(r, s.key) || [] }; });
    var n = Math.max.apply(null, series.map(function (x) { return x.data.length; }).concat([0]));
    if (!n) { box.appendChild(el("p", { "class": "muted" }, "No values to show.")); return; }
    var labels = lookup(r, v.labels) || [];
    var all = [];
    series.forEach(function (x) { all = all.concat(finite(x.data)); });
    var lo = Math.min(0, v.ymin != null ? v.ymin : Math.min.apply(null, all.concat([0])));
    var hi = Math.max(v.ymax != null ? v.ymax : -Infinity, Math.max.apply(null, all.concat([0])));
    if (hi === lo) hi = lo + 1;
    if (v.ymax == null) hi += (hi - lo) * 0.1;
    if (v.ymin == null && lo < 0) lo -= (hi - lo) * 0.08;
    var W = 560, H = 260, L = 50, R = 12, T = 22, B = 42;
    var s = svg(W, H), pw = W - L - R, ph = H - T - B;
    var Y = function (y) { return T + ph - (y - lo) / (hi - lo) * ph; };
    niceTicks(lo, hi, 5).forEach(function (t) {
      add(s, "line", { x1: L, x2: W - R, y1: Y(t), y2: Y(t), "class": t === 0 ? "axis" : "grid" });
      add(s, "text", { x: L - 6, y: Y(t) + 4, "text-anchor": "end", "class": "tick" }, tickLabel(t));
    });
    var gw = pw / n, bw = Math.min(56, gw * 0.8 / series.length);
    for (var i = 0; i < n; i++) {
      series.forEach(function (x, k) {
        var val = x.data[i];
        if (!isNum(val) || !isFinite(val)) return;
        var x0 = L + gw * i + (gw - bw * series.length) / 2 + bw * k;
        var y0 = Y(Math.max(0, val)), y1 = Y(Math.min(0, val));
        var rect = add(s, "rect", { x: x0, y: y0, width: bw - 2, height: Math.max(1, y1 - y0), fill: x.s.color || PALETTE[k % PALETTE.length], rx: 2 });
        add(rect, "title", {}, plain(val));
        add(s, "text", { x: x0 + (bw - 2) / 2, y: (val >= 0 ? y0 - 4 : y1 + 12), "text-anchor": "middle", "class": "val" }, plain(val));
      });
      add(s, "text", { x: L + gw * i + gw / 2, y: H - B + 16, "text-anchor": "middle", "class": "tick" },
        labels[i] != null ? String(labels[i]) : String(i + 1));
    }
    if (v.y_label) add(s, "text", { x: 12, y: T + ph / 2, transform: "rotate(-90 12 " + (T + ph / 2) + ")", "text-anchor": "middle", "class": "axis-label" }, v.y_label);
    if (v.x_label) add(s, "text", { x: L + pw / 2, y: H - 6, "text-anchor": "middle", "class": "axis-label" }, v.x_label);
    box.appendChild(s);
    legend(box, series.map(function (x) { return x.s; }));
  }
  function legend(box, series) {
    if (series.length < 2 && !(series[0] && series[0].label)) return;
    var lg = el("div", { "class": "legend" });
    series.forEach(function (s, k) {
      lg.appendChild(el("span", null, "<i style='background:" + (s.color || PALETTE[k % PALETTE.length]) + "'></i>" + (s.label || s.key)));
    });
    box.appendChild(lg);
  }
  function heatColor(val, lo, hi, diverging) {
    if (!isFinite(val)) return "#eee";
    if (diverging) {
      var m = Math.max(Math.abs(lo), Math.abs(hi)) || 1, t = Math.max(-1, Math.min(1, val / m));
      return t >= 0 ? "rgba(37,99,235," + (0.08 + 0.8 * t) + ")" : "rgba(220,38,38," + (0.08 + 0.8 * -t) + ")";
    }
    var u = hi > lo ? (val - lo) / (hi - lo) : 0.5;
    return "rgba(37,99,235," + (0.06 + 0.84 * u) + ")";
  }
  function matrixTable(m, opts, r) {
    opts = opts || {};
    if (!Array.isArray(m) || !m.length) return el("p", { "class": "muted" }, "—");
    if (!Array.isArray(m[0])) m = [m];
    var all = [];
    m.forEach(function (row) { all = all.concat(finite(row)); });
    var lo = opts.vmin != null ? opts.vmin : Math.min.apply(null, all.concat([0]));
    var hi = opts.vmax != null ? opts.vmax : Math.max.apply(null, all.concat([0]));
    var diverging = opts.colormap === "diverging" || (opts.colormap !== "sequential" && lo < 0);
    var rl = lookup(r, opts.row_labels) || [], cl = lookup(r, opts.col_labels) || [];
    var t = el("table", { "class": "heat" });
    var h = "<tr><th></th>";
    for (var c = 0; c < m[0].length; c++) h += "<th>" + esc(cl[c] != null ? cl[c] : c + 1) + "</th>";
    if (opts.row_sums) h += "<th class='sum'>row sum</th>";
    t.innerHTML = h + "</tr>";
    m.forEach(function (row, i) {
      var tr = "<tr><th>" + esc(rl[i] != null ? rl[i] : i + 1) + "</th>", sum = 0;
      row.forEach(function (val) {
        sum += isNum(val) ? val : 0;
        var bg = isNum(val) ? heatColor(val, lo, hi, diverging) : "transparent";
        var strong = isNum(val) && (diverging ? Math.abs(val) / (Math.max(Math.abs(lo), Math.abs(hi)) || 1) : (hi > lo ? (val - lo) / (hi - lo) : 0)) > 0.6;
        tr += "<td style='background:" + bg + (strong ? ";color:#fff" : "") + "'>" + fmt(val) + "</td>";
      });
      if (opts.row_sums) tr += "<td class='sum'>" + fmt(sum) + "</td>";
      t.insertAdjacentHTML("beforeend", tr + "</tr>");
    });
    var wrap = el("div", { "class": "scroll-x" });
    wrap.appendChild(t);
    return wrap;
  }
  function heatmap(v, r, box) { box.appendChild(matrixTable(lookup(r, v.value), v, r)); }
  function valueHTML(val) {
    if (Array.isArray(val)) {
      if (val.length && Array.isArray(val[0])) return matrixTable(val, {}, {}).outerHTML;
      return "<span class='chips'>" + val.map(function (x) { return "<span>" + fmt(x) + "</span>"; }).join("") + "</span>";
    }
    return "<span class='big'>" + fmt(val) + "</span>";
  }
  function pipeline(v, r, box) {
    var row = el("div", { "class": "pipeline" });
    (v.steps || []).forEach(function (st, i) {
      if (i) row.appendChild(el("div", { "class": "arrow", "aria-hidden": "true" }, "→"));
      var b = el("div", { "class": "step" });
      b.innerHTML = "<div class='step-label'>" + (st.label || st.key) + "</div>" +
        (st.math ? "<div class='step-math'>" + st.math + "</div>" : "") +
        "<div class='step-value'>" + valueHTML(lookup(r, st.key)) + "</div>" +
        (st.note ? "<div class='step-note'>" + st.note + "</div>" : "");
      row.appendChild(b);
    });
    box.appendChild(row);
  }
  function readout(v, r, box) {
    var g = el("div", { "class": "readout" });
    (v.items || []).forEach(function (it) {
      var val = lookup(r, it.key);
      g.appendChild(el("div", { "class": "ro" },
        "<div class='ro-label'>" + (it.label || it.key) + "</div><div class='ro-value'>" +
        (Array.isArray(val) ? valueHTML(val) : fmt(val, it.digits)) + (it.unit ? " <span class='unit'>" + esc(it.unit) + "</span>" : "") +
        "</div>" + (it.note ? "<div class='ro-note'>" + it.note + "</div>" : "")));
    });
    box.appendChild(g);
  }
  function table(v, r, box) {
    var cols = (v.columns || []).map(function (c) { return { c: c, data: lookup(r, c.key) }; });
    var n = 0;
    cols.forEach(function (x) { if (Array.isArray(x.data)) n = Math.max(n, x.data.length); });
    var rl = lookup(r, v.row_labels) || [];
    var h = "<table class='data'><tr><th>" + (v.row_header || "#") + "</th>" +
      cols.map(function (x) { return "<th>" + (x.c.label || x.c.key) + "</th>"; }).join("") + "</tr>";
    for (var i = 0; i < n; i++) {
      h += "<tr><th>" + esc(rl[i] != null ? rl[i] : i + 1) + "</th>" + cols.map(function (x) {
        var d = Array.isArray(x.data) ? x.data[i] : x.data;
        return "<td>" + fmt(d, x.c.digits) + "</td>";
      }).join("") + "</tr>";
    }
    (v.footer || []).forEach(function (f) {
      h += "<tr class='foot'><th colspan='" + cols.length + "'>" + (f.label || f.key) + "</th><td>" + fmt(lookup(r, f.key), f.digits) + "</td></tr>";
    });
    var wrap = el("div", { "class": "scroll-x" }, h + "</table>");
    box.appendChild(wrap);
  }
  function linePlot(box, xs, ys, opt) {
    var W = 560, H = 270, L = 54, R = 14, T = 16, B = 44;
    var s = svg(W, H), pw = W - L - R, ph = H - T - B;
    var allY = [];
    ys.forEach(function (y) { allY = allY.concat(finite(y.data)); });
    if (opt.markerY) allY = allY.concat(finite(opt.markerY));
    var fx = finite(xs);
    if (!fx.length || !allY.length) { box.appendChild(el("p", { "class": "muted" }, "Nothing to plot for these inputs.")); return; }
    var x0 = Math.min.apply(null, fx), x1 = Math.max.apply(null, fx);
    var y0 = opt.ymin != null ? opt.ymin : Math.min.apply(null, allY), y1 = opt.ymax != null ? opt.ymax : Math.max.apply(null, allY);
    if (opt.include_zero) { y0 = Math.min(0, y0); y1 = Math.max(0, y1); }
    if (y0 === y1) { y0 -= 1; y1 += 1; } else { var pad = (y1 - y0) * 0.06; if (opt.ymin == null) y0 -= pad; if (opt.ymax == null) y1 += pad; }
    if (x0 === x1) { x0 -= 1; x1 += 1; }
    var X = function (x) { return L + (x - x0) / (x1 - x0) * pw; };
    var Y = function (y) { return T + ph - (y - y0) / (y1 - y0) * ph; };
    niceTicks(y0, y1, 5).forEach(function (t) {
      if (t < y0 - 1e-12 || t > y1 + 1e-12) return;
      add(s, "line", { x1: L, x2: W - R, y1: Y(t), y2: Y(t), "class": "grid" });
      add(s, "text", { x: L - 6, y: Y(t) + 4, "text-anchor": "end", "class": "tick" }, tickLabel(t));
    });
    niceTicks(x0, x1, 6).forEach(function (t) {
      if (t < x0 - 1e-12 || t > x1 + 1e-12) return;
      add(s, "text", { x: X(t), y: T + ph + 16, "text-anchor": "middle", "class": "tick" }, tickLabel(t));
    });
    add(s, "line", { x1: L, x2: W - R, y1: T + ph, y2: T + ph, "class": "axis" });
    add(s, "line", { x1: L, x2: L, y1: T, y2: T + ph, "class": "axis" });
    ys.forEach(function (y, k) {
      var d = "", pen = false;
      xs.forEach(function (x, i) {
        var v = y.data[i];
        if (!isNum(v) || !isFinite(v) || !isFinite(x)) { pen = false; return; }
        d += (pen ? "L" : "M") + X(x).toFixed(1) + " " + Y(Math.max(y0, Math.min(y1, v))).toFixed(1);
        pen = true;
      });
      add(s, "path", { d: d, fill: "none", stroke: y.color || PALETTE[k % PALETTE.length], "stroke-width": 2.4 });
    });
    if (opt.markerX != null && isFinite(opt.markerX)) {
      add(s, "line", { x1: X(opt.markerX), x2: X(opt.markerX), y1: T, y2: T + ph, "class": "marker" });
      (opt.markerY || []).forEach(function (my, k) {
        if (!isNum(my) || !isFinite(my)) return;
        add(s, "circle", { cx: X(opt.markerX), cy: Y(Math.max(y0, Math.min(y1, my))), r: 5, fill: ys[k].color || PALETTE[k % PALETTE.length], stroke: "#fff", "stroke-width": 1.5 });
        add(s, "text", { x: X(opt.markerX) + 8, y: Y(my) - 8, "class": "val" }, plain(my));
      });
    }
    if (opt.x_label) add(s, "text", { x: L + pw / 2, y: H - 6, "text-anchor": "middle", "class": "axis-label" }, opt.x_label);
    if (opt.y_label) add(s, "text", { x: 12, y: T + ph / 2, transform: "rotate(-90 12 " + (T + ph / 2) + ")", "text-anchor": "middle", "class": "axis-label" }, opt.y_label);
    box.appendChild(s);
    legend(box, ys.map(function (y) { return { key: y.key, label: y.label, color: y.color }; }));
  }
  function sweep(v, r, box) {
    var p = paramById(v.x_param);
    if (!p) return;
    var idx = p.type === "vector" ? Math.max(0, Math.min(state[p.id].length - 1, v.x_index || 0)) : null;
    var lo = v.x_min != null ? v.x_min : p.min, hi = v.x_max != null ? v.x_max : p.max;
    var n = p.step && Number.isInteger(p.step) && (hi - lo) / p.step <= 80 ? Math.round((hi - lo) / p.step) + 1 : 81;
    var xs = [], ys = (v.y || []).map(function (y) { return { key: y.key, label: y.label, color: y.color, data: [] }; });
    for (var i = 0; i < n; i++) {
      var x = lo + (hi - lo) * i / (n - 1);
      var st = clone(state);
      if (idx === null) st[p.id] = x; else st[p.id][idx] = x;
      normalizeShapes(st);
      var out;
      try { out = run(st); } catch (e) { out = {}; }
      xs.push(x);
      ys.forEach(function (y) { y.data.push(out[y.key]); });
    }
    var name = (p.plain_label || p.id) + (idx === null ? "" : " — entry " + (idx + 1));
    linePlot(box, xs, ys, {
      markerX: idx === null ? state[p.id] : state[p.id][idx], markerY: ys.map(function (y) { return r[y.key]; }),
      x_label: v.x_label || name, y_label: v.y_label, ymin: v.ymin, ymax: v.ymax, include_zero: v.include_zero
    });
    box.appendChild(el("p", { "class": "caption" }, "Curve: every point is recomputed with only " + esc(name) + " changed and all other inputs held at your current settings; the dot marks your current value."));
  }
  function curve(v, r, box) {
    var xs = lookup(r, v.x) || [];
    var ys = (v.y || []).map(function (y) { return { key: y.key, label: y.label, color: y.color, data: lookup(r, y.key) || [] }; });
    var mx = v.marker_x != null ? lookup(r, v.marker_x) : null;
    var my = v.marker_y ? v.marker_y.map(function (k) { return lookup(r, k); }) : null;
    linePlot(box, xs, ys, { markerX: mx, markerY: my, x_label: v.x_label, y_label: v.y_label, ymin: v.ymin, ymax: v.ymax, include_zero: v.include_zero });
  }
  function custom(v, r, box) {
    var fn = CODE.draw && CODE.draw[v.id];
    if (!fn) return;
    var out = fn(clone(state), r);
    if (typeof out === "string" && /^\s*<svg[\s>]/i.test(out) && !/<script|\son\w+\s*=|href\s*=\s*["']?(https?:|javascript:)/i.test(out)) {
      var holder = el("div", { "class": "custom-svg" }, out);
      box.appendChild(holder);
    } else {
      box.appendChild(el("p", { "class": "muted" }, "Diagram unavailable for these inputs."));
    }
  }
  var WIDGETS = { bars: bars, heatmap: heatmap, pipeline: pipeline, readout: readout, table: table, sweep: sweep, curve: curve, svg: custom };

  /* ---------- checks ---------- */
  function evalCheck(c, st) {
    var fn = CODE.checks && CODE.checks[c.id];
    if (!fn) return { ok: false, err: "missing" };
    try {
      var out = run(st);
      var ok = !!fn.test(st, out);
      return { ok: ok, shown: fn.show ? fn.show(st, out) : null };
    } catch (e) { return { ok: false, err: e.message }; }
  }
  function renderChecks() {
    if (!checksEl) return;
    var items = "";
    (SPEC.checks || []).forEach(function (c) {
      var st = state, tag = "live";
      if (c.params) { st = normalizeShapes(Object.assign(defaults(), clone(c.params))); tag = "fixed example"; }
      var res = evalCheck(c, st);
      items += "<li class='" + (res.ok ? "pass" : "fail") + "'><span class='mark'>" + (res.ok ? "✓" : "✗") + "</span> " +
        c.name + (res.shown != null ? " <span class='shown'>(" + fmt(res.shown) + ")</span>" : "") +
        " <span class='tag'>" + tag + "</span></li>";
    });
    checksEl.innerHTML = items || "<li>No checks defined.</li>";
  }

  /* ---------- render loop ---------- */
  var viewBoxes = [];
  function buildViews() {
    viewsEl.innerHTML = "";
    viewBoxes = (SPEC.views || []).map(function (v) {
      var fig = el("figure", { "class": "view view-" + v.type + (v.wide ? " wide" : "") });
      if (v.title) fig.appendChild(el("figcaption", null, v.title));
      var body = el("div", { "class": "view-body" });
      fig.appendChild(body);
      if (v.caption) fig.appendChild(el("p", { "class": "caption" }, v.caption));
      viewsEl.appendChild(fig);
      return body;
    });
  }
  function render() {
    var r;
    try {
      r = run(state);
      if (!r || typeof r !== "object") throw new Error("compute returned no object");
      errorEl.hidden = true;
      errorEl.textContent = "";
    } catch (e) {
      errorEl.hidden = false;
      errorEl.textContent = "These inputs could not be computed: " + e.message;
      return;
    }
    if (r.warning) { errorEl.hidden = false; errorEl.textContent = String(r.warning); }
    (SPEC.views || []).forEach(function (v, i) {
      var box = viewBoxes[i];
      box.innerHTML = "";
      try { (WIDGETS[v.type] || function () {})(v, r, box); }
      catch (e) { box.innerHTML = "<p class='muted'>View unavailable: " + esc(e.message) + "</p>"; }
    });
    renderChecks();
  }
  function applyPreset(values, button) {
    state = normalizeShapes(Object.assign(defaults(), clone(values || {})));
    normalizeShapes(state);
    buildControls();
    render();
    document.querySelectorAll(".preset.active").forEach(function (b) { b.classList.remove("active"); });
    if (button) button.classList.add("active");
    var lab = document.getElementById("lab");
    if (lab && lab.scrollIntoView) lab.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  window.__explainerApi = { state: function () { return clone(state); }, compute: function () { return run(state); }, applyPreset: applyPreset };
  document.querySelectorAll("[data-preset]").forEach(function (b) {
    b.addEventListener("click", function () { applyPreset(SPEC.presets[b.getAttribute("data-preset")], b); });
  });
  var reset = document.getElementById("reset");
  if (reset) reset.addEventListener("click", function () { applyPreset({}, null); });
  if (typeof CODE.compute !== "function") {
    errorEl.hidden = false;
    errorEl.textContent = "The calculation code failed to load.";
    return;
  }
  state = defaults();
  buildControls();
  buildViews();
  render();
})();
