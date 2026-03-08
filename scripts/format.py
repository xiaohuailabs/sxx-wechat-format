#!/usr/bin/env python3
"""微信公众号文章排版工具

将 Markdown 文件转为微信公众号兼容的内联样式 HTML。
微信编辑器不支持 <style> 标签、CSS class 和 JS，
所以所有样式必须用 style="..." 内联写在每个标签上。

用法:
    python3 format.py --input article.md --theme elegant [--vault-root /path] [--output /path]
"""

import argparse
import json
import os
import re
import shutil
import sys
import webbrowser
from pathlib import Path

import markdown

# ── 路径 ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
THEMES_DIR = SKILL_DIR / "themes"
TEMPLATE_DIR = SKILL_DIR / "templates"

with open(SKILL_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

OUTPUT_DIR = Path(CONFIG["output_dir"])
VAULT_ROOT = Path(CONFIG["vault_root"])
DEFAULT_THEME = CONFIG["settings"]["default_theme"]
AUTO_OPEN = CONFIG["settings"]["auto_open_browser"]


# ── 工具函数 ────────────────────────────────────────────────────────────
def count_words(text: str) -> int:
    """统计中文文章字数（中文字符 + 英文单词）"""
    clean = re.sub(r"[#*`\[\]()!>|{}_~\-]", "", text)
    clean = re.sub(r"\n+", "\n", clean)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", clean))
    english = len(re.findall(r"[a-zA-Z]+", clean))
    return chinese + english


def extract_title(content: str, filepath: Path) -> str:
    """从内容或文件名提取标题"""
    # 从 frontmatter 提取
    fm = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm:
        for line in fm.group(1).split("\n"):
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    # 从 H1 提取
    h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if h1:
        return h1.group(1).strip()
    # 从文件名提取
    name = filepath.stem
    name = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", name)
    name = re.sub(r"-(公众号|小红书|微博)$", "", name)
    return name or filepath.stem


def strip_frontmatter(content: str) -> str:
    """去掉 YAML frontmatter"""
    return re.sub(r"^---\n.*?\n---\n*", "", content, flags=re.DOTALL)


def convert_wikilinks(text: str, vault_root: Path, output_dir: Path) -> str:
    """把 Obsidian ![[image.jpg]] 转为 <img> 标签，复制图片到输出目录"""
    images_dir = output_dir / "images"

    def replace_img(match):
        filename = match.group(1).strip()
        # 处理带尺寸的 wikilink: ![[image.jpg|300]]
        if "|" in filename:
            filename = filename.split("|")[0].strip()
        # 在 vault 中搜索图片
        for root, dirs, files in os.walk(vault_root):
            if filename in files:
                img_path = Path(root) / filename
                images_dir.mkdir(parents=True, exist_ok=True)
                dest = images_dir / filename
                if not dest.exists():
                    shutil.copy2(img_path, dest)
                # 返回占位标记，后面注入样式时处理
                return f'<section data-role="img-wrapper"><img src="images/{filename}" alt="{filename}"></section>'
        return f'<span style="color:#999;">[图片: {filename}]</span>'

    return re.sub(r"!\[\[([^\]]+)\]\]", replace_img, text)


def copy_markdown_images(text: str, input_dir: Path, output_dir: Path) -> str:
    """处理标准 Markdown 图片 ![alt](path)，把本地相对路径图片复制到输出目录"""
    images_dir = output_dir / "images"

    def replace_md_img(match):
        alt = match.group(1)
        src = match.group(2).strip()
        # 跳过外链（http/https）
        if src.startswith(("http://", "https://")):
            return match.group(0)
        # 解析相对路径，基于输入文件所在目录
        img_path = (input_dir / src).resolve()
        if img_path.exists():
            images_dir.mkdir(parents=True, exist_ok=True)
            dest = images_dir / img_path.name
            if not dest.exists():
                shutil.copy2(img_path, dest)
            # 统一改为 images/filename 路径
            return f'![{alt}](images/{img_path.name})'
        return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_md_img, text)


def extract_links_as_footnotes(html: str) -> tuple[str, str]:
    """提取外部链接转为脚注格式

    返回: (处理后的 HTML, 脚注 HTML)
    """
    footnotes = []
    counter = [0]

    def replace_link(match):
        full = match.group(0)
        href = match.group(1)
        text = match.group(2)

        # 跳过锚点链接和非 http 链接
        if not href.startswith("http"):
            return full

        counter[0] += 1
        idx = counter[0]
        footnotes.append((idx, text, href))
        # 正文中加上标注
        return f'{text}<sup style="{{footnote_sup}}">[{idx}]</sup>'

    processed = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', replace_link, html)

    if not footnotes:
        return processed, ""

    # 生成脚注区
    fn_html = '<section style="{footnote_section}">\n'
    fn_html += '<p style="{footnote_title}">参考链接</p>\n'
    for idx, text, href in footnotes:
        fn_html += f'<p style="{{footnote_item}}">[{idx}] {text}: {href}</p>\n'
    fn_html += "</section>"

    return processed, fn_html


