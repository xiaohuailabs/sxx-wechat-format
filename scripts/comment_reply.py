#!/usr/bin/env python3
"""
微信公众号评论自动回复脚本

功能：
- 扫描已发布文章的新评论
- 用 AI 生成符合小互风格的回复
- 自动发送回复
- 记录已回复的评论，避免重复

用法：
  python3 comment_reply.py                # 扫描并回复
  python3 comment_reply.py --dry-run      # 只看不发
  python3 comment_reply.py --articles 5   # 扫描最近 5 篇（默认 10）
"""

import argparse
import json
import os
import re
import time
import requests
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR.parent / "config.json"
STATE_PATH = SCRIPT_DIR.parent / "comment_state.json"
LOG_PATH = SCRIPT_DIR.parent / "comment_reply.log"

# AI 回复的系统提示
SYSTEM_PROMPT = """你是公众号"小互"的作者，回复读者评论。

## 两种模式

**模式A：文章内容相关的问题**
如果读者在问文章里提到的具体内容、方法、工具、概念，要认真回答：
- 基于文章内容给出详细准确的解答，10-40字
- 像朋友给你讲明白一样，不要敷衍
- 可以引用文章里的具体信息
- 如果文章内容确实没提到，老实说"文章里没写这个"

**模式B：其他评论（闲聊、夸奖、吐槽、杠精、技术求助等）**
极简 + 偶尔幽默：
- 大部分 2-8 个字
- 不用 emoji，不说"哈哈"，不加"哦""呢""啦""~"
- 不客套、不说"感谢""谢谢支持"
- 技术求助只给方向："问龙虾""检查密钥"
- 杠精直接怼，但要对题："你说的对 但没用""想多了"
- 偶尔来点冷幽默或调侃，但不要每条都幽默
- 观点类评论可以多说几句，给出自己的看法，但每次措辞要不同

## 公开发言分寸感（最重要）
你是公众号作者，每条回复都是公开的，要注意：
- 不点名批评具体公司或个人（不说"阿里管理有问题""百度不行"）
- 不对公司内部决策下定论（不说"商业化没做好""裁员潮开始了"）
- 不预测具体公司/个人的未来（不说"大概率被挖""迟早完"）
- 涉及敏感话题（裁员、公司内斗、行业竞争）用模糊表达："看后续""不好说""各有各的路"
- 可以表达感受但不下结论："有点可惜""挺有意思""这事说不准"

## 严格禁止
- 禁止连续多条用相同的回复（比如连续回"好的 嘿嘿"或连续回"不能"）
- "不能"只能用于回答"能不能/行不行"类的是非问题，不能当万能回复
- "好的 嘿嘿"偶尔用可以，但不能成为默认回复
- 对于看不懂或不确定的评论，用"没看懂"或"这个不好说"，而不是乱回"不能"

## 回复多样性
夸奖类可以用：搞定就好 / 好用就行 / 有用就好 / 管用吧 / 挺好
吐槽类可以用：确实 / 是这样 / 没毛病 / 你说的对 / 懂的都懂
闲聊类可以用：哈 / 可以 / 对 / 嗯 / 差不多

## 真实回复范例
- "问龙虾"（技术求助）
- "不能"（只用于能不能的问题）
- "确实有相似之处"（赞同类）
- "全自动化"（简短信息）
- "搞定就好"（用户反馈成功）
- "绑个信用卡就行了"（具体建议）
- "让龙虾检查"（技术方向）
- "发个图看看"（需要更多信息）
- "不行 他们封杀了龙虾"（带情绪的回答）
- "额 Linux 里更安全"（轻松的技术建议）
- "时代在变 适应就好"（观点回应）
- "这波不好说"（不确定的事）

只输出回复内容，不加引号、前缀或解释。"""


