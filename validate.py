"""Deterministic checks on a generated spec: structure, executable math, edge cases, grounding.

compute(p), checks and SVG drawings are executed in an embedded V8 (mini-racer)
over defaults, presets, boundary values and seeded random inputs.
"""

from __future__ import annotations

import json
import random
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
FORBIDDEN = re.compile(r"\b(fetch|XMLHttpRequest|importScripts|require|eval|Function|document|window|"
                       r"localStorage|Math\.random|setTimeout|setInterval)\b|\bimport\s*\(")
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
function resultOf(s) { var st = normalizeShapes(Object.assign(defaults(), clone(s))); return JSON.stringify(__explainer.compute(clone(st))); }
function drawAll(s) {
  var st = normalizeShapes(Object.assign(defaults(), clone(s))); var r = __explainer.compute(clone(st)); var out = {};
  for (var id in __explainer.draw) { try { out[id] = String(__explainer.draw[id](clone(st), r)).slice(0, 300); } catch (e) { out[id] = "error: " + e.message; } }
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
            if not target or target.get("type") not in ("number", "vector"):
                rep.errors.append(f"view '{v.get('title')}' (sweep) has x_param {v.get('x_param')!r}, which is not a "
                                  "number or vector param")
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
    m = FORBIDDEN.search(code)
    if m:
        rep.errors.append(f"generated code uses forbidden API {m.group(0)!r}")
    for e in spec.get("equations", []) + [{"latex": s.get("symbol", "")} for s in spec["symbols"]]:
        if e.get("latex") and not latex_ok(e["latex"]):
            rep.warnings.append(f"LaTeX not convertible: {e['latex'][:60]}")


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
            "cells": isinstance(val, list) and all(not isinstance(x, (list, dict)) for x in val) or _is_num(val),
        }[kind]
        if not ok:
            want = {"number": "a single number", "numbers": "a flat array of numbers",
                    "matrix": "an array of rows", "cells": "a flat array of numbers/strings"}[kind]
            rep.errors.append(f"view '{v.get('title')}' ({v.get('type')}): key {key!r} must be {want}, "
                              f"but compute returns {json.dumps(val)[:80]}")

    for v in spec["views"]:
        t = v.get("type")
        if t == "bars":
            for srs in v.get("series") or ([{"key": v["values"]}] if v.get("values") else []):
                need(v, srs.get("key"), "numbers")
        elif t == "heatmap":
            need(v, v.get("value"), "matrix")
        elif t == "sweep":
            for y in v.get("y", []):
                need(v, y.get("key"), "number")
        elif t == "curve":
            need(v, v.get("x"), "numbers")
            for y in v.get("y", []):
                need(v, y.get("key"), "numbers")
        elif t == "table":
            for c in v.get("columns", []):
                need(v, c.get("key"), "cells")


def check_execution(spec: dict, rep: Report) -> None:
    if MiniRacer is None:
        rep.warnings.append("JS engine unavailable; executable checks skipped")
        return
    ctx = MiniRacer()
    client = {"params": spec["params"]}
    try:
        ctx.eval(HARNESS % json.dumps(client) + "\n" + code_bundle(spec).replace("window.__explainer", "var __explainer"))
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
        results = json.loads(ctx.call("evaluate", [s for _, s in named], timeout=8000))
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
                  and q.get("type") in ("number", "vector")), None)
        if p is None:
            continue
        lo = v.get("x_min", p.get("min", 0)); hi = v.get("x_max", p.get("max", 1))
        for k in range(11):
            x = lo + (hi - lo) * k / 10
            if p["type"] == "vector":
                vec = list(p["default"]); vec[min(len(vec) - 1, int(v.get("x_index") or 0))] = x
                sweep_states.append({p["id"]: vec})
            else:
                sweep_states.append({p["id"]: x})
    if sweep_states:
        for st, res in zip(sweep_states, json.loads(ctx.call("evaluate", sweep_states, timeout=8000))):
            if not res.get("ok") or res.get("nonfinite"):
                rep.errors.append(f"sweep inputs {json.dumps(st)[:160]} break compute(): {res.get('error') or res.get('nonfinite')}")
                break
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
        check_view_shapes(spec, json.loads(ctx.call("resultOf", {})), rep)
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
                if ctx.call("resultOf", {}) != ctx.call("resultOf", {p["id"]: alt}):
                    effective += 1
                else:
                    rep.warnings.append(f"control '{p['id']}' does not change any result")
            except Exception:
                pass
        rep.stats["effective_controls"] = effective
        if effective < 2:
            rep.errors.append("fewer than two controls change the computed results")
        try:
            draws = json.loads(ctx.call("drawAll", {}))
            for vid, out in draws.items():
                if not re.match(r"\s*<svg[\s>]", out, re.I):
                    rep.errors.append(f"svg view {vid} draw() must return an <svg> string, got: {out[:120]}")
        except Exception as exc:
            rep.errors.append(f"svg draw failed: {str(exc)[:150]}")
        if base.get("warning"):
            rep.warnings.append(f"default inputs produce a warning: {base['warning']}")


def validate(spec: dict, excerpt: str | None) -> Report:
    rep = Report()
    normalize_spec(spec, rep)
    check_structure(spec, rep)
    if any(e.startswith(("missing or invalid field", "param ")) for e in rep.errors):
        return rep  # cannot build inputs for compute(); nothing executable to test yet
    check_grounding(spec, excerpt, rep)
    check_execution(spec, rep)
    return rep
