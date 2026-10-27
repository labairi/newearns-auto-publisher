"""Temporary: screenshot the table on post 3757 and report what styles reach it."""
import json, pathlib
from playwright.sync_api import sync_playwright

URL = "https://newearns.com/free-ai-tools-free-plans/"
out = pathlib.Path("render-check-out"); out.mkdir(exist_ok=True)
report = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, vp in (("desktop", {"width": 1366, "height": 900}), ("mobile", {"width": 390, "height": 844})):
        pg = b.new_page(viewport=vp, device_scale_factor=1)
        pg.goto(URL + "?rc=" + name, wait_until="networkidle", timeout=90000)
        t = pg.locator(".entry-content table").first
        t.scroll_into_view_if_needed()
        t.screenshot(path=str(out / f"table-{name}.png"))
        pg.screenshot(path=str(out / f"viewport-{name}.png"))
        report[name] = pg.evaluate("""() => {
          const t = document.querySelector('.entry-content table');
          const td = t.querySelector('td'), cs = getComputedStyle(td), ts = getComputedStyle(t);
          const css = document.getElementById('wp-custom-css');
          return {parent: t.parentElement.tagName + '.' + t.parentElement.className,
                  table_display: ts.display, table_width: t.getBoundingClientRect().width, table_height: t.getBoundingClientRect().height,
                  td_border: cs.border, td_padding: cs.padding, td_display: cs.display, rows: t.rows.length,
                  custom_css: css ? css.textContent.slice(0, 1500) : null,
                  table_html_start: t.outerHTML.slice(0, 600)};
        }""")
    b.close()
(out / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
