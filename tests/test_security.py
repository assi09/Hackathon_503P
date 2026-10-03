"""Hostile inputs: slow/looping servers, malicious generated code, unsafe page content."""

import copy
import json
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import source
from agent import FIELD_LIMITS, load_case
from page import render_page
from validate import validate

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "toy_spec.json").read_text())
CASE = {"source_url": "https://example.org/paper", "focus": "toy", "audience": "students"}


class Hostile(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/tarpit"):  # one byte per second, forever
            self.send_response(200)
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"a")
                    self.wfile.flush()
                    time.sleep(1)
            except OSError:
                return
        if self.path.startswith("/redirect"):  # endless slow redirect chain
            n = int(self.path.rsplit("/", 1)[-1] or 0)
            time.sleep(0.9)
            self.send_response(302)
            self.send_header("Location", f"/redirect/{n + 1}")
            self.end_headers()
            return
        if self.path.startswith("/hang"):  # accept, never answer
            time.sleep(120)


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Hostile)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def assert_bounded(self, path):
        t0 = time.monotonic()
        with self.assertRaises(source.SourceError):
            source.load_source(self.base + path)
        self.assertLess(time.monotonic() - t0, source.FETCH_BUDGET_SECONDS + 1.5)

    def test_tarpit_is_cut_off(self):
        self.assert_bounded("/tarpit")

    def test_redirect_loop_is_cut_off(self):
        self.assert_bounded("/redirect/0")

    def test_hanging_server_is_cut_off(self):
        self.assert_bounded("/hang")

    def test_non_http_scheme_is_refused(self):
        with self.assertRaises(source.SourceError):
            source.load_source("file:///etc/passwd")


def with_compute(code):
    spec = copy.deepcopy(FIXTURE)
    spec["compute"] = code
    return spec


class GeneratedCodeTests(unittest.TestCase):
    def errors(self, spec):
        t0 = time.monotonic()
        rep = validate(spec, None)
        self.assertLess(time.monotonic() - t0, 30, "validation itself must stay bounded")
        return " ".join(rep.errors)

    def test_infinite_loop_is_stopped(self):
        self.assertIn("timed out", self.errors(with_compute("function compute(p) { while (true) {} }")))

    def test_loop_only_for_some_inputs_is_stopped(self):
        code = FIXTURE["compute"].replace("var n = p.n;", "var n = p.n; if (p.n === 6) { while (true) {} }")
        self.assertTrue(self.errors(with_compute(code)))

    def test_memory_bomb_is_stopped(self):
        code = "function compute(p) { var a = []; while (true) { a.push(new Array(1e6).fill(1)); } }"
        self.assertTrue(self.errors(with_compute(code)))

    def test_slow_compute_is_rejected(self):
        code = FIXTURE["compute"].replace("var n = p.n;", "var n = p.n; for (var z = 0, q = 0; z < 4e7; z++) { q += z; }")
        self.assertIn("too slow", self.errors(with_compute(code)))

    def test_network_and_dom_access_are_rejected(self):
        for snippet in ("fetch('x')", "document.title", "window.x", "globalThis.y", "new Function('1')",
                        "(0).constructor.constructor('1')", "'http://evil.example'"):
            code = FIXTURE["compute"].replace("var n = p.n;", f"var n = p.n; var z = {snippet};")
            self.assertIn("forbidden", self.errors(with_compute(code)), snippet)

    def test_breaking_out_of_the_function_wrapper_is_caught(self):
        code = "function compute(p) { return {}; }); while (true) {} (function () {"
        self.assertTrue(self.errors(with_compute(code)))

    def test_unsafe_svg_is_rejected(self):
        for payload in ("<script>1</script>", "<foreignObject><iframe></iframe></foreignObject>",
                        "<rect onload=\\'1\\'/>", "<image href=\\'x.png\\'/>"):
            spec = copy.deepcopy(FIXTURE)
            spec["views"][-1]["code"] = f"function draw(p, r) {{ return '<svg viewBox=\"0 0 1 1\">{payload}</svg>'; }}"
            self.assertTrue(self.errors(spec), payload)


class PageAndInputTests(unittest.TestCase):
    def test_javascript_source_url_is_not_linked(self):
        spec = copy.deepcopy(FIXTURE)
        validate(spec, None)
        html = render_page(spec, {**CASE, "source_url": "javascript:alert(1)"}, "note")
        self.assertNotIn('href="javascript:', html)

    def test_text_cannot_inject_markup(self):
        spec = copy.deepcopy(FIXTURE)
        validate(spec, None)
        spec["idea"] = "</script><script>alert(1)</script><img src=x onerror=alert(1)>"
        spec["params"][0]["label"] = "</script><script>alert(2)</script>"
        html = render_page(spec, CASE, "note")
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)

    def test_oversized_case_fields_are_truncated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.json"
            path.write_text(json.dumps({**CASE, "focus": "x" * 200_000}), encoding="utf-8")
            case = load_case(path)
            self.assertEqual(len(case["focus"]), FIELD_LIMITS["focus"])

    def test_watchdog_exits_nonzero(self):
        code = ("import time, guard; guard.start_watchdog(0.3, lambda: print('expired', flush=True)); "
                "time.sleep(5)")
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
                              cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("expired", proc.stdout)


if __name__ == "__main__":
    unittest.main()
