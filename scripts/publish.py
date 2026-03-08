#!/usr/bin/env python3
"""微信公众号草稿箱发布工具

将 format.py 排版后的文章推送到微信公众号草稿箱。

用法:
    # 发布排版好的文章目录
    python3 publish.py --dir /path/to/formatted/article/

    # 指定封面图
    python3 publish.py --dir /path/to/formatted/article/ --cover cover.jpg

    # 直接从 Markdown 一步到位（自动排版+发布）
    python3 publish.py --input article.md --theme elegant
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

# ── 路径 ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent

with open(SKILL_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)


# ── 微信 API ─────────────────────────────────────────────────────────
def get_access_token():
    """获取微信 API access_token"""
    wechat = CONFIG.get("wechat", {})
    app_id = wechat.get("app_id")
    app_secret = wechat.get("app_secret")

    if not app_id or not app_secret:
        print("错误: config.json 中未配置 wechat.app_id 或 wechat.app_secret")
        sys.exit(1)

    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )
    resp = requests.get(url, timeout=15)
    data = resp.json()

    if "access_token" in data:
        print(f"  token 有效期: {data.get('expires_in', '?')} 秒")
        return data["access_token"]
    else:
        errcode = data.get("errcode", "?")
        errmsg = data.get("errmsg", "未知错误")
        print(f"错误: 获取 access_token 失败 (errcode={errcode}: {errmsg})")
        if errcode == 40164:
            print("  → IP 不在白名单中，请到公众号后台添加当前 IP")
        elif errcode in (40001, 40125):
            print("  → AppSecret 无效，请检查 config.json 中的 app_secret")
        sys.exit(1)


def upload_thumb_image(token, image_path):
    """上传封面图到永久素材库，返回 media_id"""
    url = (
        "https://api.weixin.qq.com/cgi-bin/material/add_material"
        f"?access_token={token}&type=image"
    )

    filename = os.path.basename(image_path)
    ext = Path(image_path).suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        files = {"media": (filename, f, content_type)}
        resp = requests.post(url, files=files, timeout=30)

    data = resp.json()
    if "media_id" in data:
        return data["media_id"]
    else:
        print(f"错误: 上传封面图失败 - {data}")
        return None


def upload_content_image(token, image_path):
    """上传正文图片（返回 CDN URL，用于替换 HTML 中的本地路径）"""
    url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"

    filename = os.path.basename(image_path)
    ext = Path(image_path).suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        files = {"media": (filename, f, content_type)}
        resp = requests.post(url, files=files, timeout=30)

    data = resp.json()
    if "url" in data:
        return data["url"]
    else:
        print(f"  错误: 上传正文图片失败 - {filename}: {data}")
        return None


def replace_local_images(html, article_dir, token):
    """替换 HTML 中的本地图片为微信 CDN URL"""
    image_dir = article_dir / "images"
    replaced = 0
    failed = 0

    def replace_src(match):
        nonlocal replaced, failed
        src = match.group(1)

        # 跳过已经是网络 URL 的图片
        if src.startswith("http://") or src.startswith("https://"):
            return match.group(0)

        # 构建本地路径
        local_path = article_dir / src
        if not local_path.exists() and image_dir.exists():
            local_path = image_dir / os.path.basename(src)

        if local_path.exists():
            cdn_url = upload_content_image(token, str(local_path))
            if cdn_url:
                replaced += 1
                print(f"  ✓ {os.path.basename(src)}")
                return f'src="{cdn_url}"'
            else:
                failed += 1
                return match.group(0)
        else:
            print(f"  ✗ 未找到: {src}")
            failed += 1
            return match.group(0)

    html = re.sub(r'src="([^"]+)"', replace_src, html)
    return html, replaced, failed


def push_draft(token, title, content, thumb_media_id, author="小互"):
    """推送文章到草稿箱"""
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"

    data = {
        "articles": [
            {
                "title": title,
                "author": author,
                "content": content,
                "content_source_url": "",
                "thumb_media_id": thumb_media_id,
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }
        ]
    }

    # 必须用 ensure_ascii=False，否则中文被转义为 \uXXXX 导致微信计算标题长度错误
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    resp = requests.post(url, data=body,
                         headers={"Content-Type": "application/json"}, timeout=30)
    result = resp.json()

    if "media_id" in result:
        return result["media_id"]
    else:
        errcode = result.get("errcode", "?")
        errmsg = result.get("errmsg", "未知错误")
        print(f"错误: 推送草稿箱失败 (errcode={errcode}: {errmsg})")
        return None


# ── 辅助函数 ──────────────────────────────────────────────────────────
def extract_title_from_html(html):
    """从 HTML 中提取 h1 标题"""
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()
    return None


def find_cover_image(article_dir, cover_arg=None):
    """找到封面图路径"""
    if cover_arg:
        p = Path(cover_arg)
        if p.exists():
            return p
        # 尝试在 article_dir 下找
        p = article_dir / cover_arg
        if p.exists():
            return p
        print(f"警告: 指定的封面图不存在: {cover_arg}")

    # 在 images/ 目录下找封面图
    image_dir = article_dir / "images"
    if image_dir.exists():
        # 优先找 cover- 开头的文件
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.gif"):
            covers = sorted(image_dir.glob(f"cover*{ext[1:]}"))
            if covers:
                return covers[0]
        # 没有 cover- 开头的，取第一张
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.gif"):
            covers = sorted(image_dir.glob(ext))
            if covers:
                return covers[0]

    return None


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="微信公众号草稿箱发布工具")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", "-d", help="format.py 的输出目录（含 article.html 和 images/）")
    group.add_argument("--input", "-i", help="Markdown 文件路径（自动调用 format.py 排版后发布）")
    parser.add_argument("--cover", "-c", help="封面图片路径")
    parser.add_argument("--title", "-t", help="文章标题（默认从 HTML 提取）")
    parser.add_argument("--theme", default=CONFIG["settings"]["default_theme"],
                        help="排版主题（仅 --input 模式有效）")
    parser.add_argument("--author", "-a",
                        default=CONFIG.get("wechat", {}).get("author", "小互"),
                        help="作者名")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做排版和图片上传，不推送草稿箱（用于测试）")
    args = parser.parse_args()

    # ── 1. 确定文章目录 ──────────────────────────────────────────────
    if args.input:
        # 先调用 format.py 排版
        input_path = Path(args.input).resolve()
        print(f"=== 第一步：排版 ===")
        format_cmd = [
            sys.executable, str(SCRIPT_DIR / "format.py"),
            "--input", str(input_path),
            "--theme", args.theme,
            "--no-open",
        ]
        result = subprocess.run(format_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"排版失败:\n{result.stderr}")
            sys.exit(1)
        print(result.stdout)

        # 从 format.py 输出中找到目录
        output_base = Path(CONFIG["output_dir"])
        file_stem = re.sub(r"-(公众号|小红书|微博)$", "", input_path.stem)
        article_dir = output_base / file_stem
    else:
        article_dir = Path(args.dir)

    if not article_dir.exists():
        print(f"错误: 目录不存在 - {article_dir}")
        sys.exit(1)

    # ── 2. 读取文章 HTML ─────────────────────────────────────────────
    print(f"\n=== {'第二步' if args.input else '第一步'}：准备发布 ===")
    article_path = article_dir / "article.html"

    if not article_path.exists():
        # 兼容旧版：从 preview.html 提取
        preview_path = article_dir / "preview.html"
        if preview_path.exists():
            print("未找到 article.html，从 preview.html 提取...")
            preview_content = preview_path.read_text(encoding="utf-8")
            match = re.search(
                r'<div id="wechatHtml">(.*?)</div>\s*<script>',
                preview_content, re.DOTALL
            )
            if match:
                html = match.group(1).strip()
            else:
                print("错误: 无法从 preview.html 提取文章内容")
                sys.exit(1)
        else:
            print(f"错误: 未找到 article.html 或 preview.html")
            sys.exit(1)
    else:
        html = article_path.read_text(encoding="utf-8")

    # ── 3. 提取标题 ──────────────────────────────────────────────────
    title = args.title or extract_title_from_html(html) or article_dir.name
    author = args.author
    print(f"标题: {title}")
    print(f"作者: {author}")

    # ── 4. 获取 token ────────────────────────────────────────────────
    print(f"\n获取 access_token...")
    token = get_access_token()
    print("✓ token 获取成功")

    # ── 5. 上传正文图片 ──────────────────────────────────────────────
    image_dir = article_dir / "images"
    if image_dir.exists() and list(image_dir.iterdir()):
        print(f"\n上传正文图片 ({len(list(image_dir.iterdir()))} 张)...")
        html, replaced, failed = replace_local_images(html, article_dir, token)
        print(f"  上传完成: {replaced} 成功, {failed} 失败")
        if failed > 0:
            print("  警告: 部分图片上传失败，文章中对应位置可能显示空白")
    else:
        print("\n无正文图片需上传")

    # ── 6. 上传封面图 ────────────────────────────────────────────────
    cover_path = find_cover_image(article_dir, args.cover)
    if cover_path:
        print(f"\n上传封面图: {cover_path.name}")
        thumb_media_id = upload_thumb_image(token, str(cover_path))
        if thumb_media_id:
            print(f"  ✓ media_id: {thumb_media_id[:20]}...")
        else:
            print("  ✗ 封面上传失败")
            thumb_media_id = None
    else:
        print("\n未找到封面图")
        thumb_media_id = None

    if not thumb_media_id:
        print("\n错误: 微信要求必须有封面图。")
        print("  请用 --cover 指定封面图路径，或在 images/ 目录放一张图片")
        sys.exit(1)

    # ── 7. 推送草稿箱 ────────────────────────────────────────────────
    if args.dry_run:
        print(f"\n[dry-run] 跳过推送草稿箱")
        print(f"  标题: {title}")
        print(f"  封面 media_id: {thumb_media_id}")
        print(f"  HTML 长度: {len(html)} 字符")
        return

    print(f"\n推送到草稿箱...")
    media_id = push_draft(token, title, html, thumb_media_id, author)

    if media_id:
        print(f"\n{'='*40}")
        print(f"  发布成功!")
        print(f"  草稿 media_id: {media_id}")
        print(f"  → 请到微信公众号后台 → 草稿箱 查看和发布")
        print(f"{'='*40}")
    else:
        print(f"\n发布失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
