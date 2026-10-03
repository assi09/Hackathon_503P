"""Execute the identical lesson calculation used by the browser in bounded QuickJS."""
import copy
import json
import math
import random
import re

import quickjs


def normalize_spec(spec, source_locator=None):
    """Canonicalize harmless widget synonyms without spending an API call."""
    spec = copy.deepcopy(spec)
    changes = []
    for control in spec.get("controls", []):
        if isinstance(control, dict) and control.get("type") in {"range", "slider"}:
            old = control["type"]
            control["type"] = "number"
            changes.append({"control": control.get("id"), "from": old, "to": "number"})
    if source_locator and isinstance(spec.get("source"), dict) and spec["source"].get("locator") != source_locator:
        spec["source"]["locator"] = source_locator
        changes.append({"field": "source.locator", "from": "model-proposed locator", "to": source_locator})
    return spec, changes


def normalize_invariants(invariants):
    return [{"name": f"Scientific invariant {i+1}", "expression": item}
            if isinstance(item, str) else item for i, item in enumerate(invariants)]


def defaults(spec):
    return {c["id"]: copy.deepcopy(c["value"]) for c in spec["controls"]}


def check_schema(spec):
    errors = []
    for key in ("title", "subtitle", "intro", "equation", "compute"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            errors.append(f"Missing nonempty string: {key}")
    for key in ("plan", "symbols", "limitations", "tests"):
        if not isinstance(spec.get(key), list) or not spec[key]:
            errors.append(f"Missing nonempty list: {key}")
    source = spec.get("source", {})
    for key in ("title", "locator", "supported", "simplifications"):
        if not isinstance(source, dict) or not source.get(key):
            errors.append(f"Missing source.{key}")
    controls = spec.get("controls", [])
    if not isinstance(controls, list) or not controls:
        errors.append("Need at least two independent controls")
        return errors
    independent_inputs = len(controls) + sum(1 for c in controls if isinstance(c, dict)
        and c.get("type") == "vector" and c.get("maxItems", 0) > c.get("minItems", 0))
    if independent_inputs < 2:
        errors.append("Need two independent inputs; a resizable vector supplies entries and length")
    ids = set()
    for c in controls:
        if not isinstance(c, dict):
            errors.append("Control must be an object")
            continue
        cid = c.get("id", "")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", cid) or cid in ids:
            errors.append(f"Invalid/duplicate control ID: {cid}")
        ids.add(cid)
        if not c.get("label") or not c.get("help"):
            errors.append(f"Control {cid} needs label and help")
        kind, val = c.get("type"), c.get("value")
        if kind not in {"number", "toggle", "select", "vector", "matrix"}:
            errors.append(f"Unsupported control type {kind}")
        elif kind == "toggle" and not isinstance(val, bool):
            errors.append(f"{cid}: toggle must be boolean")
        elif kind == "select":
            opts = c.get("options", [])
            if len(opts) < 2 or val not in [o.get("value") for o in opts]:
                errors.append(f"{cid}: invalid select options/default")
        elif kind in {"number", "vector", "matrix"}:
            try:
                low, high = c["min"], c["max"]
                assert math.isfinite(low) and math.isfinite(high) and low < high
                if kind == "matrix":
                    assert 1 <= len(val) <= 8 and 1 <= len(val[0]) <= 8
                    assert all(len(row) == len(val[0]) for row in val)
                    nums = [x for row in val for x in row]
                elif kind == "vector":
                    assert 1 <= len(val) <= 16
                    assert 1 <= c.get("minItems", len(val)) <= len(val)
                    assert len(val) <= c.get("maxItems", len(val)) <= 16
                    nums = val
                else:
                    assert c.get("step", 0) > 0
                    nums = [val]
                assert all(not isinstance(x, bool) and math.isfinite(x) and low <= x <= high for x in nums)
            except (KeyError, TypeError, ValueError, AssertionError, IndexError):
                errors.append(f"{cid}: invalid numeric shape, bounds, step, or default")
    explorations = spec.get("explorations", [])
    if not isinstance(explorations, list) or len(explorations) != 2:
        errors.append("Need exactly two guided explorations")
    else:
        for e in explorations:
            for k in ("title", "prediction", "action", "state", "observe", "why"):
                if not isinstance(e, dict) or not e.get(k):
                    errors.append(f"Exploration missing {k}")
            if isinstance(e, dict) and isinstance(e.get("state"), dict):
                if set(e["state"]) - ids:
                    errors.append("Exploration refers to nonexistent control")
    code = spec.get("compute", "")
    if not re.match(r"\s*function\s+compute\s*\(", code):
        errors.append("compute must declare function compute(s)")
    if re.search(r"\b(fetch|XMLHttpRequest|WebSocket|import|require|eval|Function|document|window|globalThis|Date)\b|Math\.random", code):
        errors.append("Computation must use pure arithmetic, no external capabilities")
    if "</script" in code.lower():
        errors.append("Script closing tag forbidden in computation")
    return errors


class Engine:
    def __init__(self, spec, invariants=None):
        self.ctx = quickjs.Context()
        self.ctx.set_memory_limit(32 * 1024 * 1024)
        self.ctx.set_time_limit(0.6)
        self.ctx.set_max_stack_size(512 * 1024)
        self.ctx.eval(spec["compute"])
        self.base = defaults(spec)
        self.invariants = normalize_invariants(invariants or [])

    def run(self, overrides=None):
        state = {**self.base, **(overrides or {})}
        # Detect nonfinite values BEFORE JSON.stringify converts them to null.
        script = r'''JSON.stringify((function(){
          const s=STATE; const before=JSON.stringify(s); const o=compute(s);
          if(JSON.stringify(s)!==before) throw Error('compute mutated input');
          function finite(x){if(typeof x==='number'&&!Number.isFinite(x)) throw Error('Nonfinite output');
            if(x&&typeof x==='object')Object.values(x).forEach(finite);}
          finite(o); INVARIANTS return o; })())'''.replace("STATE", json.dumps(state, allow_nan=False), 1)
        assertions = []
        for inv in self.invariants:
            expression = inv["expression"]
            if re.search(r"\b(fetch|import|require|eval|Function|document|window|globalThis)\b", expression):
                raise ValueError("Invariant uses forbidden capabilities")
            assertions.append("if ((" + expression + ") !== true) throw Error(" +
                              json.dumps("Invariant failed: " + inv["name"] + "; expression: " + expression + "; observed: ") +
                              "+JSON.stringify({state:s,metrics:o.metrics,panels:o.panels}));")
        script = script.replace("INVARIANTS", "\n".join(assertions))
        return json.loads(self.ctx.eval(script))


def output_errors(out):
    errors = []
    if not isinstance(out, dict):
        return ["compute output must be an object"]
    if not out.get("metrics"):
        errors.append("No computed metrics")
    for m in out.get("metrics", []):
        undefined = m.get("value", False) is None and m.get("defined") is False and bool(m.get("reason"))
        if (not isinstance(m.get("value"), (int, float)) and not undefined) or not m.get("label"):
            errors.append("Metrics need a numeric value and label, or value:null, defined:false, reason for an undefined quantity")
    if len(out.get("steps", [])) < 2:
        errors.append("Need at least two visible intermediate steps")
    if not out.get("panels"):
        errors.append("No visual panels")
    for p in out.get("panels", []):
        try:
            assert p.get("title") and p.get("description")
            kind = p["kind"]
            if isinstance(p.get("unavailable"), str) and p["unavailable"].strip():
                continue
            if kind == "bar":
                assert len(p["labels"]) == len(p["values"]) > 0
                assert all(isinstance(x, (int, float)) for x in p["values"])
            elif kind == "matrix":
                assert len(p["rows"]) == len(p["values"]) > 0
                assert len(p["columns"]) > 0
                assert all(len(r) == len(p["columns"]) for r in p["values"])
                assert all(isinstance(x, (int, float)) for r in p["values"] for x in r)
            elif kind == "table":
                assert p["columns"] and p["rows"]
                assert all(len(r) == len(p["columns"]) for r in p["rows"])
            elif kind == "line":
                assert p["xLabel"] and p["yLabel"] and p["series"]
                for series in p["series"]:
                    assert series["name"] and series["points"]
                    assert all(len(pt) == 2 and all(isinstance(x, (int, float)) for x in pt) for pt in series["points"])
            elif kind == "flow":
                ids = {n["id"] for n in p["nodes"]}
                assert ids
                assert all(0 <= n["x"] <= 1 and 0 <= n["y"] <= 1 for n in p["nodes"])
                assert all(e["from"] in ids and e["to"] in ids and e["weight"] >= 0 for e in p["edges"])
            else:
                errors.append(f"Unknown visual kind: {kind}")
        except (KeyError, TypeError, AssertionError):
            errors.append(f"Malformed visual: {p.get('title', '?')}")
    return errors


def numeric_signature(out):
    # Text changes do not count as numerical interactivity.
    def collect(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            return [x]
        if isinstance(x, dict):
            return sum((collect(v) for v in x.values()), [])
        if isinstance(x, list):
            return sum((collect(v) for v in x), [])
        return []
    return collect({"metrics": out.get("metrics"), "panels": out.get("panels")})


def variants(c):
    kind, val = c["type"], c["value"]
    if kind == "toggle":
        return [not val]
    if kind == "select":
        return [o["value"] for o in c["options"]]
    lo, hi = c["min"], c["max"]
    mid = (lo + hi) / 2
    if kind == "number":
        step = c["step"]
        mid = min(hi, lo + round((mid - lo) / step) * step)
        return [lo, mid, hi]
    if kind == "vector":
        result = [[lo] * len(val), [hi] * len(val), [lo if i % 2 else hi for i in range(len(val))]]
        result.extend([[val[0]] * n for n in (c.get("minItems", len(val)), c.get("maxItems", len(val)))])
        return result
    return [[[v] * len(val[0]) for _ in val] for v in (lo, mid, hi)] + [
        [[lo if (i + j) % 2 else hi for j in range(len(val[0]))] for i in range(len(val))]]


def validate(spec, reference_tests=None, invariants=None):
    results = []
    def record(name, passed, **extra):
        results.append({"name": name, "passed": passed, **extra})
    try:
        schema = check_schema(spec)
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        schema = ["Malformed lesson structure: " + str(exc)]
    record("lesson_schema", not schema, errors=schema)
    if schema:
        return results
    try:
        engine = Engine(spec, invariants)
        default = engine.run()
        errs = output_errors(default)
        record("default_execution_and_visual_schema", not errs, errors=errs)
        record("deterministic_computation", engine.run() == default)
    except Exception as exc:
        record("default_execution", False, error=str(exc)[:1200])
        return results
    for test in list(spec.get("tests", [])) + list(reference_tests or []):
        try:
            out = engine.run(test.get("state", {}))
            comparisons = []
            for expected in test["expect"]:
                actual = out
                for key in expected["path"].split("."):
                    actual = actual[int(key)] if isinstance(actual, list) else actual[key]
                target = expected.get("value")
                if "formula" in expected:
                    expression = expected["formula"]
                    if re.search(r"\b(compute|o|fetch|import|require|eval|Function|document|window|globalThis)\b", expression):
                        raise ValueError("Reference formula must be independent of compute/output")
                    ref = quickjs.Context()
                    ref.set_memory_limit(8 * 1024 * 1024)
                    ref.set_time_limit(0.2)
                    state = {**defaults(spec), **test.get("state", {})}
                    target = ref.eval("(function(s){return (" + expression + ");})(" + json.dumps(state) + ")")
                    if target is not None and (not isinstance(target, (int, float)) or not math.isfinite(target)):
                        raise ValueError("Reference formula must return a finite number or null")
                tol = min(abs(float(expected.get("atol", 1e-8))), 1e-4)
                ok = actual is None if target is None else isinstance(actual, (int, float)) and abs(actual-target) <= tol
                comparisons.append({"path": expected["path"], "expected": target, "actual": actual, "atol": tol, "passed": ok, **({"formula": expected["formula"]} if "formula" in expected else {})})
            record(test["name"], bool(comparisons) and all(x["passed"] for x in comparisons), comparisons=comparisons)
        except Exception as exc:
            record(test.get("name", "reference_test"), False, error=str(exc)[:600])
    for e in spec["explorations"]:
        try:
            out = engine.run(e["state"])
            errs = output_errors(out)
            record("exploration:" + e["title"], not errs, errors=errs)
        except Exception as exc:
            record("exploration:" + e["title"], False, error=str(exc)[:600])
    base_signature = numeric_signature(default)
    changed = 0
    for control in spec["controls"]:
        affected, failures = False, []
        for value in variants(control):
            try:
                out = engine.run({control["id"]: value})
                affected |= numeric_signature(out) != base_signature
                failures.extend(output_errors(out))
            except Exception as exc:
                failures.append(str(exc)[:500])
        # Some valid controls act only in another context (normalization of an
        # already-normalized input, or an option enabled by another control).
        if not affected and not failures:
            contexts = []
            for other in spec["controls"]:
                if other["id"] != control["id"]:
                    contexts.extend({other["id"]: v} for v in variants(other)[:3])
            for context in contexts[:12]:
                try:
                    signature = numeric_signature(engine.run(context))
                    for value in variants(control)[:4]:
                        out = engine.run({**context, control["id"]: value})
                        failures.extend(output_errors(out))
                        affected |= numeric_signature(out) != signature
                    if affected:
                        break
                except Exception as exc:
                    failures.append(str(exc)[:500])
        changed += affected
        record("control:" + control["id"], affected and not failures, affects_numbers=affected, errors=failures)
        if control["type"] == "vector" and control.get("maxItems", 0) > control.get("minItems", 0):
            length_changes = False
            for n in (control["minItems"], control["maxItems"]):
                try:
                    v = [control["value"][0]] * n
                    length_changes |= numeric_signature(engine.run({control["id"]: v})) != base_signature
                except Exception:
                    pass  # Reported by the regular boundary sweep above.
            changed += length_changes
            record("vector_length:" + control["id"], length_changes)
    record("at_least_two_meaningful_controls", changed >= 2, count=changed)
    # Cross-input combinations catch failures invisible to one-at-a-time sweeps.
    rng = random.Random(435)
    for i in range(12):
        state = {c["id"]: rng.choice(variants(c)) for c in spec["controls"]}
        try:
            errs = output_errors(engine.run(state))
            record(f"combined_boundaries:{i}", not errs, errors=errs)
        except Exception as exc:
            record(f"combined_boundaries:{i}", False, error=str(exc)[:500])
    return results
