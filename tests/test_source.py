import unittest

from source import MAX_EXCERPT_CHARS, SourceDocument, SourceError, _extract, select_excerpt


class SourceTests(unittest.TestCase):
    def test_html_removes_navigation_and_scripts(self):
        data = ("<html><nav>Ignore this navigation<meta name='x'><br>still ignored</nav>"
                "<article><h2>Mechanism</h2>"
                "<p>" + "A useful equation relates input and output. " * 10 + "</p>"
                "<script>Ignore this script</script></article></html>").encode()
        document = _extract(data, "text/html", "https://example.org/paper")
        self.assertIn("Mechanism", document.text)
        self.assertNotIn("Ignore this navigation", document.text)
        self.assertNotIn("still ignored", document.text)
        self.assertNotIn("Ignore this script", document.text)

    def test_short_source_is_rejected(self):
        with self.assertRaises(SourceError):
            _extract(b"<html>Empty</html>", "text/html", "test")

    def test_arxiv_math_alttext_kept(self):
        data = ("<p>" + "Context words here. " * 20 + "</p><p>We compute <math alttext=\"\\sqrt{d_k}\"><mi>junk</mi></math> now.</p>").encode()
        text = _extract(data, "text/html", "t").text
        self.assertIn("$\\sqrt{d_k}$", text)
        self.assertNotIn("junk", text)

    def test_excerpt_keeps_relevant_late_section(self):
        text = "Introduction to the paper.\n" + "x" * 800 + "\n" + ("Unrelated background material.\n" * 1200)
        text += "Entropy is the expected information, calculated from a distribution.\n"
        text += "More unrelated material.\n" * 1200
        result = select_excerpt(SourceDocument(text, "html", "test"), "Explain entropy")
        self.assertIn("Entropy is the expected information", result)
        self.assertLessEqual(len(result), MAX_EXCERPT_CHARS)


    def test_named_algorithm_outside_section_is_included(self):
        filler = "Background sentence about optimisation methods. " * 60
        text = ("Paper title\n" + filler + "\nAlgorithm 1: Our update rule. Require step size and decay rates.\n"
                + filler + "\n2 Algorithm\nThe method keeps moving averages of the gradient.\n" + filler * 6)
        ex = select_excerpt(SourceDocument(text, "pdf", "t"), "Section 2 and Algorithm 1: explain the update rule")
        self.assertIn("2 Algorithm", ex)
        self.assertIn("Algorithm 1: Our update rule", ex)

    def test_appendix_and_multiple_sections(self):
        filler = "Unrelated filler text for spacing purposes only. " * 80
        text = ("Title\n" + filler + "\n3 Method\nCore method text.\n" + filler * 3
                + "\nA.2 Derivation\nDerivation details here.\n" + filler * 3)
        ex = select_excerpt(SourceDocument(text, "pdf", "t"), "Section 3 and Appendix A.2: method and derivation")
        self.assertIn("Core method text", ex)
        self.assertIn("Derivation details here", ex)

if __name__ == "__main__":
    unittest.main()
