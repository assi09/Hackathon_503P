import json
import tempfile
import unittest
from pathlib import Path

from agent import CaseError, load_case, main
from page import Exploration, PageSpec, Symbol, render_page


class FoundationTests(unittest.TestCase):
    def test_valid_case_allows_extra_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            path.write_text(json.dumps({
                "source_url": "https://example.org/paper",
                "focus": "  Explain a slope  ",
                "audience": "engineering undergraduate",
                "excerpt": "Additional fields remain available for the source reader.",
            }), encoding="utf-8")
            case = load_case(path)
            self.assertEqual(case.focus, "Explain a slope")

    def test_missing_field_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            path.write_text('{"focus":"example"}', encoding="utf-8")
            with self.assertRaisesRegex(CaseError, "source_url"):
                load_case(path)

    def test_cli_writes_failure_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "case.json"
            output = root / "out"
            case.write_text(json.dumps({
                "source_url": "https://example.org/paper", "focus": "slope", "audience": "student"
            }), encoding="utf-8")
            self.assertEqual(main(["--input", str(case), "--output", str(output), "--model", "test/model"]), 1)
            events = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["result"], "ok")
            self.assertEqual(events[-1]["result"], "failed")
            self.assertFalse((output / "index.html").exists())

    def test_page_is_self_contained_and_escapes_source_text(self):
        spec = PageSpec(
            title="A < B", idea="A simple relationship", why_it_matters="Predict an output",
            symbols=(Symbol("x", "input"),),
            visual_html="<output id='value'>0</output>",
            controls_html="<label>Input <input id='x' type='range' min='0' max='10'></label>",
            calculation_js="document.querySelector('#x').oninput = e => document.querySelector('#value').value = e.target.value;",
            explorations=(Exploration("increase x", "the output", "they are linked"),
                          Exploration("decrease x", "the output", "they are linked")),
            limitation="A toy model", source_url="https://example.org/paper",
            source_location="Section 1", source_support="A relationship", simplification="Small numbers",
        )
        page = render_page(spec)
        self.assertIn("A &lt; B", page)
        self.assertIn("<script>", page)
        self.assertNotIn("https://cdn", page)
        self.assertIn("id='x'", page)


if __name__ == "__main__":
    unittest.main()
