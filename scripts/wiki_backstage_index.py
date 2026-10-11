# -*- coding: utf-8 -*-
"""
Wiki backstage 全文索引生成器
==============================

功能：
  扫描 wiki/ 目录下所有 .md 条目，抽取 <div class="srp-backstage"> ... </div>
  中的内容，转成纯文本，生成：
    1. wiki/wiki_backstage_index.json          —— 主页在 ?backstage=1 时按需 fetch
    2. wiki/wiki_backstage_index_snapshot.js   —— fetch 失败时的兜底数据源

用途：
  让"内部可搜"成为可能：默认视图的站内搜索只检索标题/别名/摘要，
  不会把隐藏内容暴露给普通读者；只有在 ?backstage=1 时，
  主页才会加载本索引，把后台文本并入搜索域。

注意：
  本索引是公开可访问的静态文件，因此 **不是权限控制**，
  仅用于实现"默认视图不检索后台内容"的呈现分层。

用法：
  python scripts/wiki_backstage_index.py          # 生成 JSON + JS 快照
  python scripts/wiki_backstage_index.py --dry    # 只打印统计
"""

import os
import re
import json
import sys
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, "wiki")
OUT_JSON = os.path.join(WIKI_DIR, "wiki_backstage_index.json")
OUT_JS = os.path.join(WIKI_DIR, "wiki_backstage_index_snapshot.js")

BLOCK_RE = re.compile(
    r'<div\s+class="srp-backstage(?P<cls>[^"]*)"(?P<attrs>[^>]*)>(?P<body>.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
# 围栏代码块（```...```）：其中的示例不算真正的后台块，扫描前先剔除
FENCE_RE = re.compile(r"(?m)^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*$", re.DOTALL)
LAYER_RE = re.compile(r'data-layer="([^"]*)"', re.IGNORECASE)
TITLE_RE = re.compile(r'data-title="([^"]*)"', re.IGNORECASE)


def to_plain_text(md_text):
    """把后台块内的 markdown / html 转成用于检索的纯文本。"""
    text = md_text
    # 去掉 html 标签，但保留内容
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|h[1-6]|blockquote)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # 图片
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    # 链接保留显示文本
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # 表格分隔行
    text = re.sub(r"^\s*\|?[\s:|-]+\|?\s*$", " ", text, flags=re.MULTILINE)
    # 表格竖线、引用符、列表符、标题符
    text = text.replace("|", " ")
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}([-*+]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    # 强调符
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?<!\w)[*_](?=\S)", "", text)
    text = text.replace("*", "").replace("`", "")
    # 压缩空白
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def extract_blocks(content):
    blocks = []
    for m in BLOCK_RE.finditer(content):
        body = m.group("body")
        attrs = m.group("attrs") or ""
        layer_m = LAYER_RE.search(attrs)
        title_m = TITLE_RE.search(attrs)
        blocks.append(
            {
                "layer": (layer_m.group(1).strip() if layer_m else ""),
                "title": (title_m.group(1).strip() if title_m else ""),
                "text": to_plain_text(body),
            }
        )
    return blocks


def build():
    entries = {}
    total_blocks = 0
    for fname in sorted(os.listdir(WIKI_DIR)):
        if not fname.endswith(".md"):
            continue
        slug = fname[:-3]
        path = os.path.join(WIKI_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        blocks = extract_blocks(FENCE_RE.sub("\n", content))
        if not blocks:
            continue
        layers = []
        for b in blocks:
            if b["layer"] and b["layer"] not in layers:
                layers.append(b["layer"])
        entries[slug] = {
            "blocks": len(blocks),
            "layers": layers,
            # 层与层之间用分隔符隔开，避免跨块产生虚假的相邻词
            "text": "\u0001".join(b["text"] for b in blocks if b["text"]),
        }
        total_blocks += len(blocks)

    return {
        "generated_at_hint": "请运行 scripts/wiki_backstage_index.py 重新生成此文件",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "本文件仅用于 ?backstage=1 时的站内检索，不是权限控制。",
        "entry_count": len(entries),
        "block_count": total_blocks,
        "entries": entries,
    }


def main():
    data = build()
    if "--dry" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
        print(
            "\n[INFO] 含后台层条目 %d 个，后台块 %d 个"
            % (data["entry_count"], data["block_count"])
        )
        return

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[OK] 已生成: %s" % OUT_JSON)

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    size = len(payload.encode("utf-8"))
    js = (
        "// Wiki backstage 索引快照（自动生成，勿手改。运行 scripts/wiki_backstage_index.py 重新生成）\n"
        "// 仅在页面以 ?backstage=1 打开时由主页加载，作为 fetch(json) 失败时的兜底。\n"
        "(function () {\n"
        "  try {\n"
        "    window.WIKI_BACKSTAGE_SNAPSHOT = " + payload + ";\n"
        "  } catch (e) {\n"
        '    console.error("[wiki] backstage snapshot failed to parse", e);\n'
        "  }\n"
        "})();\n"
        "// meta: entries=" + str(data["entry_count"]) + " blocks=" + str(data["block_count"]) +
        " size=" + str(size) + "B\n"
    )
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(js)
    print("[OK] 已生成: %s" % OUT_JS)
    print(
        "     含后台层条目 %d 个 / 后台块 %d 个 / %dB"
        % (data["entry_count"], data["block_count"], size)
    )


if __name__ == "__main__":
    main()
