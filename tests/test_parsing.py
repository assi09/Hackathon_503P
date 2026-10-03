"""Extraction edge cases and accuracy checks, on synthetic inputs (no network)."""

import copy
import json
import unittest
from pathlib import Path

import pdfglyphs
from source import (SourceDocument, _extract, _fix_letter_spacing, _repair_text, _section_numbers,
                    _strip_running_lines, quality, select_excerpt)
from validate import Report, check_brief_citations, validate

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "toy_spec.json").read_text())


class ExtractionTests(unittest.TestCase):
    def test_letter_spacing_is_repaired(self):
        self.assertEqual(_fix_letter_spacing("6. C HOICE AND T h e s e words"), "6. CHOICE AND These words")

    def test_ligatures_hyphenation_cid_and_mojibake(self):
        self.assertEqual(_repair_text("the ﬁrst eﬀect"), "the first effect")
        self.assertEqual(_repair_text("an algo-\nrithm"), "an algorithm")
        self.assertEqual(_repair_text("x (cid:42) y"), "x ⟨?⟩ y")
        self.assertEqual(_repair_text("itâ€™s"), "it’s")

    def test_running_headers_and_page_numbers_are_removed(self):
        pages = [f"Journal of Things, 2020\nBody text of page {i}.\n{i + 1}" for i in range(6)]
        cleaned = _strip_running_lines(pages)
        self.assertNotIn("Journal of Things", "\n".join(cleaned))
        self.assertIn("Body text of page 3.", cleaned[3])
        self.assertNotIn("\n4", cleaned[3])

    def test_html_math_sources_are_kept_as_latex(self):
        body = ("<p>" + "Context sentence for length. " * 15 + "</p>"
                "<p>Inline <script type='math/tex'>a^2+b^2</script> and display "
                "<script type='math/tex; mode=display'>\\sum_i x_i</script>.</p>"
                "<p>MathML <math><semantics><mi>x</mi><annotation encoding='application/x-tex'>\\alpha_1"
                "</annotation></semantics></math> here.</p>")
        text = _extract(body.encode(), "text/html", "t").text
        self.assertIn("$a^2+b^2$", text)
        self.assertIn("$$\\sum_i x_i$$", text)
        self.assertIn("$\\alpha_1$", text)

    def test_arxiv_conversion_errors_lower_quality(self):
        good = ("<p>" + "A clean sentence about attention. " * 40 + "</p>").encode()
        bad = good + ("<span class='ltx_ERROR'>\\undefined</span>" * 6).encode()
        self.assertGreater(quality(_extract(good, "text/html", "t")), quality(_extract(bad, "text/html", "t")))

    def test_quality_penalizes_garbled_text(self):
        clean = SourceDocument("A clean sentence about entropy. " * 50, "pdf", "t")
        garbled = SourceDocument("A □ sentence /#19 about ⟨?⟩ entropy. " * 50, "pdf", "t")
        self.assertGreater(quality(clean), 0.9)
        self.assertLess(quality(garbled), 0.5)

    def test_roman_and_arabic_sections_match(self):
        self.assertEqual(_section_numbers("Section III, Method"), ["III", "3"])
        self.assertIn("IV", _section_numbers("Section 4: results"))
        filler = "Unrelated filler words for spacing only. " * 80
        text = "Title\n" + filler + "\nIII. METHOD\nThe method computes a weighted sum.\n" + filler * 4
        self.assertIn("The method computes a weighted sum", select_excerpt(SourceDocument(text, "pdf", "t"), "Section 3: method"))

    def test_tex_font_tables(self):
        self.assertEqual(pdfglyphs.OML[0x19], "π")
        self.assertEqual(pdfglyphs.OMS[0x00], "−")
        self.assertIs(pdfglyphs._table({0x00, 0x01, 0x14, 0x15}), pdfglyphs.OMS)
        self.assertIs(pdfglyphs._table({0x0E, 0x0F, 0x19, 0x3C}), pdfglyphs.OML)
        self.assertIsNone(pdfglyphs._table({0x28, 0x29, 0x2B}))


class AccuracyTests(unittest.TestCase):
    def errors(self, spec):
        return [e for e in validate(spec, None).errors]

    def test_wrong_number_in_exploration_is_caught(self):
        spec = copy.deepcopy(FIXTURE)
        spec["explorations"][0]["observe"] = "The mean becomes 2.71 for these equal weights."
        self.assertTrue(any(e.startswith("exploration 1") for e in self.errors(spec)))

    def test_number_at_a_named_setting_is_checked_at_that_setting(self):
        spec = copy.deepcopy(FIXTURE)  # preset 1: n = 4, a = [1, 1, 1, 1] -> mean 2.5; with n = 2 -> 1.5
        spec["explorations"][0]["observe"] = "The mean is 2.5; with n = 2 it drops to 1.5."
        self.assertFalse(any(e.startswith("exploration 1") for e in self.errors(spec)))
        spec["explorations"][0]["observe"] = "The mean is 2.5; with n = 2 it drops to 1.75."
        self.assertTrue(any(e.startswith("exploration 1") for e in self.errors(spec)))

    def test_identical_presets_are_rejected(self):
        spec = copy.deepcopy(FIXTURE)
        spec["explorations"][1]["preset"] = dict(spec["explorations"][0]["preset"])
        self.assertTrue(any("identical results" in e for e in self.errors(spec)))

    def test_check_must_test_computed_results(self):
        spec = copy.deepcopy(FIXTURE)
        spec["checks"][0]["test"] = "Math.abs(Math.sin(0)) < 1e-12"
        self.assertTrue(any("does not test any computed result" in e for e in self.errors(spec)))

    def test_brief_only_citations_and_metadata(self):
        brief = "Section 3, Focal Loss. Explain FL(p_t) and Eq. (5). https://arxiv.org/abs/1708.02002"
        ok = {"paper": {"section": "Section 3", "equation": "Eq. (5)"}, "grounding": {"from_paper": [], "quotes": []}}
        bad = {"paper": {"section": "Section 3.2", "equation": "Eq. (4)", "authors": "Lin, Goyal", "year": "2017"},
               "grounding": {"from_paper": [], "quotes": ["made up"]}}
        rep = Report(); check_brief_citations(ok, brief, rep); self.assertEqual(rep.errors, [])
        rep = Report(); check_brief_citations(bad, brief, rep)
        self.assertTrue(rep.errors and "3.2" in rep.errors[0] and "4" in rep.errors[0])
        self.assertEqual(bad["grounding"]["quotes"], [])
        self.assertEqual((bad["paper"]["authors"], bad["paper"]["year"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
