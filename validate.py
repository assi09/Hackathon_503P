"""Deterministic checks on a generated spec: structure, executable math, edge cases, grounding.

compute(p), checks and SVG drawings are executed in an embedded V8 (mini-racer)
over defaults, presets, boundary values and seeded random inputs.
"""

from __future__ import annotations

import json
import random
import time
import re
from dataclasses import dataclass, field
from typing import Any

from page import code_bundle, latex_ok

try:
    from py_mini_racer import MiniRacer
except Exception:  # pragma: no cover - only if the wheel is unavailable
    MiniRacer = None

PARAM_TYPES = {"number", "toggle", "select", "vector", "matrix"}
VIEW_TYPES = {"pipeline", "bars", "heatmap", "table", "readout", "sweep", "curve", "svg"}
FORBIDDEN = re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|importScripts|eval|Function|constructor|"
                       r"document|window|globalThis|navigator|localStorage|sessionStorage|indexedDB|Worker|"
                       r"postMessage|Math\.random|setTimeout|setInterval)\b|\bimport\s*\(|https?:|javascript:")
UNSAFE_SVG = re.compile(r"<\s*(script|foreignObject|iframe|image|use|a|style)\b|\son\w+\s*=|href\s*=|url\s*\(", re.I)
JS_LIMITS = {"timeout_sec": 8, "max_memory": 256 * 1024 * 1024}
MAX_COMPUTE_MS = 10.0
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

HARNESS = r"""
var SPEC = %s;
function clone(v) { return JSON.parse(JSON.stringify(v)); }
function dim(v, st) { if (typeof v === "string") return Math.max(1, Math.round(Number(st[v]) || 1)); return Math.max(1, Math.round(Number(v) || 1)); }
function fillValue(p, arr) { if (p.fill != null) return p.fill; if (arr && arr.length) return arr[arr.length - 1]; return p.min != null ? p.min : 0; }
function normalizeShapes(st) {
  SPEC.params.forEach(function (p) {
    if (p.type === "vector") { var n = dim(p.length, st), a = Array.isArray(st[p.id]) ? st[p.id].slice(0, n) : []; var f = fillValue(p, a); while (a.length < n) a.push(f); st[p.id] = a; }
    if (p.type === "matrix") { var R = dim(p.rows, st), C = dim(p.cols, st), m = Array.isArray(st[p.id]) ? st[p.id].slice(0, R) : [], f2 = p.fill != null ? p.fill : 0;
      for (var i = 0; i < R; i++) { var row = Array.isArray(m[i]) ? m[i].slice(0, C) : []; while (row.length < C) row.push(f2); m[i] = row; } st[p.id] = m; }
  });
  return st;
}
function defaults() { var st = {}; SPEC.params.forEach(function (p) { st[p.id] = clone(p["default"]); }); return normalizeShapes(st); }
function bad(v, path, out) {
  if (typeof v === "number") { if (!isFinite(v)) out.push(path + "=" + v); }
  else if (Array.isArray(v)) { for (var i = 0; i < v.length && out.length < 5; i++) bad(v[i], path + "[" + i + "]", out); }
  else if (v && typeof v === "object") { for (var k in v) bad(v[k], path + "." + k, out); }
  return out;
}
function runState(st) {
  var res = { ok: true };
  try {
    var r = __explainer.compute(clone(st));
    if (!r || typeof r !== "object" || Array.isArray(r)) return { ok: false, error: "compute did not return an object" };
    res.r = r;
    res.nonfinite = bad(r, "r", []);
    res.checks = {};
    for (var id in __explainer.checks) {
      try { res.checks[id] = !!__explainer.checks[id].test(clone(st), r); }
      catch (e) { res.checks[id] = "error: " + e.message; }
    }
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  return res;
}
function evaluate(states) {
  return JSON.stringify(states.map(function (s) {
    var st = normalizeShapes(Object.assign(defaults(), clone(s)));
    var out = runState(st);
    if (!out.ok) return out;
    return { ok: true, nonfinite: out.nonfinite, checks: out.checks, warning: out.r.warning || null,
             keys: Object.keys(out.r), sig: JSON.stringify(out.r).slice(0, 20000) };
  }));
}
function numbersOver(states) {
  var out = [];
  function walk(v) { if (typeof v === "number" && isFinite(v)) out.push(v); else if (Array.isArray(v)) { if (v.length <= 12) v.forEach(walk); }
                     else if (v && typeof v === "object") for (var k in v) walk(v[k]); }
  states.forEach(function (s) { try { var st = normalizeShapes(Object.assign(defaults(), clone(s))); walk(st); walk(__explainer.compute(clone(st))); } catch (e) {} });
  return JSON.stringify(out);
}
function resultOf(s) { var st = normalizeShapes(Object.assign(defaults(), clone(s))); return JSON.stringify(__explainer.compute(clone(st))); }
function drawAll(s) {
  var st = normalizeShapes(Object.assign(defaults(), clone(s))); var r = __explainer.compute(clone(st)); var out = {};
  for (var id in __explainer.draw) { try { out[id] = String(__explainer.draw[id](clone(st), r)).slice(0, 200000); } catch (e) { out[id] = "error: " + e.message; } }
  return JSON.stringify(out);
}
"""


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _shape_of(value: Any) -> str:
    if isinstance(value, list):
        if value and isinstance(value[0], list):
            return "matrix"
        return "vector"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _flat(v: Any) -> list:
    return [y for x in v for y in _flat(x)] if isinstance(v, list) else [v]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def normalize_spec(spec: dict, rep: Report) -> dict:
    """Cheap local repairs that do not need the model: ids, clamping, missing optional fields."""
    spec.setdefault("checks", [])
    for i, c in enumerate(spec["checks"]):
        if isinstance(c, dict):
            c["id"] = f"c{i + 1}"
    for i, v in enumerate(spec.get("views", []) or []):
        if isinstance(v, dict) and v.get("type") == "svg":
            v["id"] = f"v{i + 1}"
    params = {p.get("id"): p for p in spec.get("params", []) if isinstance(p, dict)}
    for p in params.values():
        if p.get("type") == "vector" and p.get("default") is None:
            ref = p.get("length")
            n = params.get(ref, {}).get("default") if isinstance(ref, str) else ref
            if isinstance(n, (int, float)) and p.get("fill") is not None:
                p["default"] = [p["fill"]] * max(1, int(n))
                rep.fixes.append(f"built missing default for vector {p.get('id')} from fill")
        if p.get("type") == "number":
            for k in ("min", "max", "default"):
                if isinstance(p.get(k), str):
                    try:
                        p[k] = float(p[k])
                    except ValueError:
                        pass
            if isinstance(p.get("default"), (int, float)) and isinstance(p.get("min"), (int, float)) \
                    and isinstance(p.get("max"), (int, float)) and not p["min"] <= p["default"] <= p["max"]:
                p["default"] = min(max(p["default"], p["min"]), p["max"])
                rep.fixes.append(f"clamped default of {p['id']}")
    ex = spec.get("explorations")
    if isinstance(ex, list) and len(ex) > 2:  # keep the first two complete ones instead of paying for a repair
        complete = [e for e in ex if isinstance(e, dict) and e.get("preset")
                    and all(isinstance(e.get(k), str) and e.get(k) for k in ("title", "change", "observe", "why"))]
        if len(complete) >= 2:
            spec["explorations"] = complete[:2]
            rep.fixes.append(f"kept the first 2 of {len(ex)} explorations")
    for e in spec.get("explorations", []) or []:
        preset = e.get("preset") if isinstance(e, dict) else None
        if not isinstance(preset, dict):
            continue
        for k in list(preset):
            if k not in params:
                del preset[k]
                rep.fixes.append(f"removed unknown preset key {k}")
            elif params[k].get("type") == "number" and isinstance(preset[k], (int, float)):
                lo, hi = params[k].get("min"), params[k].get("max")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and not lo <= preset[k] <= hi:
                    preset[k] = min(max(preset[k], lo), hi)
                    rep.fixes.append(f"clamped preset value {k}")
    return spec


