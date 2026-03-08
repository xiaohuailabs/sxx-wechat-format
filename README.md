# sxx-wechat-format

Claude Code 公众号一键排版+发布技能。Markdown → 微信兼容 HTML → 推送草稿箱，一句话搞定。

## 功能

- **排版引擎**：Markdown 转微信公众号兼容的内联样式 HTML
- **14 套主题**：赤陶、优雅、科技、温暖、醒目、极简、杂志、教程、暗色、复古、故事、新闻、观点、渐变
- **图片处理**：自动处理 Obsidian `![[image]]` 和标准 Markdown `![](image)` 引用
- **外链转脚注**：微信不支持外链，自动转文末脚注
- **一键发布**：自动上传图片到微信 CDN + 推送到草稿箱
- **浏览器预览**：排版后自动打开预览页面，带「复制到微信」按钮

## 安装

```bash
cd ~/.claude/skills/
git clone https://github.com/xiaohuailabs/sxx-wechat-format.git
pip3 install markdown requests
```

## 配置

编辑 `config.json`，填入你的公众号凭证：

```json
{
  "output_dir": "/tmp/wechat-format",
  "vault_root": "/path/to/your/obsidian/vault",
  "settings": {
    "default_theme": "terracotta",
    "auto_open_browser": true
  },
  "wechat": {
    "app_id": "你的AppID",
    "app_secret": "你的AppSecret",
    "author": "你的笔名"
  }
}
```

获取 AppID 和 AppSecret：微信公众号后台 → 设置与开发 → 基本配置

**重要**：需要把你的公网 IP 加到公众号后台的 IP 白名单里，否则 API 调用会报 40164 错误。

## 使用

在 Claude Code 里直接说：

```
排版这篇文章 /path/to/article.md
```

```
排版这篇文章并推送到草稿箱，封面图用 /path/to/cover.jpg
```

### 命令行直接调用

**排版**：
```bash
python3 scripts/format.py --input article.md --theme tech
```

**发布**：
```bash
python3 scripts/publish.py --dir /tmp/wechat-format/article-name/ --cover cover.jpg
```

**一步到位**：
```bash
python3 scripts/publish.py --input article.md --cover cover.jpg --theme tech
```

## 主题一览

| 主题 | 命令值 | 适合内容 |
|------|--------|---------|
| 赤陶 | terracotta | **默认** 知识分享、干货 |
| 优雅 | elegant | 深度长文、观点文 |
| 科技 | tech | 技术教程、AI 资讯 |
| 温暖 | warm | 故事、生活类 |
| 醒目 | bold | 热点评论 |
| 极简 | minimal | 知识分享 |
| 杂志 | magazine | 专题报道 |
| 教程 | tutorial | 手把手教程 |
| 暗色 | dark | 技术文章 |
| 复古 | retro | 文化、历史 |
| 故事 | story | 人物故事 |
| 新闻 | news | 新闻快讯 |
| 观点 | opinion | 评论文章 |
| 渐变 | gradient | 创意内容 |

## 自定义主题

在 `themes/` 目录下创建 JSON 文件即可。参考 `themes/terracotta.json`。

## 依赖

- Python 3
- `markdown` 库
- `requests` 库

## License

MIT
