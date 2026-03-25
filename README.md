# xiaohu-wechat-format

A Claude Code skill that formats Markdown articles into WeChat Official Account (公众号) compatible HTML — with 30 themes, a visual gallery picker, and one-click publishing to drafts.

**[中文说明](README_CN.md)**

![Gallery Preview](docs/gallery-preview.png)

## What It Does

1. **Markdown → WeChat HTML**: Converts standard Markdown into inline-styled HTML that works in WeChat's editor (no `<style>` tags, no classes — everything inline)
2. **30 Themes**: From newspaper-style serif to neon cyberpunk, organized into 5 categories
3. **Visual Gallery**: Preview all themes with your actual article in a browser, then pick one
4. **AI Content Enhancement**: Auto-detects dialogue, key quotes, and image sequences — wraps them in styled containers
5. **One-Click Publish**: Uploads images to WeChat CDN and pushes the article to your drafts

![Bytedance Theme](docs/gallery-bytedance.png)

## Quick Start

```bash
# Install
cd ~/.claude/skills/
git clone https://github.com/xiaohuailabs/xiaohu-wechat-format.git
cp xiaohu-wechat-format/config.example.json xiaohu-wechat-format/config.json
pip3 install markdown requests

# Format an article (opens gallery in browser)
python3 scripts/format.py --input article.md --gallery

# Format with a specific theme
python3 scripts/format.py --input article.md --theme newspaper

# Publish to WeChat drafts
python3 scripts/publish.py --dir /tmp/wechat-format/article-name/ --cover cover.jpg
```

## Using with Claude Code

Just say:

```
排版这篇文章 /path/to/article.md
```

Claude will:
1. Read and analyze your article
2. Auto-enhance content (add dialogue containers, callout blocks, etc.)
3. Open the theme gallery in your browser
4. You pick a theme, tell Claude the name
5. Claude formats and optionally publishes to WeChat

## Themes (30)

### Standalone Styles (9)

| Theme | ID | Style |
|-------|-----|-------|
| Terracotta | `terracotta` | Warm orange, rounded headers |
| ByteDance | `bytedance` | Blue-teal gradient, modern tech |
| Chinese | `chinese` | Vermillion red, classical |
| Newspaper | `newspaper` | NYT-style serif, serious |
| GitHub | `github` | Developer-friendly, light code blocks |
| SSPAI | `sspai` | Chinese tech media red |
| Bauhaus | `bauhaus` | Primary colors, geometric |
| Ink | `ink` | Pure black, minimal whitespace |
| Midnight | `midnight` | Dark background, neon accents |

### Curated Styles (7)

| Theme | ID | Style |
|-------|-----|-------|
| Sports | `sports` | Gradient stripes, energetic |
| Mint | `mint-fresh` | Mint green, fresh |
| Sunset | `sunset-amber` | Warm amber tones |
| Lavender | `lavender-dream` | Purple dreamy |
| Coffee | `coffee-house` | Brown warm tones |
| WeChat Native | `wechat-native` | WeChat green |
| Magazine | `magazine` | Extra whitespace, editorial |

### Template Series (14)

4 layouts (Minimal / Focus / Elegant / Bold) × multiple color variants (Gold / Blue / Red / Green / Navy / Gray)

## Container Syntax

Enhance your Markdown with these containers before formatting:

```markdown
:::dialogue[Interview Title]
Alice: Hello there
Bob: Hi, how are you?
:::

:::gallery[Screenshots]
![](img1.jpg)
![](img2.jpg)
![](img3.jpg)
:::

> [!important] Key Insight
> This is the important takeaway

> [!tip] Pro Tip
> A useful tip for readers
```

## Configuration

Edit `config.json`:

```json
{
  "output_dir": "/tmp/wechat-format",
  "vault_root": "/path/to/your/obsidian/vault",
  "settings": {
    "default_theme": "newspaper",
    "auto_open_browser": true
  },
  "wechat": {
    "app_id": "YOUR_APP_ID",
    "app_secret": "YOUR_APP_SECRET"
  }
}
```

Get AppID/AppSecret from: WeChat Official Account Admin → Settings → Basic Configuration

**Important**: Add your public IP to the WeChat IP whitelist, otherwise API calls will fail with error 40164.

## How WeChat Compatibility Works

WeChat's editor strips `<style>` tags and CSS classes. This tool:

- Writes all styles as inline `style="..."` attributes on every element
- Converts `<ul>/<ol>` to `<section>` + flexbox (WeChat mangles native lists)
- Transforms external links `[text](url)` into footnotes (WeChat blocks external links)
- Processes `![[image.jpg]]` Obsidian wiki-links and standard `![](image)` references
- Renders callout blocks (`[!tip]`, `[!important]`, `[!warning]`) with distinct colors
- Converts dialogue blocks into chat-bubble layouts

## Requirements

- Python 3
- `markdown` library
- `requests` library (for publishing)

## License

MIT