def check_structure(spec: dict, rep: Report) -> None:
    for key, typ in (("title", str), ("idea", str), ("why_it_matters", str), ("compute", str),
                     ("params", list), ("views", list), ("explorations", list), ("symbols", list),
                     ("grounding", dict), ("paper", dict)):
        if not isinstance(spec.get(key), typ) or not spec.get(key):
            rep.errors.append(f"missing or invalid field '{key}' (expected {typ.__name__})")
    if rep.errors:
        return
    ids = set()
    for p in spec["params"]:
        pid = p.get("id", "")
        if not IDENT.match(str(pid)) or pid in ids:
            rep.errors.append(f"param id {pid!r} must be a unique JS identifier")
        ids.add(pid)
        t = p.get("type")
        if t not in PARAM_TYPES:
            rep.errors.append(f"param {pid}: unknown type {t!r}")
        if t == "number":
            if not all(isinstance(p.get(k), (int, float)) for k in ("min", "max", "default")) or p["min"] >= p["max"]:
                rep.errors.append(f"param {pid}: number needs numeric min < max and default")
        if t in ("vector", "matrix"):
            for k in (("length",) if t == "vector" else ("rows", "cols")):
                ref = p.get(k)
                if isinstance(ref, str):
                    target = next((q for q in spec["params"] if q.get("id") == ref), None)
                    if not target or target.get("type") != "number":
                        rep.errors.append(f"param {pid}: {k} refers to {ref!r}, which is not a number param")
                elif not isinstance(ref, int):
                    rep.errors.append(f"param {pid}: {k} must be an int or a number-param id")
            if isinstance(p.get("default"), str):
                try:
                    p["default"] = json.loads(p["default"])
                except ValueError:
                    pass
            want = "vector" if t == "vector" else "matrix"
            if _shape_of(p.get("default")) != want:
                rep.errors.append(f"param {pid}: default must be a {want} (got {json.dumps(p.get('default'))[:80]})")
            elif all(v == 0 for v in _flat(p["default"])):
                rep.errors.append(f"param {pid}: default is all zeros, a degenerate starting point; use varied, "
                                  "meaningful default values")
        if t == "select" and not p.get("options"):
            rep.errors.append(f"param {pid}: select needs options")
    if len(spec["params"]) < 2:
        rep.errors.append("need at least two controls (params)")
    for v in spec["views"]:
        if v.get("type") not in VIEW_TYPES:
            rep.errors.append(f"view '{v.get('title')}' ({v.get('type')}) has an unknown view type")
        if v.get("type") == "sweep":
            target = next((q for q in spec["params"] if q.get("id") == v.get("x_param")), None)
            if not target or target.get("type") not in ("number", "vector", "matrix"):
                rep.errors.append(f"view '{v.get('title')}' (sweep) has x_param {v.get('x_param')!r}, which is not a "
                                  "number, vector or matrix param (use x_index [row, col] for a matrix entry)")
    if len(spec["explorations"]) != 2:
        rep.errors.append(f"need exactly 2 explorations, got {len(spec['explorations'])}")
    for e in spec["explorations"]:
        for k in ("title", "change", "observe", "why"):
            if not isinstance(e.get(k), str) or not e.get(k):
                rep.errors.append(f"exploration missing '{k}'")
        if not isinstance(e.get("preset"), dict) or not e["preset"]:
            rep.errors.append(f"exploration {e.get('title')!r} needs a non-empty preset")
    if len(spec.get("checks", [])) < 2:
        rep.errors.append("need at least 2 executable checks")
    for c in spec.get("checks", []):
        if not isinstance(c.get("test"), str) or not isinstance(c.get("name"), str):
            rep.errors.append("each check needs string 'name' and 'test'")
    if not (spec.get("misconception") or {}).get("text"):
        rep.errors.append("missing misconception.text")
    code = spec["compute"] + " ".join(str(c.get("test", "")) + str(c.get("show", "")) for c in spec.get("checks", [])) \
        + " ".join(str(v.get("code", "")) for v in spec["views"])
    m = FORBIDDEN.search(re.sub(r"https?://www\.w3\.org/[\w/.#-]*", "", code))  # SVG namespaces are fine
    if m:
        rep.errors.append(f"generated code uses forbidden API {m.group(0)!r}")
    for e in spec.get("equations", []) + [{"latex": s.get("symbol", "")} for s in spec["symbols"]]:
        if e.get("latex") and not latex_ok(e["latex"]):
            rep.warnings.append(f"LaTeX not convertible: {e['latex'][:60]}")


