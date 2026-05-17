"""
test_district_balloon.py
────────────────────────
TEST SCRIPT — District balloon click extraction
Clicks each TPCODL district SVG path on the IMD district page and
tries to capture the popup/balloon text that appears.

Saves results to: data/balloon_test_results.json
                  data/balloon_test_screenshot_<DISTRICT>.png

Run locally:  python test_district_balloon.py
GitHub:       can be triggered manually from Actions tab
"""

import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Config ────────────────────────────────────────────────────
STATE_ID    = "10"
DISTRICT_URL = f"https://mausam.imd.gov.in/imd_latest/contents/districtwisewarnings_mc.php?id={STATE_ID}"

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Only test the 8 TPCODL districts — exact IMD names
TPCODL_DISTRICTS = [
    "ANUGUL",
    "CUTTACK",
    "DHENKANAL",
    "JAGATSINGHPUR",
    "KENDRAPARHA",   # IMD uses this spelling (normalized to KENDRAPARA in main script)
    "KHORDHA",
    "NAYAGARH",
    "PURI",
]

# All possible selectors/locations where balloon text might appear after click
BALLOON_SELECTORS = [
    ".amcharts-balloon-div",          # amCharts default balloon
    "div.amcharts-balloon-div",
    "#info",                          # custom info div used by some IMD pages
    "#info p",
    "div[class*='balloon']",
    "div[class*='tooltip']",
    "div[style*='z-index: 2']",       # amCharts balloon uses inline z-index:2
    "div[style*='z-index:2']",
    ".amcharts-chart-div > div:not([class])",  # unnamed div injected by amCharts
]


def launch_browser(p):
    return p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
        ],
    )


def extract_balloon_text_from_page(page) -> str:
    """
    Try every known balloon selector and return the first non-empty text found.
    Also dumps all visible div content for debugging.
    """
    for sel in BALLOON_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                text = (el.inner_text() or "").strip()
                if text and len(text) > 3:
                    return f"[{sel}] {text}"
        except Exception:
            pass

    # Fallback: dump all divs with inline style containing z-index >= 2 (amCharts injects balloon this way)
    try:
        all_divs = page.query_selector_all("div[style]")
        for div in all_divs:
            style = div.get_attribute("style") or ""
            if "z-index" in style:
                text = (div.inner_text() or "").strip()
                if text and len(text) > 5 and "amcharts" not in text.lower():
                    return f"[z-index div] {text}"
    except Exception:
        pass

    return ""


