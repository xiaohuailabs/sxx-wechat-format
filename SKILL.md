# sxx-wechat-format

公众号一键排版技能。把 Obsidian 里的 Markdown 文章转成微信公众号兼容的排版 HTML，浏览器预览后一键复制粘贴到微信后台。

## Skill Description For Claude

把 Markdown 文章转为微信公众号兼容的内联样式 HTML。当用户说"排版""微信排版""格式化文章""format"时使用。

## Instructions

### 触发条件

用户说以下任何一种：
- `/format 文件路径`
- `排版这篇文章`
- `微信排版`
- `格式化为公众号格式`
- `把这篇转成微信格式`

### 完整工作流

#### 第 1 步：确认文章

1. 如果用户给了文件路径，直接读取
2. 如果没给路径，问用户要文章路径
3. 读取文章内容，确认标题和字数

#### 第 2 步：AI 预处理（在 Markdown 层面增强）

读取文章后，在调用脚本前，Claude 在 Markdown 层面做以下增强处理：

1. **识别重要引用** → 标记为 callout 格式 `> [!callout] 标题`
   - 适合标记的场景：核心观点、重要提醒、关键数据
   - 不要过度使用，一篇文章 1-3 处即可

2. **外部链接处理** → 无需处理（脚本自动转脚注）

3. **分隔符建议** → 在章节转换处确保有 `---` 分隔

4. **主题推荐**：
   - 深度长文、观点文 → `elegant`（金色，默认）
   - 技术教程、AI 资讯 → `tech`（蓝色）
   - 故事、生活类 → `warm`（橘色）
   - 热点评论、观点文 → `bold`（红色）
   - 知识分享、教程 → `minimal`（灰色）

5. 把增强后的 Markdown 保存为临时文件（或直接用原文件）

#### 第 3 步：调用格式化脚本

```bash
python3 /Users/apple/.claude/skills/sxx-wechat-format/scripts/format.py \
  --input "文章路径.md" \
  --theme elegant
```

参数说明：
- `--input` / `-i`：Markdown 文件路径（必须）
- `--theme` / `-t`：主题名（elegant/tech/warm/bold/minimal，默认 elegant）
- `--vault-root`：Obsidian Vault 根目录（默认从 config.json 读取）
- `--output` / `-o`：输出目录（默认 /tmp/wechat-format）
- `--no-open`：不自动打开浏览器

#### 第 4 步：确认结果

脚本执行后会：
1. 在 `/tmp/wechat-format/` 生成 `preview.html`（带预览壳）和 `wechat.html`（纯微信 HTML）
2. 自动在浏览器打开预览
3. 告诉用户点「复制到微信」按钮，粘贴到公众号后台

### 可用主题

| 主题 | 命令值 | 主色 | 强调色 | 适合内容 |
|------|--------|------|--------|---------|
| 赤陶 | terracotta | #3B3B38 | 赤陶橙 #D97757 | **默认** 知识分享、自媒体干货 |
| 优雅 | elegant | #333 | 金色 #c9a962 | 深度长文、观点文 |
| 科技 | tech | #2c3e50 | 蓝色 #0071e3 | 技术教程、AI 资讯 |
| 温暖 | warm | #3d3d3d | 橘色 #e07a5f | 故事、生活类 |
| 醒目 | bold | #1a1a1a | 红色 #ff4757 | 热点评论、观点文 |
| 极简 | minimal | #444 | 灰色 #888 | 知识分享、教程 |

### 微信兼容说明

脚本自动处理以下微信限制：
- **纯内联样式**：所有 CSS 直接写在每个标签的 `style="..."` 属性上
- **列表模拟**：`<ul>/<ol>` 改为 `<section>` + flexbox 模拟（微信会重构列表样式）
- **外链转脚注**：`[text](url)` 自动变成正文 `text[1]` + 文末脚注列表
- **图片处理**：`![[image.jpg]]` 自动搜索 Vault 并复制到输出目录

### 注意事项

- 依赖 Python `markdown` 库（系统已安装）
- 图片在预览中可见，但粘贴到微信后需要手动上传（微信不接受本地图片）
- 如果用户对排版不满意，可以切换主题重新生成