_CITE = re.compile(r"\b(Eqs?\.|Equations?|Algorithm|Theorem|Lemma|Definition|Proposition|Corollary|Figure|Fig\.|Table)"
                   r"\s*((?:\(?\d+(?:\.\d+)?\)?\s*(?:[-–,]|and|&)?\s*)+)", re.I)


def check_brief_citations(spec: dict, brief: str, rep: Report) -> None:
    """Brief-only mode: any section/equation/algorithm/theorem/figure/table number the page cites must be named in
    the brief, since nothing else about the paper is known."""
    paper, g = spec.get("paper", {}), spec.get("grounding", {})
    cited = " ".join([str(paper.get("section", "")), str(paper.get("equation", ""))]
                     + [str(x) for x in g.get("from_paper", [])] + [str(e.get("caption", "")) for e in spec.get("equations", [])])
    refs = [(k, n) for k, nums in _CITE.findall(cited) for n in re.findall(r"\d+(?:\.\d+)?", nums)]
    refs += [("Section", n) for n in re.findall(r"\b(?:Section|Sec\.|§)\s*(\d+(?:\.\d+)*)", cited)]
    missing = sorted({f"{k} {n}" for k, n in refs if not re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", brief)})
    if missing:
        rep.errors.append(f"citations {missing} are not named in the brief; the source text was not available, so "
                          "cite only what the brief names and describe everything else without numbers")
    if g.get("quotes"):
        g["quotes"] = []
        rep.fixes.append("removed quotes: no source text was available")
    known = _name(brief)
    for key in ("title", "authors", "year"):
        value = str(paper.get(key) or "")
        words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", value)]
        if value and (not words or sum(_name(w) in known for w in words) / len(words) < 0.6):
            paper[key] = ""
            rep.fixes.append(f"cleared paper.{key}: not stated in the brief or URL")


def check_citations(spec: dict, excerpt: str | None, rep: Report) -> None:
    """Every numbered equation/algorithm/theorem/figure/table the page attributes to the paper must appear in the
    source text we read. (Brief-only mode uses check_brief_citations instead.)"""
    if not excerpt:
        return
    g, paper = spec.get("grounding", {}), spec.get("paper", {})
    cited = " ".join([str(paper.get("section", "")), str(paper.get("equation", ""))]
                     + [str(x) for x in g.get("from_paper", [])]
                     + [str(e.get("caption", "")) for e in spec.get("equations", [])])
    missing = []
    for kind, nums in _CITE.findall(cited):
        k = kind.lower()
        for n in re.findall(r"\d+(?:\.\d+)?", nums):
            if k.startswith(("eq", "equation")):
                found = re.search(rf"\(\s*{re.escape(n)}\s*\)\s*$", excerpt, re.M)  # an equation tag ends its line
            else:
                label = "fig(?:ure|\\.)" if k.startswith("fig") else re.escape(k.rstrip("s"))
                found = re.search(rf"\b{label}\s*{re.escape(n)}\b", excerpt, re.I)
            if not found:
                missing.append(f"{kind} {n}")
    if missing:
        rep.errors.append(f"citations {sorted(set(missing))} do not appear in the source excerpt; cite only numbers "
                          "that appear there, otherwise describe the location (e.g. 'unnumbered equation in "
                          "Section 3.5') in paper, grounding and equation captions")
    rep.stats["citations_checked"] = len(_CITE.findall(cited))


def check_grounding(spec: dict, excerpt: str | None, rep: Report) -> None:
    g = spec.setdefault("grounding", {})
    for k in ("from_paper", "our_simplifications", "quotes"):
        if not isinstance(g.get(k), list):
            g[k] = []
    if not g["from_paper"] or not g["our_simplifications"]:
        rep.errors.append("grounding needs non-empty from_paper and our_simplifications lists")
    kept = []
    hay = _norm(excerpt or "")
    for q in g["quotes"]:
        if excerpt and isinstance(q, str) and len(_norm(q)) >= 20 and _norm(q) in hay:
            kept.append(q)
        else:
            rep.fixes.append(f"dropped unverifiable quote: {str(q)[:60]}")
    g["quotes"] = kept
    rep.stats["verified_quotes"] = len(kept)


def _random_state(spec: dict, rng: random.Random, mode: str) -> dict:
    st: dict[str, Any] = {}
    for p in spec["params"]:
        t, lo, hi = p.get("type"), p.get("min", 0), p.get("max", 1)
        step = p.get("step") or 0
        if not isinstance(lo, (int, float)):
            lo = 0
        if not isinstance(hi, (int, float)):
            hi = lo + 1

        def val() -> float:
            if mode == "min":
                return lo
            if mode == "max":
                return hi
            if mode == "zero":
                return min(max(0, lo), hi)
            x = rng.uniform(lo, hi)
            return round(round((x - lo) / step) * step + lo, 10) if step else x

        if t == "number":
            st[p["id"]] = val()
        elif t == "toggle":
            st[p["id"]] = rng.random() < 0.5 if mode == "rand" else mode == "max"
        elif t == "select":
            opts = p.get("options") or [{}]
            st[p["id"]] = rng.choice(opts).get("value")
    for p in spec["params"]:
        if p.get("type") in ("vector", "matrix"):
            def n(ref: Any) -> int:
                v = st.get(ref, next((q.get("default") for q in spec["params"] if q.get("id") == ref), 1)) \
                    if isinstance(ref, str) else ref
                return max(1, int(round(float(v or 1))))
            lo, hi, step = p.get("min", 0), p.get("max", 1), p.get("step") or 0

            def val() -> float:
                if mode == "min":
                    return lo
                if mode == "max":
                    return hi
                if mode == "zero":
                    return min(max(0, lo), hi)
                x = rng.uniform(lo, hi)
                return round(round((x - lo) / step) * step + lo, 10) if step else x
            if p["type"] == "vector":
                st[p["id"]] = [val() for _ in range(n(p.get("length")))]
            else:
                st[p["id"]] = [[val() for _ in range(n(p.get("cols")))] for _ in range(n(p.get("rows")))]
    return st


def _one_hot_states(spec: dict) -> list[dict]:
    out = []
    for p in spec["params"]:
        if p.get("type") == "vector" and isinstance(p.get("default"), list) and len(p["default"]) > 1:
            lo, hi = p.get("min", 0), p.get("max", 1)
            base = [lo] * len(p["default"])
            base[0] = hi
            out.append({p["id"]: base})
    return out


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_view_shapes(spec: dict, r: dict, rep: Report) -> None:
    """Each widget gets the kind of data it can draw (checked on the default inputs)."""
    state = {p["id"]: p.get("default") for p in spec["params"]}

    def get(key: Any) -> Any:
        return r.get(key, state.get(key)) if isinstance(key, str) else key

    def need(v: dict, key: Any, kind: str) -> None:
        val = get(key)
        ok = {
            "number": _is_num(val),
            "numbers": isinstance(val, list) and bool(val) and all(_is_num(x) or x is None for x in val),
            "matrix": isinstance(val, list) and bool(val) and all(isinstance(row, list) for row in val),
            "cells": (isinstance(val, list) and all(not isinstance(x, dict) for x in val)) or _is_num(val),
            "numbers_or_matrix": isinstance(val, list) and bool(val) and (
                all(_is_num(x) or x is None for x in val)
                or all(isinstance(row, list) and all(_is_num(x) or x is None for x in row) for row in val)),
            "number_or_numbers": _is_num(val) or (isinstance(val, list) and bool(val) and all(_is_num(x) for x in val)),
        }[kind]
        if not ok:
            want = {"number": "a single number", "numbers": "a flat array of numbers",
                    "numbers_or_matrix": "an array of numbers or an array of rows",
                    "number_or_numbers": "a number or a flat array of numbers",
                    "matrix": "an array of rows", "cells": "a flat array of numbers/strings"}[kind]
            rep.errors.append(f"view '{v.get('title')}' ({v.get('type')}): key {key!r} must be {want}, "
                              f"but compute returns {json.dumps(val)[:80]}")

    for v in spec["views"]:
        t = v.get("type")
        if t == "bars":
            series = v.get("series") or ([{"key": v["values"]}] if v.get("values") else [])
            if series and all(_is_num(get(srs.get("key"))) for srs in series):
                continue  # one bar per single-number key
            for srs in series:
                need(v, srs.get("key"), "numbers_or_matrix")
        elif t == "heatmap":
            need(v, v.get("value"), "numbers_or_matrix")  # a flat array is drawn as one row
        elif t == "sweep":
            for y in v.get("y", []):
                need(v, y.get("key"), "number_or_numbers")
        elif t == "curve":
            need(v, v.get("x"), "numbers")
            for y in v.get("y", []):
                need(v, y.get("key"), "numbers")
        elif t == "table":
            for c in v.get("columns", []):
                need(v, c.get("key"), "cells")


NUM = re.compile(r"(?<![A-Za-z_^])[-−]?\d+(?:\.\d+)?(?:e[-−]?\d+)?")


def _numbers(text: str) -> list[tuple[str, float]]:
    out = []
    # Inside $math$, drop sub/superscripts (x_4, \sigma^2, 10^{-3}) but keep stated values (\sigma^2 = 11.5).
    text = re.sub(r"\$([^$]*)\$", lambda m: " " + re.sub(r"[_^](\{[^{}]*\}|\\?\w)", " ", m.group(1)) + " ", text)
    text = re.sub(r"\\[A-Za-z]+", " ", text)
    for m in NUM.finditer(text):
        tok = m.group(0).replace("−", "-")
        try:
            out.append((tok, float(tok)))
        except ValueError:
            pass
    return out


def _supported(tok: str, x: float, pool: list[float]) -> bool:
    if abs(x) <= 10 and float(x).is_integer():
        return True  # counts, indices, small integers (n = 4, p_t = 1, ...)
    mantissa, _, exp = tok.lower().partition("e")
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    scale = 10 ** int(exp.replace("−", "-")) if exp else 1  # 1.05e-5 is precise to 0.005e-5, not 0.005
    tol = max(0.5 * 10 ** -decimals * scale, 1e-12) + 0.011 * abs(x)
    return any(abs(v - x) <= tol or abs(abs(v) - abs(x)) <= tol for v in pool)


def _round(v: Any) -> Any:
    if isinstance(v, list):
        return [_round(x) for x in v]
    return float(f"{v:.4g}") if isinstance(v, float) else v


GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon", "θ": "theta",
         "λ": "lambda", "μ": "mu", "σ": "sigma", "τ": "tau", "ρ": "rho", "η": "eta", "ω": "omega", "π": "pi",
         "φ": "phi", "ϕ": "phi", "κ": "kappa", "ν": "nu", "ξ": "xi", "ζ": "zeta", "χ": "chi", "ψ": "psi"}


def _name(s: str) -> str:
    s = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|mathit)\{([^}]*)\}", r"\1", str(s))
    for g, n in GREEK.items():
        s = s.replace(g, n)
    return re.sub(r"[\\{}$\s_]", "", s).lower()