def try_click_district(page, district_name: str) -> dict:
    """
    Attempt multiple strategies to click a district and read its balloon.
    Returns dict with all findings.
    """
    result = {
        "district":        district_name,
        "fill_color":      "",
        "balloon_text":    "",
        "click_strategy":  "",
        "error":           "",
    }

    # ── Strategy 1: Click the SVG path by aria-label ──────────
    selector = f"path[aria-label='{district_name}  ']"   # IMD adds trailing spaces
    selector_nospace = f"path[aria-label='{district_name}']"

    path_el = page.query_selector(selector) or page.query_selector(selector_nospace)

    # Also try case-insensitive match via JS
    if not path_el:
        try:
            path_el = page.evaluate_handle(
                f"""
                () => {{
                    const paths = document.querySelectorAll('path[aria-label]');
                    for (const p of paths) {{
                        if (p.getAttribute('aria-label').trim().toUpperCase() === '{district_name}') {{
                            return p;
                        }}
                    }}
                    return null;
                }}
                """
            )
            # evaluate_handle returns JSHandle — check if it's a real element
            tag = page.evaluate("el => el ? el.tagName : null", path_el)
            if not tag:
                path_el = None
        except Exception:
            path_el = None

    if not path_el:
        result["error"] = "SVG path not found by aria-label"
        return result

    # Get fill color before clicking
    try:
        result["fill_color"] = page.evaluate("el => el.getAttribute('fill')", path_el)
    except Exception:
        pass

    # ── Try hover first (amCharts shows balloon on rollOver) ──
    try:
        path_el.scroll_into_view_if_needed()
        path_el.hover()
        page.wait_for_timeout(800)

        balloon = extract_balloon_text_from_page(page)
        if balloon:
            result["balloon_text"]   = balloon
            result["click_strategy"] = "hover"
            # Screenshot after hover
            ss_path = DATA_DIR / f"balloon_hover_{district_name}.png"
            page.screenshot(path=str(ss_path))
            print(f"  [hover] Got balloon text → {balloon[:80]}")
            return result
    except Exception as e:
        pass

    # ── Try click ─────────────────────────────────────────────
    try:
        path_el.click()
        page.wait_for_timeout(1000)

        balloon = extract_balloon_text_from_page(page)
        if balloon:
            result["balloon_text"]   = balloon
            result["click_strategy"] = "click"
            ss_path = DATA_DIR / f"balloon_click_{district_name}.png"
            page.screenshot(path=str(ss_path))
            print(f"  [click] Got balloon text → {balloon[:80]}")
            return result
    except Exception as e:
        result["error"] = f"click failed: {e}"

    # ── Try JS dispatchEvent (simulate mouse events) ───────────
    try:
        page.evaluate(
            """
            (selector) => {
                const paths = document.querySelectorAll('path[aria-label]');
                for (const p of paths) {
                    if (p.getAttribute('aria-label').trim().toUpperCase() === selector) {
                        const bbox = p.getBoundingClientRect();
                        const cx = bbox.left + bbox.width / 2;
                        const cy = bbox.top  + bbox.height / 2;
                        ['mouseover','mousemove','mouseenter'].forEach(evtName => {
                            p.dispatchEvent(new MouseEvent(evtName, {
                                bubbles: true, cancelable: true,
                                clientX: cx, clientY: cy
                            }));
                        });
                    }
                }
            }
            """,
            district_name,
        )
        page.wait_for_timeout(1200)

        balloon = extract_balloon_text_from_page(page)
        if balloon:
            result["balloon_text"]   = balloon
            result["click_strategy"] = "js_dispatch"
            ss_path = DATA_DIR / f"balloon_js_{district_name}.png"
            page.screenshot(path=str(ss_path))
            print(f"  [js_dispatch] Got balloon text → {balloon[:80]}")
            return result
    except Exception as e:
        result["error"] += f" | js_dispatch failed: {e}"

    # ── Strategy 2: amCharts fires 'hit' on its own object ────
    # amCharts stores chart reference in window.chart — try triggering rollOver
    try:
        page.evaluate(
            """
            (districtName) => {
                if (window.chart && window.chart.dataProvider && window.chart.dataProvider.areas) {
                    window.chart.dataProvider.areas.forEach(area => {
                        if (area.title && area.title.trim().toUpperCase() === districtName) {
                            window.chart.rollOverMapObject(area);
                        }
                    });
                }
            }
            """,
            district_name,
        )
        page.wait_for_timeout(1000)

        balloon = extract_balloon_text_from_page(page)
        if balloon:
            result["balloon_text"]   = balloon
            result["click_strategy"] = "amcharts_rollOver"
            print(f"  [amcharts_rollOver] Got balloon text → {balloon[:80]}")
            return result
    except Exception as e:
        result["error"] += f" | amcharts_rollOver failed: {e}"

    # ── Nothing worked — take debug screenshot ─────────────────
    ss_path = DATA_DIR / f"balloon_failed_{district_name}.png"
    page.screenshot(path=str(ss_path))
    print(f"  [FAIL] No balloon captured. Screenshot: {ss_path.name}")

    # Dump all visible text on page for debug
    try:
        all_text = page.evaluate(
            """
            () => {
                const divs = document.querySelectorAll('div[style]');
                const out = [];
                divs.forEach(d => {
                    const t = d.innerText.trim();
                    if (t && t.length > 5 && t.length < 500) out.push(t);
                });
                return out.slice(0, 20);
            }
            """
        )
        result["debug_page_text_samples"] = all_text
    except Exception:
        pass

    return result