def process_callouts(text: str) -> str:
    """处理 Obsidian callout 语法: > [!callout] 内容"""
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        # 检查是否是 callout 开始
        callout_match = re.match(r"^>\s*\[!([\w]+)\]\s*(.*)", lines[i])
        if callout_match:
            callout_type = callout_match.group(1)
            title = callout_match.group(2).strip()
            content_lines = []
            i += 1
            # 收集 callout 内容行
            while i < len(lines) and lines[i].startswith(">"):
                content_lines.append(lines[i][1:].strip())
                i += 1
            content = "\n".join(content_lines)
            # 用特殊标记包裹
            if title:
                result.append(f'<div class="callout" data-type="{callout_type}">')
                result.append(f'<p class="callout-title">{title}</p>')
            else:
                result.append(f'<div class="callout" data-type="{callout_type}">')
            result.append(f'<p class="callout-content">{content}</p>')
            result.append("</div>")
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def md_to_html(content: str) -> str:
    """Markdown 转 HTML"""
    html = markdown.markdown(
        content,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    return html


# ── 核心：内联样式注入 ──────────────────────────────────────────────────
def build_style_string(props: dict) -> str:
    """把主题 JSON 的属性字典转成 CSS style 字符串

    JSON key 下划线 → CSS 连字符: font_size → font-size
    """
    parts = []
    for key, val in props.items():
        css_key = key.replace("_", "-")
        parts.append(f"{css_key}:{val}")
    return ";".join(parts)


def inject_dark_mode_attrs(html: str, dark_mode: dict, style_map: dict) -> str:
    """为微信深色模式添加 data-darkmode-* 属性

    通过匹配元素的 style 字符串来定位目标元素，
    然后添加对应的深色模式颜色覆盖。
    """
    for tag_key, dark_cfg in dark_mode.items():
        if tag_key not in style_map:
            continue
        style_str = style_map[tag_key]
        if not style_str:
            continue
        attrs = []
        if "bgcolor" in dark_cfg:
            attrs.append(f'data-darkmode-bgcolor="{dark_cfg["bgcolor"]}"')
        if "color" in dark_cfg:
            attrs.append(f'data-darkmode-color="{dark_cfg["color"]}"')
        if not attrs:
            continue
        dark_attr_str = " ".join(attrs)
        html = html.replace(
            f'style="{style_str}"',
            f'style="{style_str}" {dark_attr_str}',
        )
    return html


def inject_inline_styles(html: str, theme: dict) -> str:
    """为每个 HTML 标签注入内联 style 属性"""
    styles = theme["styles"]

    # 构建各标签的 style 字符串
    style_map = {}
    for tag_key, props in styles.items():
        style_map[tag_key] = build_style_string(props)

    # === 1. 处理列表（微信特殊处理：ul/ol → section 模拟）===
    html = convert_lists_to_sections(html, style_map)

    # === 2. 处理 callout 块 ===
    html = convert_callouts(html, style_map)

    # === 3. 处理 blockquote 内部的 p 标签 ===
    def style_blockquote(match):
        bq_content = match.group(1)
        # blockquote 内部的 p 标签用 blockquote_p 样式
        if "blockquote_p" in style_map:
            bq_content = re.sub(
                r"<p>",
                f'<p style="{style_map["blockquote_p"]}">',
                bq_content,
            )
        bq_style = style_map.get("blockquote", "")
        return f'<blockquote style="{bq_style}">{bq_content}</blockquote>'

    html = re.sub(r"<blockquote>(.*?)</blockquote>", style_blockquote, html, flags=re.DOTALL)

    # === 4. 处理 pre > code（必须在单独的 code 之前）===
    def style_pre(match):
        pre_content = match.group(1)
        pre_style = style_map.get("pre", "")
        pre_code_style = style_map.get("pre_code", "")
        code_block_style = style_map.get("code_block", "")
        code_header_style = style_map.get("code_header", "")
        # 保护空格：公众号编辑器会压缩连续空格，用 &nbsp; 替换
        # 先保护已有的 HTML 标签，只替换标签外的空格
        def protect_spaces(text):
            parts = re.split(r'(<[^>]+>)', text)
            for i, part in enumerate(parts):
                if not part.startswith('<'):
                    part = part.replace(' ', '&nbsp;')
                parts[i] = part
            return ''.join(parts)
        pre_content = protect_spaces(pre_content)
        # 替换内部 code 标签
        pre_content = re.sub(
            r"<code[^>]*>",
            f'<code style="{pre_code_style}">',
            pre_content,
        )
        # Mac 风格工具栏（红黄绿三圆点）
        dot_base = "display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px"
        mac_header = (
            f'<section style="{code_header_style}">'
            f'<span style="{dot_base};background:#FF5F56"></span>'
            f'<span style="{dot_base};background:#FFBD2E"></span>'
            f'<span style="{dot_base};background:#27C93F"></span>'
            f'</section>'
        )
        return (
            f'<section style="{code_block_style}">'
            f'{mac_header}'
            f'<pre style="{pre_style}">{pre_content}</pre>'
            f'</section>'
        )

    html = re.sub(r"<pre>(.*?)</pre>", style_pre, html, flags=re.DOTALL)

    # === 5. 普通标签注入样式 ===
    simple_tags = ["h1", "h2", "h3", "p", "strong", "em", "a", "img", "hr", "code", "table", "th", "td"]
    for tag in simple_tags:
        if tag not in style_map:
            continue
        s = style_map[tag]
        if tag == "hr":
            html = re.sub(r"<hr\s*/?>", f'<hr style="{s}">', html)
        elif tag == "img":
            html = re.sub(r"<img ", f'<img style="{s}" ', html)
        elif tag == "code":
            # 只处理不在 pre 内的 code（pre 内的已经处理过了）
            # 通过检查是否已有 style 属性来避免重复
            html = re.sub(r'<code(?!\s+style)>', f'<code style="{s}">', html)
        else:
            html = re.sub(rf"<{tag}(?!\s+style)>", f'<{tag} style="{s}">', html)
            html = re.sub(rf"<{tag}(\s+(?!style)[^>]*)>", f'<{tag} style="{s}"\\1>', html)

    # === 6. 处理脚注占位符样式 ===
    for key in ["footnote_section", "footnote_title", "footnote_item", "footnote_sup"]:
        if key in style_map:
            html = html.replace("{" + key + "}", style_map[key])

    # === 7. 处理图片包裹容器 ===
    if "img_wrapper" in style_map:
        html = re.sub(
            r'<section data-role="img-wrapper">',
            f'<section data-role="img-wrapper" style="{style_map["img_wrapper"]}">',
            html,
        )

    # === 8. 处理 wrapper（整体背景色，用于 dark/retro 等主题）===
    if "wrapper" in style_map:
        html = f'<section style="{style_map["wrapper"]}">{html}</section>'

    # === 9. 注入微信深色模式属性 ===
    dark_mode = theme.get("dark_mode", {})
    if dark_mode:
        html = inject_dark_mode_attrs(html, dark_mode, style_map)

    return html


def convert_lists_to_sections(html: str, style_map: dict) -> str:
    """把 ul/ol 列表转为 section 模拟（微信兼容）"""
    wrapper_style = style_map.get("list_wrapper", "")
    row_style = style_map.get("list_item_row", "")
    bullet_style = style_map.get("list_item_bullet", "")
    text_style = style_map.get("list_item_text", "")

    def replace_ul(match):
        items = re.findall(r"<li>(.*?)</li>", match.group(0), re.DOTALL)
        rows = []
        for item in items:
            # 清理 item 中的 p 标签
            item = re.sub(r"</?p>", "", item).strip()
            rows.append(
                f'<section style="{row_style}">'
                f'<span style="{bullet_style}">•</span>'
                f'<span style="{text_style}">{item}</span>'
                f"</section>"
            )
        return f'<section style="{wrapper_style}">{"".join(rows)}</section>'

    def replace_ol(match):
        ol_bullet_style = style_map.get("ol_item_bullet", bullet_style)
        items = re.findall(r"<li>(.*?)</li>", match.group(0), re.DOTALL)
        rows = []
        for idx, item in enumerate(items, 1):
            item = re.sub(r"</?p>", "", item).strip()
            rows.append(
                f'<section style="{row_style}">'
                f'<span style="{ol_bullet_style}">{idx}</span>'
                f'<span style="{text_style}">{item}</span>'
                f"</section>"
            )
        return f'<section style="{wrapper_style}">{"".join(rows)}</section>'

    html = re.sub(r"<ul>.*?</ul>", replace_ul, html, flags=re.DOTALL)
    html = re.sub(r"<ol>.*?</ol>", replace_ol, html, flags=re.DOTALL)
    return html


def convert_callouts(html: str, style_map: dict) -> str:
    """转换 callout 块为带样式的 HTML"""
    callout_style = style_map.get("callout", "")
    title_style = style_map.get("callout_title", "")
    content_style = style_map.get("callout_content", "")

    def replace_callout(match):
        inner = match.group(1)
        # 提取标题
        title_match = re.search(r'<p class="callout-title">(.*?)</p>', inner)
        content_match = re.search(r'<p class="callout-content">(.*?)</p>', inner, re.DOTALL)

        result = f'<section style="{callout_style}">'
        if title_match and title_match.group(1):
            result += f'<p style="{title_style}">{title_match.group(1)}</p>'
        if content_match:
            result += f'<p style="{content_style}">{content_match.group(1)}</p>'
        result += "</section>"
        return result

    return re.sub(r'<div class="callout"[^>]*>(.*?)</div>', replace_callout, html, flags=re.DOTALL)


# ── 预览 HTML 生成 ──────────────────────────────────────────────────────
def generate_preview(article_html: str, footnote_html: str, theme: dict,
                     title: str, word_count: int, output_path: Path):
    """生成浏览器预览 HTML 文件"""
    template_path = TEMPLATE_DIR / "preview.html"
    template = template_path.read_text(encoding="utf-8")

    # 合并文章和脚注
    full_html = article_html
    if footnote_html:
        full_html += "\n" + footnote_html

    preview_html = (
        template
        .replace("{{TITLE}}", title)
        .replace("{{THEME_NAME}}", theme.get("name", ""))
        .replace("{{WORD_COUNT}}", f"{word_count:,}")
        .replace("{{ARTICLE_HTML}}", full_html)
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(preview_html, encoding="utf-8")
    return output_path


# ── 主流程 ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="微信公众号文章排版工具")
    parser.add_argument("--input", "-i", required=True, help="输入 Markdown 文件路径")
    parser.add_argument("--theme", "-t", default=DEFAULT_THEME, help=f"主题名称（默认: {DEFAULT_THEME}）")
    parser.add_argument("--vault-root", default=str(VAULT_ROOT), help="Obsidian Vault 根目录")
    parser.add_argument("--output", "-o", default=str(OUTPUT_DIR), help="输出目录")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    input_path = Path(args.input)
    vault_root = Path(args.vault_root)
    output_base = Path(args.output)
    theme_name = args.theme

    # 每篇文章一个子目录: 公众号排版/2026-02-26-文章名/
    file_stem = re.sub(r"-(公众号|小红书|微博)$", "", input_path.stem)
    output_dir = output_base / file_stem

    # 验证输入文件
    if not input_path.exists():
        print(f"错误: 文件不存在 - {input_path}")
        sys.exit(1)

    # 加载主题
    theme_path = THEMES_DIR / f"{theme_name}.json"
    if not theme_path.exists():
        available = [p.stem for p in THEMES_DIR.glob("*.json")]
        print(f"错误: 主题 '{theme_name}' 不存在。可用主题: {', '.join(available)}")
        sys.exit(1)

    with open(theme_path, encoding="utf-8") as f:
        theme = json.load(f)

    print(f"主题: {theme['name']} ({theme_name})")
    print(f"输入: {input_path}")

    # 读取文章
    content = input_path.read_text(encoding="utf-8")
    title = extract_title(content, input_path)
    word_count = count_words(content)
    print(f"标题: {title}")
    print(f"字数: {word_count:,}")

    # 处理流程
    # 1. 去 frontmatter
    content = strip_frontmatter(content)

    # 2. 处理 callout
    content = process_callouts(content)

    # 3. 处理 Obsidian 图片 wikilink
    output_dir.mkdir(parents=True, exist_ok=True)
    content = convert_wikilinks(content, vault_root, output_dir)

    # 3.5 处理标准 Markdown 图片（本地相对路径复制到输出目录）
    content = copy_markdown_images(content, input_path.parent, output_dir)

    # 4. Markdown → HTML
    html = md_to_html(content)

    # 5. 外链 → 脚注
    html, footnote_html = extract_links_as_footnotes(html)

    # 6. 注入内联样式
    html = inject_inline_styles(html, theme)
    if footnote_html:
        footnote_html = inject_inline_styles(footnote_html, theme)

    # 7. 保存纯文章 HTML（供发布脚本使用）
    full_article = html
    if footnote_html:
        full_article += "\n" + footnote_html
    article_path = output_dir / "article.html"
    article_path.write_text(full_article, encoding="utf-8")

    # 8. 保存完整预览 HTML（带手机框+复制按钮）到成品目录
    preview_path = output_dir / "preview.html"
    generate_preview(html, footnote_html, theme, title, word_count, preview_path)
    print(f"\n排版成品: {preview_path}")

    # 打开浏览器
    if AUTO_OPEN and not args.no_open:
        webbrowser.open(f"file://{preview_path}")
        print("已在浏览器中打开预览")

    print("\n完成! 在浏览器中点击「复制到微信」按钮，然后粘贴到公众号后台即可。")


if __name__ == "__main__":
    main()