def _aliases(spec: dict) -> dict[str, tuple[str, int | None]]:
    """Names a sentence may use for a control: its id, symbol, LaTeX symbols in its label, Greek letters."""
    out: dict[str, tuple[str, int | None]] = {}
    for p in spec["params"]:
        names = {p["id"], p.get("symbol") or ""} | set(re.findall(r"\$([^$]+)\$", str(p.get("label", ""))))
        for n in filter(None, (_name(x) for x in names)):
            out.setdefault(n, (p["id"], None))
    return out


_ASSIGN = re.compile(r"(?<![\w.])([A-Za-z][A-Za-z0-9_]*?)(?:_?\{?(\d+)\}?)?\s*(?:=|→|->|\bto\b)\s*(-?\d+(?:\.\d+)?(?:e-?\d+)?)")
_SKIP_NUM = re.compile(r"^\s*(?:x\b|×|%|-?fold|times)")
_REF_BEFORE = re.compile(r"(?:section|sec\.|eq\.|eqs\.|equation|figure|fig\.|table|algorithm|theorem|step|page|appendix)\s*\(?$", re.I)


def _sentence_state(sentence: str, preset: dict, spec: dict, aliases: dict) -> dict:
    """The control state a sentence talks about: the preset plus any 'name = value' settings it names."""
    state = json.loads(json.dumps(preset))
    text = sentence
    for g, n in GREEK.items():
        text = text.replace(g, n)
    text = re.sub(r"\\(?:mathrm|text)\{([^}]*)\}", r"\1", text).replace("$", " ").replace("\\", "")
    params = {p["id"]: p for p in spec["params"]}
    low = text.lower()
    for p in spec["params"]:
        if p.get("type") != "toggle":
            continue
        words = {p["id"].lower()} | {w.lower() for w in re.findall(r"[A-Za-z]{4,}", re.sub(r"\$[^$]*\$", " ", str(p.get("label", ""))))}
        stems = {w[:4] for w in words if w not in ("with", "show", "turn", "when", "from", "each", "into")}
        for stem in stems:
            off = re.search(rf"\b(?:off|disabled?|without|no)\b[^.;]{{0,25}}\b{stem}\w*|\b{stem}\w*\b[^.;]{{0,15}}\b(?:off|disabled|false)\b", low)
            on = re.search(rf"\b(?:on|enabled?|with)\b[^.;]{{0,10}}\b{stem}\w*\b(?![^.;]{{0,15}}\boff\b)|\b{stem}\w*\b[^.;]{{0,15}}\b(?:on|enabled|true)\b", low)
            if off:
                state[p["id"]] = False
                break
            if on:
                state[p["id"]] = True
                break
    for m in _ASSIGN.finditer(text):
        name, idx, val = _name(m.group(1)), m.group(2), float(m.group(3))
        target = aliases.get(name + (idx or "")) or aliases.get(name)
        if not target:
            continue
        pid = target[0]
        p = params[pid]
        if p.get("type") == "vector" and idx and target is aliases.get(name):
            vec = list(state.get(pid, p.get("default") or []))
            k = int(idx) - 1
            if 0 <= k < len(vec):
                vec[k] = val
                state[pid] = vec
        elif p.get("type") == "number" and not idx:
            state[pid] = val
    return state


