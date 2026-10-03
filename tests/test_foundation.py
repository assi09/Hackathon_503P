import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent import CaseError, check_html, load_case, main
from page import render_page
from validate import validate

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "toy_spec.json").read_text())
CASE = {"source_url": "https://example.org/paper", "focus": "toy", "audience": "engineering undergraduates"}


class CaseTests(unittest.TestCase):
    def write(self, directory, data):
        path = Path(directory) / "case.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_valid_case_is_trimmed(self):
        with tempfile.TemporaryDirectory() as d:
            case = load_case(self.write(d, {**CASE, "focus": "  Explain a slope  "}))
            self.assertEqual(case["focus"], "Explain a slope")

    def test_missing_field_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(CaseError, "source_url"):
                load_case(self.write(d, {"focus": "x", "audience": "y"}))

    def test_cli_failure_writes_trace_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, {"focus": "x"})
            out = Path(d) / "out"
            self.assertEqual(main(["--input", str(path), "--output", str(out), "--model", "m"]), 1)
            events = [json.loads(l) for l in (out / "trace.jsonl").read_text().splitlines()]
            self.assertEqual(events[-1]["result"], "failed")


class SpecTests(unittest.TestCase):
    def test_fixture_passes_validation(self):
        rep = validate(copy.deepcopy(FIXTURE), None)
        self.assertTrue(rep.ok, rep.errors)
        self.assertGreaterEqual(rep.stats["effective_controls"], 2)

    def test_division_by_zero_is_caught(self):
        bad = copy.deepcopy(FIXTURE)
        bad["compute"] = bad["compute"].replace(
            "tot > 0 ? a.map(function(v){return v/tot;}) : a.map(function(){return 1/n;})",
            "a.map(function(v){return v/tot;})")
        self.assertTrue(any("non-finite" in e for e in validate(bad, None).errors))

    def test_wrong_expected_value_is_caught(self):
        bad = copy.deepcopy(FIXTURE)
        bad["checks"][1]["test"] = "Math.abs(r.mean - 2.6) < 1e-9"
        self.assertTrue(any("fails" in e for e in validate(bad, None).errors))

    def test_compute_may_use_helper_functions_and_other_forms(self):
        core = FIXTURE["compute"].replace("function compute(p)", "function core(p)", 1)
        for code in (core + "\nfunction helper(x) { return x; }\nfunction compute(p) { return core(p); }",
                     "function (p) { var core = " + core + "; return core(p); }",
                     "(p) => { var core = " + core + "; return core(p); }"):
            spec = copy.deepcopy(FIXTURE)
            spec["compute"] = code
            self.assertTrue(validate(spec, None).ok, code[:40])

    def test_citation_must_exist_in_source(self):
        from validate import Report, check_citations
        excerpt = "Intro.\n$$y = mx + b$$\n(1)\nThe slope m scales x."
        good = {"paper": {"section": "Section 1", "equation": "Eq. (1)"}, "grounding": {"from_paper": []}}
        bad = {"paper": {"section": "Section 1", "equation": "Eqs. (1)-(2), Algorithm 3"}, "grounding": {"from_paper": []}}
        rep = Report(); check_citations(good, excerpt, rep); self.assertEqual(rep.errors, [])
        rep = Report(); check_citations(bad, excerpt, rep)
        self.assertTrue(rep.errors and "Eqs. 2" in rep.errors[0] and "Algorithm 3" in rep.errors[0])

    def test_unverifiable_quote_is_dropped(self):
        spec = copy.deepcopy(FIXTURE)
        spec["grounding"]["quotes"] = ["A weighted mean averages values by their importance.", "Invented sentence that is not there."]
        rep = validate(spec, "Intro. A weighted mean averages values by their importance. More text.")
        self.assertEqual(spec["grounding"]["quotes"], ["A weighted mean averages values by their importance."])
        self.assertTrue(rep.fixes)

    def test_page_is_self_contained(self):
        spec = copy.deepcopy(FIXTURE)
        validate(spec, None)
        html = render_page(spec, CASE, "note")
        self.assertEqual(check_html(html), [])
        self.assertIn("<math", html)
        self.assertNotIn("https://cdn", html)


if __name__ == "__main__":
    unittest.main()
