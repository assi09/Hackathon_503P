"""Development-only smoke test: open a generated page in headless Chromium,
operate every control and preset, report JS errors, save screenshots.
Requires `pip install playwright && python -m playwright install chromium`
(not needed by agent.py)."""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(page_path: Path, shots: Path) -> dict:
    errors: list[str] = []
    report: dict = {"page": str(page_path)}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        page.goto(page_path.resolve().as_uri())
        page.wait_for_timeout(300)
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots / "full.png"), full_page=True)
        report["controls"] = page.locator("#controls .ctl").count()
        report["views"] = page.locator("#views .view").count()
        report["checks"] = page.locator("#live-checks li").all_inner_texts()
        report["warning"] = page.locator("#lab-error").inner_text() if page.locator("#lab-error").is_visible() else ""
        before = page.evaluate("JSON.stringify(window.__explainerApi.compute())")
        changed = []
        for rng in page.locator("#controls input[type=range]").all():
            mx = rng.get_attribute("max")
            rng.fill(mx)
            page.wait_for_timeout(50)
            changed.append(page.evaluate("JSON.stringify(window.__explainerApi.compute())") != before)
            mn = rng.get_attribute("min")
            rng.fill(mn)
            page.wait_for_timeout(50)
        for cb in page.locator("#controls input[type=checkbox]").all():
            cb.click(); page.wait_for_timeout(50); cb.click()
        report["ranges_change_output"] = changed
        for i, b in enumerate(page.locator("button.preset").all()):
            b.click()
            page.wait_for_timeout(400)
            page.screenshot(path=str(shots / f"preset{i + 1}.png"), full_page=False)
            report[f"preset{i + 1}_checks"] = page.locator("#live-checks li").all_inner_texts()
            report[f"preset{i + 1}_warning"] = page.locator("#lab-error").inner_text() if page.locator("#lab-error").is_visible() else ""
        # Only places that display computed numbers; prose may legitimately say "NaN" or "undefined".
        nan = page.evaluate("""Array.from(document.querySelectorAll(
            '#views svg text, #views td, #views .chips span, #views .big, #views .ro-value, #live-checks .shown'))
            .map(e => e.textContent).filter(t => /NaN|undefined|\\[object/.test(t))""") or None
        report["bad_text"] = nan
        page.set_viewport_size({"width": 400, "height": 800})
        page.screenshot(path=str(shots / "mobile.png"), full_page=False)
        report["horizontal_scroll_mobile"] = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        browser.close()
    report["errors"] = errors
    return report


if __name__ == "__main__":
    out = check(Path(sys.argv[1]), Path(sys.argv[2] if len(sys.argv) > 2 else "shots"))
    print(json.dumps(out, indent=1, ensure_ascii=False))