def _claimed_numbers(sentence: str) -> list[tuple[str, float]]:
    out = []
    for m in NUM.finditer(sentence):
        tok = m.group(0).replace("−", "-")
        try:
            x = float(tok)
        except ValueError:
            continue
        if _SKIP_NUM.match(sentence[m.end():]) or _REF_BEFORE.search(sentence[:m.start()]):
            continue
        if x != 0 and abs(x) >= 10 and float(f"{abs(x):.0e}") == abs(x) and "." not in tok:
            continue  # round powers of ten are scale words (100x, 1000), not computed claims
        if "." in tok or "e" in tok.lower() or abs(x) > 10:
            out.append((tok, x))
    return out


def check_exploration_numbers(spec: dict, ctx: Any, rep: Report) -> None:
    """Accuracy: each number an exploration states must be what compute() returns in the state that sentence
    describes (the preset plus any 'control = value' settings it names)."""
    aliases = _aliases(spec)
    constants = [x for _, x in _numbers(json.dumps(spec.get("equations", [])))]  # not slider ranges
    checked = 0
    for i, e in enumerate(spec["explorations"]):
        preset = e.get("preset") or {}
        bad, states_seen = [], []
        for field in ("observe", "why"):
            for sentence in re.split(r"(?<=[.;!?])\s+", str(e.get(field, ""))):
                plain = re.sub(r"\$([^$]*)\$", lambda m: " " + re.sub(r"[_^](\{[^{}]*\}|\\?\w)", " ", m.group(1)) + " ", sentence)
                plain = re.sub(r"\\[A-Za-z]+", " ", plain)
                claims = _claimed_numbers(plain)
                if not claims:
                    continue
                state = _sentence_state(sentence, preset, spec, aliases)
                try:
                    pool = json.loads(ctx.call("numbersOver", [{}, preset, state], **JS_LIMITS))  # defaults too: "from X to Y"
                except Exception:
                    continue
                pool += constants + [x for _, x in _numbers(json.dumps(state))]
                for tok, x in claims:
                    checked += 1
                    if not _supported(tok, x, pool):
                        bad.append(tok)
                        if state != preset and state not in states_seen:
                            states_seen.append(state)
        if bad:
            try:
                actual = json.loads(ctx.call("resultOf", preset, **JS_LIMITS))
            except Exception:
                actual = {}
            shown = {k: _round(v) for k, v in actual.items() if isinstance(v, (int, float, list)) and not isinstance(v, bool)
                     and len(json.dumps(v)) < 300}
            other = ""
            for st in states_seen[:2]:
                try:
                    res = json.loads(ctx.call("resultOf", st, **JS_LIMITS))
                    diff = {k: v for k, v in st.items() if preset.get(k) != v}
                    other += (f" With {json.dumps(diff)} it returns "
                              + json.dumps({k: _round(v) for k, v in res.items() if isinstance(v, (int, float, list))
                                            and not isinstance(v, bool) and len(json.dumps(v)) < 300})[:600] + ".")
                except Exception:
                    pass
            rep.errors.append(f"exploration {i + 1} ('{e.get('title')}') states {bad}, which compute() does not return "
                              f"in the state each sentence describes. At the preset compute() returns "
                              f"{json.dumps(shown)[:800]}.{other} Restate the numbers from these results. If the preset "
                              "does not show the scenario the exploration describes (e.g. 'equal scores'), change the "
                              "preset so it does. Name any other setting in the same sentence as '<control> = <value>' or "
                              "'with <toggle> off'")
    rep.stats["exploration_numbers_checked"] = checked


