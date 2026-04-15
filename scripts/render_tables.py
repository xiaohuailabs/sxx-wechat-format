#!/usr/bin/env python3
"""把 article.html 中的 <table> 用 Playwright 渲染成 PNG，并在 HTML 中替换成 <img>。

被 publish_batch.py 调用。也支持独立运行：python3 render_tables.py <article_dir>
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


TABLE_RE = re.compile(r"<table\b[^>]*>[\s\S]*?</table>", re.MULTILINE)

# 渲染用的最小 HTML 外壳 —— 字体保持和微信观感接近
WRAPPER = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:#fff}}
body{{padding:16px;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;color:#222;font-size:15px;line-height:1.6}}
table{{border-collapse:separate;border-spacing:0;width:100%;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid #eee;vertical-align:top}}
thead th{{background:#f6f7f9;font-weight:600}}
tr:last-child td{{border-bottom:none}}
code{{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-family:SFMono-Regular,Consolas,monospace;font-size:13px}}
strong{{font-weight:600}}
</style></head><body>{body}</body></html>"""


def render_and_replace(html: str, article_dir: Path) -> tuple[str, int]:
    """对 html 里所有 <table> 渲染成 PNG 并替换。返回 (new_html, n_tables)。"""
    tables = TABLE_RE.findall(html)
    if not tables:
        return html, 0

    out_dir = article_dir / "images" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # viewport 宽度 900 模拟微信文章阅读宽度（实际阅读宽 ~750，但留余量避免裁切）
        page = browser.new_page(viewport={"width": 900, "height": 200}, device_scale_factor=2)

        png_paths: list[str] = []
        for idx, table_html in enumerate(tables, 1):
            page.set_content(WRAPPER.format(body=table_html))
            page.wait_for_load_state("domcontentloaded")
            table_el = page.locator("table").first
            rel_path = f"images/tables/table-{idx:03d}.png"
            abs_path = article_dir / rel_path
            table_el.screenshot(path=str(abs_path))
            png_paths.append(rel_path)

        browser.close()

    # 按出现顺序替换
    it = iter(png_paths)

    def _replace(_match: re.Match) -> str:
        rel = next(it)
        return f'<section style="margin:18px 0;text-align:center"><img src="{rel}" style="max-width:100%;border-radius:8px" /></section>'

    new_html = TABLE_RE.sub(_replace, html)
    return new_html, len(tables)


def main():
    article_dir = Path(sys.argv[1]).resolve()
    html_path = article_dir / "article.html"
    html = html_path.read_text(encoding="utf-8")
    new_html, n = render_and_replace(html, article_dir)
    if n:
        html_path.write_text(new_html, encoding="utf-8")
        print(f"  渲染 {n} 张表格为 PNG，HTML 已更新")
    else:
        print("  无表格需要处理")


if __name__ == "__main__":
    main()
