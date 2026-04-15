#!/usr/bin/env python3
"""清洗 format.py 生成 HTML 的两类代码块问题：

1. 语法高亮器 bug：每个代码块开头塞了 `class</span>=<span...>"language-xxx"</span>>`
2. 嵌套 <span style="color:..."> 在微信白名单里会被剥离，留下属性字符串作文本

做法：扁平化所有 <code>...</code> 内容 —— 剥掉所有 span、删掉垃圾属性串。
保留换行（<br>）和空格占位（&nbsp;）。
"""
import re


GARBAGE = re.compile(r'class</span>=<span[^>]*>"language-[a-z0-9_+-]+"</span>>')
SPAN_OPEN = re.compile(r"<span\b[^>]*>")
SPAN_CLOSE = re.compile(r"</span>")
CODE_BLOCK = re.compile(r"(<code\b[^>]*>)(.*?)</code>", re.DOTALL)

# format.py 的嵌套 span 崩坏后留下的残余字符串
#   "color:#6a9955">#6a9955">
#   "color:#ce9178">"language-rust"</span>>
#   ">"language-xxx"</span>>
STYLE_LEAK = re.compile(r'"?color:#[0-9a-fA-F]+"?>')
HEX_LEAK = re.compile(r'#[0-9a-fA-F]{6}"?>')
LANG_LEAK = re.compile(r'"?language-[a-z0-9_+-]+"?(?:</span>)?>')
CSS_KEY_LEAK = re.compile(r'"?background:[^">]*"?>|"?font-family:[^">]*"?>')


def clean(html: str) -> tuple[str, int]:
    html = GARBAGE.sub("", html)

    replaced = [0]

    def flatten(m: re.Match) -> str:
        opener = m.group(1)
        body = m.group(2)
        # 反复剥标签直到稳定（嵌套属性里还有标签）
        prev = None
        while prev != body:
            prev = body
            body = SPAN_OPEN.sub("", body)
            body = SPAN_CLOSE.sub("", body)
        # 剥掉标签后残留的属性字符串
        body = LANG_LEAK.sub("", body)
        body = STYLE_LEAK.sub("", body)
        body = HEX_LEAK.sub("", body)
        body = CSS_KEY_LEAK.sub("", body)
        # 处理残留的孤立引号 + > 组合
        body = re.sub(r'(?<![=&a-zA-Z0-9])"\s*>', '', body)
        if body != m.group(2):
            replaced[0] += 1
        return f"{opener}{body}</code>"

    html = CODE_BLOCK.sub(flatten, html)
    return html, replaced[0]


if __name__ == "__main__":
    import sys
    path = sys.argv[1]
    text = open(path).read()
    new, n = clean(text)
    open(path, "w").write(new)
    print(f"cleaned: {n} code blocks flattened")