_DIM_WORDS = re.compile(r"dimension|dim\b|size|length|number of|count|\bd_|^d[a-z]?$|^n[a-z]?$", re.I)
_UNIT_WORDS = re.compile(r"\bbase\b|unit|bits|nats|dits|degrees|radians|decibel|\bdb\b|log", re.I)


def check_control_consistency(spec: dict, ctx: Any, rep: Report) -> None:
    """Scientific consistency: a control must not contradict the inputs it describes.
    1. A number control for a dimension that is fixed by a vector/matrix shape (e.g. a d_k slider while Q has
       3 columns) must instead be derived in compute or be the size control of that vector/matrix.
    2. No control may switch units (log base, degrees/radians, dB): fixed labels would become wrong."""
    params = spec["params"]
    size_refs = {q.get(k) for q in params for k in ("length", "rows", "cols") if isinstance(q.get(k), str)}
    shapes = set()
    for q in params:
        d = q.get("default")
        if q.get("type") == "vector" and isinstance(d, list):
            shapes.add(len(d))
        if q.get("type") == "matrix" and isinstance(d, list) and d and isinstance(d[0], list):
            shapes.update({len(d), len(d[0])})
    for p in params:
        text = f"{p['id']} {p.get('label', '')}"
        if (p.get("type") == "number" and p["id"] not in size_refs and _DIM_WORDS.search(text)
                and isinstance(p.get("default"), (int, float)) and p["default"] in shapes):
            rep.errors.append(f"control '{p['id']}' sets a dimension ({p['default']}) that is already fixed by the shape of a "
                              "vector/matrix input; derive it inside compute from that shape (e.g. dk = Q[0].length), or "
                              "make it that input's rows/cols/length control, so the formula and the data always agree")
    for p in params:
        if p.get("type") not in ("select", "toggle"):
            continue
        text = f"{p['id']} {p.get('label', '')} " + " ".join(str(o.get("label", "")) for o in p.get("options", []) or [])
        if not _UNIT_WORDS.search(text):
            continue
        values = [o.get("value") for o in p.get("options", [])] if p["type"] == "select" else [True, False]
        try:
            outs = {json.dumps(json.loads(ctx.call("resultOf", {p["id"]: v}, **JS_LIMITS))) for v in values}
        except Exception:
            continue
        if len(outs) > 1:
            rep.errors.append(f"control '{p['id']}' switches the units of the results (e.g. bits/nats); chart labels, "
                              "titles and text would then be wrong. Remove it and report results in the units the "
                              "brief and paper use")