def main():
    print(f"\n[balloon-test] IMD District Balloon Click Test")
    print(f"[balloon-test] URL: {DISTRICT_URL}")
    print(f"[balloon-test] Testing {len(TPCODL_DISTRICTS)} TPCODL districts\n")

    all_results = []
    scraped_at  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            locale="en-IN",
        )
        page = context.new_page()

        # ── Load page ─────────────────────────────────────────
        print("[balloon-test] Loading district page...")
        try:
            page.goto(DISTRICT_URL, wait_until="networkidle", timeout=60000)
        except PlaywrightTimeout:
            print("[balloon-test] WARNING: page load timed out — continuing anyway")

        # Wait for SVG map to render
        try:
            page.wait_for_selector("svg path.amcharts-map-area", timeout=20000)
            page.wait_for_timeout(3000)   # extra settle time for amCharts JS
            print("[balloon-test] SVG map loaded ✓")
        except PlaywrightTimeout:
            print("[balloon-test] WARNING: SVG paths not found — amCharts may not have rendered")

        # Full page screenshot before clicking anything
        page.screenshot(path=str(DATA_DIR / "balloon_test_initial.png"), full_page=True)
        print("[balloon-test] Initial screenshot saved\n")

        # ── Check if amCharts chart object is accessible ──────
        has_chart = page.evaluate("() => typeof window.chart !== 'undefined'")
        print(f"[balloon-test] window.chart accessible: {has_chart}")

        # Dump amCharts version and available methods
        if has_chart:
            chart_info = page.evaluate(
                """
                () => {
                    const c = window.chart;
                    return {
                        type: c.type || 'unknown',
                        version: (window.AmCharts && window.AmCharts.version) || 'unknown',
                        methods: Object.getOwnPropertyNames(Object.getPrototypeOf(c))
                                       .filter(m => typeof c[m] === 'function')
                                       .slice(0, 20),
                    };
                }
                """
            )
            print(f"[balloon-test] Chart info: {json.dumps(chart_info, indent=2)}\n")

        # ── Click each TPCODL district ─────────────────────────
        for district in TPCODL_DISTRICTS:
            print(f"[balloon-test] Testing: {district}")

            # Reload page between districts to reset balloon state
            # (amCharts sometimes gets stuck if balloon is already visible)
            if district != TPCODL_DISTRICTS[0]:
                try:
                    page.reload(wait_until="networkidle", timeout=30000)
                    page.wait_for_selector("svg path.amcharts-map-area", timeout=15000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            result = try_click_district(page, district)
            result["scraped_at"] = scraped_at
            all_results.append(result)

            status = "✅" if result["balloon_text"] else "❌"
            print(f"  {status} {district}: fill={result['fill_color']} | "
                  f"strategy={result['click_strategy']} | "
                  f"error={result['error'][:60] if result['error'] else 'none'}\n")

        browser.close()

    # ── Save results ───────────────────────────────────────────
    out_path = DATA_DIR / "balloon_test_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "scraped_at":   scraped_at,
                "url":          DISTRICT_URL,
                "total":        len(all_results),
                "success":      sum(1 for r in all_results if r["balloon_text"]),
                "failed":       sum(1 for r in all_results if not r["balloon_text"]),
                "results":      all_results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\n[balloon-test] ── SUMMARY ──────────────────────────────")
    print(f"[balloon-test] Total tested : {len(all_results)}")
    print(f"[balloon-test] Balloon got  : {sum(1 for r in all_results if r['balloon_text'])}")
    print(f"[balloon-test] Failed       : {sum(1 for r in all_results if not r['balloon_text'])}")
    print(f"[balloon-test] Results JSON : {out_path}")
    print(f"\n[balloon-test] Per-district:")
    for r in all_results:
        status = "✅" if r["balloon_text"] else "❌"
        print(f"  {status} {r['district']:20s} | {r['fill_color']:10s} | "
              f"strategy: {r['click_strategy']:20s} | {r['balloon_text'][:60]}")

    print("\n[balloon-test] DONE")


if __name__ == "__main__":
    main()
