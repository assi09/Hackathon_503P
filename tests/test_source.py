import unittest

from source import MAX_EXCERPT_CHARS, SourceDocument, SourceError, _extract, select_excerpt


class SourceTests(unittest.TestCase):
    def test_html_removes_navigation_and_scripts(self):
        data = ("<html><nav>Ignore this navigation<meta name='x'><br>still ignored</nav>"
                "<article><h2>Mechanism</h2>"
                "<p>" + "A useful equation relates input and output. " * 4 + "</p>"
                "<script>Ignore this script</script></article></html>").encode()
        document = _extract(data, "text/html", "https://example.org/paper")
        self.assertIn("Mechanism", document.text)
        self.assertNotIn("Ignore this navigation", document.text)
        self.assertNotIn("still ignored", document.text)
        self.assertNotIn("Ignore this script", document.text)

    def test_short_source_is_rejected(self):
        with self.assertRaises(SourceError):
            _extract(b"<html>Empty</html>", "text/html", "test")

    def test_excerpt_keeps_relevant_late_section(self):
        text = "Introduction to the paper.\n" + ("Unrelated background material.\n" * 1200)
        text += "Entropy is the expected information, calculated from a distribution.\n"
        text += "More unrelated material.\n" * 1200
        result = select_excerpt(SourceDocument(text, "html", "test"), "Explain entropy")
        self.assertIn("Entropy is the expected information", result)
        self.assertLessEqual(len(result), MAX_EXCERPT_CHARS)


if __name__ == "__main__":
    unittest.main()