def log(msg):
    """写日志"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"replied": {}}  # {mid_commentid: timestamp}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_access_token(wechat_config):
    resp = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential",
        "appid": wechat_config["app_id"],
        "secret": wechat_config["app_secret"]
    }).json()
    if "access_token" not in resp:
        raise Exception(f"Token 获取失败: {resp}")
    return resp["access_token"]


def get_published_articles(token, count=10):
    """获取已发布文章列表"""
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/freepublish/batchget?access_token={token}",
        json={"offset": 0, "count": count}
    ).json()

    articles = []
    for item in resp.get("item", []):
        for idx, news in enumerate(item.get("content", {}).get("news_item", [])):
            url = news.get("url", "")
            mid_match = re.search(r"mid=(\d+)", url)
            if mid_match and news.get("need_open_comment", 0) == 1:
                # 提取文章摘要，供 AI 回复文章相关问题
                digest = news.get("digest", "")
                # 如果有完整内容，提取纯文本摘要（前500字）
                content_html = news.get("content", "")
                if content_html:
                    import re as _re
                    text = _re.sub(r"<[^>]+>", "", content_html)
                    text = _re.sub(r"\s+", " ", text).strip()
                    digest = text[:500]
                articles.append({
                    "mid": int(mid_match.group(1)),
                    "index": idx,
                    "title": news.get("title", "无标题"),
                    "digest": digest,
                })
    return articles


def get_comments(token, mid, index=0):
    """获取文章评论"""
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/comment/list?access_token={token}",
        data=json.dumps({
            "msg_data_id": mid,
            "index": index,
            "begin": 0,
            "count": 50,
            "type": 0
        })
    ).json()

    if resp.get("errcode", 0) != 0:
        return []
    return resp.get("comment", [])


def find_unreplied(comments, mid, state):
    """找出未回复的精选评论"""
    unreplied = []
    for c in comments:
        cid = c.get("user_comment_id", "")
        state_key = f"{mid}_{cid}"

        # 跳过已回复的（微信已回复 或 我们已处理过的）
        if c.get("reply", {}).get("content"):
            continue
        if state_key in state.get("replied", {}):
            continue
        # 只回复精选评论（comment_type == 1 表示精选）
        # 实测发现精选评论的 comment_type 不一定可靠，
        # 用 is_elected 字段或直接回复所有未回复的

        unreplied.append({
            "comment_id": cid,
            "content": c.get("content", ""),
            "state_key": state_key,
        })
    return unreplied


def generate_reply(comment_content, article_title, ai_config, article_digest=""):
    """用 AI 生成回复"""
    try:
        user_msg = f"文章标题：{article_title}\n"
        if article_digest:
            user_msg += f"文章内容摘要：{article_digest}\n"
        user_msg += f"\n读者评论：{comment_content}\n\n请生成回复："

        resp = requests.post(
            f"{ai_config['url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {ai_config['key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": ai_config.get("model", "google/gemini-2.5-flash"),
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}
                ],
                "max_tokens": 150,
                "temperature": 0.7,
            },
            timeout=30,
        ).json()

        reply = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        # 清理可能的引号包裹
        if reply.startswith('"') and reply.endswith('"'):
            reply = reply[1:-1]
        if reply.startswith("'") and reply.endswith("'"):
            reply = reply[1:-1]
        return reply
    except Exception as e:
        log(f"  AI 生成失败: {e}")
        return None


def send_reply(token, mid, index, comment_id, reply_content):
    """发送评论回复"""
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/comment/reply/add?access_token={token}",
        data=json.dumps({
            "msg_data_id": mid,
            "index": index,
            "user_comment_id": comment_id,
            "content": reply_content,
        }, ensure_ascii=False).encode("utf-8")
    ).json()
    return resp.get("errcode", -1) == 0, resp


def main():
    parser = argparse.ArgumentParser(description="微信公众号评论自动回复")
    parser.add_argument("--dry-run", action="store_true", help="只生成回复，不发送")
    parser.add_argument("--articles", type=int, default=10, help="扫描最近几篇文章（默认 10）")
    args = parser.parse_args()

    log("=" * 40)
    log("评论自动回复启动")

    config = load_config()
    state = load_state()

    # AI 配置（复用 twittersave 的 OpenRouter）
    tw_config = json.load(open(
        Path.home() / ".claude/skills/xiaohu-twittersave/config.json", "r"
    ))
    ai_config = {
        "url": tw_config.get("settings", {}).get("twai_url", "https://openrouter.ai/api/v1"),
        "key": tw_config.get("secrets", {}).get("twai_api_key", ""),
        "model": "anthropic/claude-sonnet-4",
    }

    # 获取 token
    try:
        token = get_access_token(config["wechat"])
        log("Token OK")
    except Exception as e:
        log(f"Token 失败: {e}")
        return

    # 获取文章
    articles = get_published_articles(token, count=args.articles)
    log(f"扫描 {len(articles)} 篇文章")

    total_replied = 0
    total_skipped = 0

    for article in articles:
        mid = article["mid"]
        title = article["title"]

        comments = get_comments(token, mid, article["index"])
        unreplied = find_unreplied(comments, mid, state)

        if not unreplied:
            continue

        log(f"\n📝 {title} ({len(unreplied)} 条待回复)")

        for item in unreplied:
            content = item["content"][:100]
            log(f"  💬 [{item['comment_id']}] {content}")

            # 生成回复
            reply = generate_reply(item["content"], title, ai_config, article_digest=article.get("digest", ""))
            if not reply:
                log(f"  ⚠️ 跳过（AI 生成失败）")
                total_skipped += 1
                continue

            log(f"  ↳ 回复: {reply}")

            if args.dry_run:
                log(f"  [dry-run] 未发送")
                continue

            # 发送回复
            ok, resp = send_reply(token, mid, article["index"], item["comment_id"], reply)
            if ok:
                log(f"  ✅ 发送成功")
                state["replied"][item["state_key"]] = datetime.now().isoformat()
                save_state(state)
                total_replied += 1
            else:
                log(f"  ❌ 发送失败: {resp}")
                total_skipped += 1

            # 避免请求太快
            time.sleep(1)

    log(f"\n完成：回复 {total_replied} 条，跳过 {total_skipped} 条")
    log("=" * 40)


if __name__ == "__main__":
    main()