def check_presets_differ(spec: dict, ctx: Any, rep: Report) -> None:
    try:
        results = [ctx.call("resultOf", e.get("preset") or {}, **JS_LIMITS) for e in spec["explorations"]]
    except Exception:
        return
    if len(results) == 2 and results[0] == results[1]:
        rep.errors.append("both explorations' presets produce identical results; each preset must set up the scenario "
                          "its exploration describes (e.g. the second one a different value of the control it varies)")


def check_tests_meaningful(spec: dict, rep: Report) -> None:
    for c in spec.get("checks", []):
        test = re.sub(r"\s+", "", str(c.get("test", "")))
        if not re.search(r"\br(?:\.|\[)", test) or test.lower() in ("true", "1", "!0", "!!1"):
            rep.errors.append(f"check '{c.get('name')}' does not test any computed result (r.<key>); "
                              "every check must compare compute() outputs against an expected relation or value")


def check_execution(spec: dict, rep: Report) -> None:
    if MiniRacer is None:
        rep.warnings.append("JS engine unavailable; executable checks skipped")
        return
    ctx = MiniRacer()
    client = {"params": spec["params"]}
    try:
        ctx.eval(HARNESS % json.dumps(client) + "\n" + code_bundle(spec).replace("window.__explainer", "var __explainer"),
                 **JS_LIMITS)
    except Exception as exc:
        rep.errors.append(f"JavaScript does not load (syntax error?): {str(exc)[:300]}")
        return
    rng = random.Random(1234)
    named: list[tuple[str, dict]] = [("defaults", {})]
    named += [(f"preset {i + 1}", e.get("preset", {})) for i, e in enumerate(spec["explorations"])]
    named += [(f"{m} values", _random_state(spec, rng, m)) for m in ("min", "max", "zero")]
    named += [("one-hot vector", s) for s in _one_hot_states(spec)]
    def zeros(v: Any) -> Any:
        return [zeros(x) for x in v] if isinstance(v, list) else 0
    zero_arrays = {q["id"]: zeros(q.get("default")) for q in spec["params"]
                   if q.get("type") in ("vector", "matrix") and isinstance(q.get("default"), list)
                   and (q.get("min") is None or q.get("min") <= 0 <= q.get("max", 0))}
    if zero_arrays:
        named.append(("all-zero vectors/matrices", zero_arrays))
    for p in spec["params"]:
        if p.get("type") == "number":
            named += [(f"{p['id']}={p['min']}", {p["id"]: p["min"]}), (f"{p['id']}={p['max']}", {p["id"]: p["max"]})]
        if p.get("type") == "toggle":
            named.append((f"{p['id']} flipped", {p["id"]: not p.get("default")}))
        if p.get("type") == "select":
            named += [(f"{p['id']}={o.get('value')}", {p["id"]: o.get("value")}) for o in p.get("options", [])]
    named += [(f"random #{i + 1}", _random_state(spec, rng, "rand")) for i in range(30)]
    fixed = [c for c in spec.get("checks", []) if c.get("params")]
    named += [(f"check '{c['name']}'", c["params"]) for c in fixed]

    try:
        t0 = time.perf_counter()
        results = json.loads(ctx.call("evaluate", [s for _, s in named], **JS_LIMITS))
        per_call_ms = (time.perf_counter() - t0) * 1000 / max(1, len(named))
    except Exception as exc:
        rep.errors.append(f"compute() timed out or crashed the engine: {str(exc)[:200]}")
        return
    invariant_ids = {c["id"]: c for c in spec.get("checks", []) if not c.get("params")}
    fixed_ids = {c["id"]: c for c in fixed}
    failures: dict[str, list[str]] = {}
    passed = total = 0
    for (label, state), res in zip(named, results):
        if not res.get("ok"):
            rep.errors.append(f"compute() throws for {label} inputs {json.dumps(state)[:200]}: {res.get('error')}")
            continue
        if res["nonfinite"]:
            rep.errors.append(f"compute() returns non-finite values for {label} inputs {json.dumps(state)[:200]}: {res['nonfinite'][:3]}")
        is_fixed_case = label.startswith("check '")
        for cid, outcome in res["checks"].items():
            if cid in invariant_ids or (is_fixed_case and fixed_ids.get(cid, {}).get("name") == label[7:-1]):
                total += 1
                if outcome is True:
                    passed += 1
                else:
                    failures.setdefault(cid, []).append(f"{label} {json.dumps(state)[:160]} -> {outcome}")
    for cid, fails in failures.items():
        c = invariant_ids.get(cid) or fixed_ids.get(cid)
        rep.errors.append(f"check '{c['name']}' ({c['test'][:120]}) fails: {fails[0]}" + (f" (+{len(fails) - 1} more)" if len(fails) > 1 else ""))
    # Sweeps: the runtime re-runs compute across the swept range; those states must be valid too.
    sweep_states = []
    for v in spec["views"]:
        if v.get("type") != "sweep":
            continue
        p = next((q for q in spec["params"] if q.get("id") == v.get("x_param")
                  and q.get("type") in ("number", "vector", "matrix")), None)
        if p is None:
            continue
        lo = v.get("x_min", p.get("min", 0)); hi = v.get("x_max", p.get("max", 1))
        step = p.get("step") or 0
        for k in range(11):
            x = lo + (hi - lo) * k / 10
            if step:  # the page only ever sweeps values on the control's step grid
                x = min(hi, max(lo, round(round((x - lo) / step) * step + lo, 10)))
            if p["type"] == "vector":
                vec = list(p["default"]); vec[min(len(vec) - 1, int(v.get("x_index") or 0))] = x
                sweep_states.append({p["id"]: vec})
            elif p["type"] == "matrix":
                mat = json.loads(json.dumps(p["default"]))
                i, j = (list(v.get("x_index")) + [0, 0])[:2] if isinstance(v.get("x_index"), list) else (0, 0)
                mat[min(len(mat) - 1, int(i))][min(len(mat[0]) - 1, int(j))] = x
                sweep_states.append({p["id"]: mat})
            else:
                sweep_states.append({p["id"]: x})
    if sweep_states:
        for st, res in zip(sweep_states, json.loads(ctx.call("evaluate", sweep_states, **JS_LIMITS))):
            if not res.get("ok") or res.get("nonfinite"):
                rep.errors.append(f"sweep inputs {json.dumps(st)[:160]} break compute(): {res.get('error') or res.get('nonfinite')}")
                break
    rep.stats["compute_ms"] = round(per_call_ms, 3)
    if per_call_ms > MAX_COMPUTE_MS:
        rep.errors.append(f"compute() is too slow ({per_call_ms:.1f} ms per call); the page re-runs it for every "
                          "control change and sweep point, so keep it under a few milliseconds (small loops only)")
    rep.stats.update(states_tested=len(named), check_evaluations=total, check_passes=passed)

    # Every view must find its data on defaults and presets.
    base = results[0]
    if base.get("ok"):
        keys = set(base["keys"]) | {p["id"] for p in spec["params"]}
        for v in spec["views"]:
            refs = [v.get(k) for k in ("value", "x", "labels", "row_labels", "col_labels", "marker_x") if isinstance(v.get(k), str)]
            refs += [s.get("key") for k in ("series", "steps", "items", "columns", "footer", "y") for s in (v.get(k) or [])
                     if isinstance(s, dict)]
            if isinstance(v.get("values"), str):
                refs.append(v["values"])
            refs += [k for k in (v.get("marker_y") or []) if isinstance(k, str)]
            missing = [r for r in refs if r and r not in keys]
            if missing:
                rep.errors.append(f"view '{v.get('title')}' ({v.get('type')}) reads keys not returned by compute: {missing}")
        check_view_shapes(spec, json.loads(ctx.call("resultOf", {}, **JS_LIMITS)), rep)
        check_exploration_numbers(spec, ctx, rep)
        check_presets_differ(spec, ctx, rep)
        check_control_consistency(spec, ctx, rep)
        # Controls must matter: changing each one should change the result.
        effective = 0
        for p in spec["params"]:
            alt = None
            if p.get("type") == "number":
                alt = p["max"] if p.get("default") != p["max"] else p["min"]
            elif p.get("type") == "toggle":
                alt = not p.get("default")
            elif p.get("type") == "select" and len(p.get("options", [])) > 1:
                alt = next(o.get("value") for o in p["options"] if o.get("value") != p.get("default"))
            elif p.get("type") == "vector":
                d = list(p.get("default") or [0])
                d[0] = p.get("max", 1) if d[0] != p.get("max", 1) else p.get("min", 0)
                alt = d
            elif p.get("type") == "matrix":
                d = json.loads(json.dumps(p.get("default") or [[0]]))
                d[0][0] = p.get("max", 1) if d[0][0] != p.get("max", 1) else p.get("min", 0)
                alt = d
            if alt is None:
                continue
            try:
                if ctx.call("resultOf", {}, **JS_LIMITS) != ctx.call("resultOf", {p["id"]: alt}, **JS_LIMITS):
                    effective += 1
                else:
                    rep.warnings.append(f"control '{p['id']}' does not change any result")
            except Exception:
                pass
        rep.stats["effective_controls"] = effective
        if effective < 2:
            rep.errors.append("fewer than two controls change the computed results")
        try:
            draws = json.loads(ctx.call("drawAll", {}, **JS_LIMITS))
            for vid, out in draws.items():
                if UNSAFE_SVG.search(out):
                    rep.errors.append(f"svg view {vid} draw() output contains scripts, links or external references")
                elif not re.match(r"\s*<svg[\s>]", out, re.I):
                    rep.errors.append(f"svg view {vid} draw() must return an <svg> string, got: {out[:120]}")
        except Exception as exc:
            rep.errors.append(f"svg draw failed: {str(exc)[:150]}")
        if base.get("warning"):
            rep.warnings.append(f"default inputs produce a warning: {base['warning']}")


def validate(spec: dict, excerpt: str | None, brief: str | None = None) -> Report:
    rep = Report()
    normalize_spec(spec, rep)
    check_structure(spec, rep)
    if any(e.startswith(("missing or invalid field", "param ")) for e in rep.errors):
        return rep  # cannot build inputs for compute(); nothing executable to test yet
    check_grounding(spec, excerpt, rep)
    check_tests_meaningful(spec, rep)
    check_citations(spec, excerpt, rep)
    if excerpt is None and brief:
        check_brief_citations(spec, brief, rep)
    check_execution(spec, rep)
    return rep
