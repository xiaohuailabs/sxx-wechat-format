#!/usr/bin/env python3
"""把多篇 Markdown 聚合为一条微信公众号多图文草稿（最多 8 篇）。

微信 /cgi-bin/draft/add 接口的 articles 数组支持 1-8 篇，第 1 篇是头条、
其余是次条。本脚本自动跑：
  每篇 Markdown
    → format.py 排版为 HTML
    → <table> 转 PNG（通过 render_tables.py）
    → 代码块扁平化（通过 clean_code.py）
    → 上传正文图片到微信 CDN
    → 上传封面图为素材
  最后一次性 POST 到 /cgi-bin/draft/add。

两种调用方式：

  (a) JSON 批次配置（推荐）：
    python3 publish_batch.py --batch batch.json

    batch.json:
      {
        "theme": "magazine",
        "author": "(覆盖 config.json 默认作者，可省)",
        "articles": [
          {"input": "/path/to/01.md", "cover": "/path/to/cover1.png"},
          {"input": "/path/to/02.md", "cover": "/path/to/cover2.png"}
        ]
      }

  (b) Inline 命令行：
    python3 publish_batch.py --theme magazine \\
      --article 01.md:cover1.png \\
      --article 02.md:cover2.png
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import requests

# 复用同目录下的 publish.py 核心函数
from publish import (
    CONFIG,
    SCRIPT_DIR,
    extract_title_from_html,
    get_access_token,
    replace_all_images,
    upload_thumb_image,
)
from clean_code import clean as clean_code_html


def _format_article(md_path: Path, theme: str) -> tuple[str, str, Path]:
    """跑 format.py，返回 (title, html, article_dir)。"""
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT_DIR / "format.py"),
            "--input", str(md_path),
            "--theme", theme,
            "--no-open",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"排版失败 ({md_path.name}):\n{result.stderr}")
        sys.exit(1)

    output_base = Path(CONFIG["output_dir"])
    stem = re.sub(r"-(公众号|小红书|微博)$", "", md_path.stem)
    article_dir = output_base / stem
    html_path = article_dir / "article.html"
    if not html_path.exists():
        print(f"未找到 article.html: {html_path}")
        sys.exit(1)
    html = html_path.read_text(encoding="utf-8")
    title = extract_title_from_html(html) or stem
    return title, html, article_dir


def _load_batch(args) -> tuple[str, str, list[dict]]:
    """返回 (theme, author, [{input, cover}, ...])。"""
    default_theme = CONFIG["settings"].get("default_theme", "newspaper")
    default_author = CONFIG.get("wechat", {}).get("author", "")

    if args.batch:
        data = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        theme = data.get("theme") or default_theme
        author = data.get("author") or default_author
        items = data["articles"]
    else:
        theme = args.theme or default_theme
        author = args.author or default_author
        items = []
        for spec in args.article or []:
            if ":" not in spec:
                print(f"错误: --article 语法应为 <md>:<cover>，收到 {spec!r}")
                sys.exit(2)
            md, cover = spec.rsplit(":", 1)
            items.append({"input": md, "cover": cover})

    if not items:
        print("错误: 未提供任何文章（用 --batch 或 --article）")
        sys.exit(2)
    if len(items) > 8:
        print(f"错误: 微信多图文最多 8 篇，收到 {len(items)} 篇")
        sys.exit(2)

    return theme, author, items


def main():
    parser = argparse.ArgumentParser(
        description="多篇 Markdown → 单条微信多图文草稿",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--batch", "-b",
                        help="JSON 批次配置文件路径")
    parser.add_argument("--article", action="append", metavar="MD:COVER",
                        help="单篇文章，格式 <markdown路径>:<封面图路径>，可重复")
    parser.add_argument("--theme", "-t",
                        help="排版主题（默认读 config.json）")
    parser.add_argument("--author", "-a",
                        help="作者名（默认读 config.json）")
    parser.add_argument("--no-table-image", action="store_true",
                        help="禁用 <table> → PNG 转换")
    parser.add_argument("--no-flatten-code", action="store_true",
                        help="禁用代码块扁平化")
    parser.add_argument("--dry-run", action="store_true",
                        help="上传图片但不推送草稿箱")
    args = parser.parse_args()

    theme, author, items = _load_batch(args)

    # 延迟 import render_tables，playwright 可能未装
    render_and_replace = None
    if not args.no_table_image:
        try:
            from render_tables import render_and_replace  # noqa: F811
        except ImportError:
            print("⚠ Playwright 未安装，跳过表格转图。"
                  "需要: pip install playwright && playwright install chromium")

    token = get_access_token()
    print(f"✓ token ok，共 {len(items)} 篇待处理\n")

    # 封面去重：同一张图只上传一次，共享 media_id
    cover_media_ids: dict[str, str] = {}
    articles = []

    for idx, item in enumerate(items, 1):
        md_path = Path(item["input"]).resolve()
        cover_path = str(Path(item["cover"]).resolve())
        print(f"── [{idx}/{len(items)}] {md_path.name} ──")

        title, html, article_dir = _format_article(md_path, theme)
        print(f"  标题: {title}")
        print(f"  HTML 长度: {len(html)} 字符")

        # 表格转图
        if render_and_replace is not None:
            html, n_tables = render_and_replace(html, article_dir)
            if n_tables:
                print(f"  表格渲染: {n_tables} 张 → PNG")

        # 代码扁平化
        if not args.no_flatten_code:
            html, n_code = clean_code_html(html)
            if n_code:
                print(f"  代码块扁平化: {n_code} 个")

        # 回写调试
        (article_dir / "article.html").write_text(html, encoding="utf-8")

        # 正文图片上传
        html, replaced, failed = replace_all_images(html, article_dir, token)
        if replaced or failed:
            print(f"  正文图片: {replaced} 成功 / {failed} 失败")
            if failed and not replaced:
                print("  ✗ 全部失败，中止")
                sys.exit(1)

        # 封面
        if cover_path not in cover_media_ids:
            media_id = upload_thumb_image(token, cover_path)
            if not media_id:
                print(f"  ✗ 封面上传失败: {cover_path}")
                sys.exit(1)
            cover_media_ids[cover_path] = media_id
            print(f"  ✓ 封面上传 media_id={media_id[:16]}...")
        else:
            print(f"  ✓ 封面复用")

        articles.append({
            "title": title,
            "author": author,
            "content": html,
            "content_source_url": "",
            "thumb_media_id": cover_media_ids[cover_path],
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        })
        print()

    if args.dry_run:
        print(f"[dry-run] 准备就绪，跳过推送。共 {len(articles)} 篇。")
        return

    print("推送多图文草稿...")
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    body = json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        url, data=body,
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    result = resp.json()

    if "media_id" in result:
        print(f"\n{'=' * 50}")
        print(f"  发布成功！{len(articles)} 篇图文合为一条多图文草稿")
        print(f"  草稿 media_id: {result['media_id']}")
        print(f"  → 公众号后台 → 草稿箱 查看")
        print(f"{'=' * 50}")
    else:
        print(f"\n发布失败: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
