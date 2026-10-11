# -*- coding: utf-8 -*-
"""
隐藏内容分层 lint
==================

用于在提交前检查 wiki/ 条目是否符合《Wiki编写规范》第九章的分层要求：

  1. 每个隐藏容器都必须带 markdown="1"，且开闭标签成对；
  2. 隐藏容器**前面不得留下可见的标题或悬空引导句**——
     否则普通读者会看到一个只有标题、底下空空的节，
     等于变相提示"这里藏了东西"；
  3. 公开正文与 frontmatter 中不得出现调取方式的字样（避免把触发方式写进公开页面）。

用法：
  python scripts/wiki_backstage_lint.py          # 检查，有问题则退出码 1
  python scripts/wiki_backstage_lint.py --quiet  # 只输出问题

设计原则：只检查"是否会把隐藏痕迹暴露给普通读者"，不检查隐藏内容本身。
"""

import os
import re
import sys
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, "wiki")

OPEN_RE = re.compile(r'<div\s+class="srp-backstage(?P<cls>[^"]*)"(?P<attrs>[^>]*)>', re.IGNORECASE)
CLOSE_RE = re.compile(r"</div>", re.IGNORECASE)
FENCE_RE = re.compile(r"(?m)^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*$", re.DOTALL)

# 公开页面中不应出现的调取方式字样（大小写不敏感）
LEAK_PATTERNS = [
    r"\?backstage\s*=",
    r"#backstage\s*=",
    r"backstage\s*=\s*1",
]

# 悬空引导句的典型结尾（说明"下面内容被隐藏"但标题/句子留在外面）
DANGLING_TAIL = ("：", ":", "默认隐藏", "不予公开", "不作公开")


def check_file(path):
    problems = []
    raw = open(path, encoding="utf-8").read()
    body = FENCE_RE.sub("\n", raw)
    lines = body.split("\n")

    # 1) 开闭标签配平 + markdown="1"
    opens = list(OPEN_RE.finditer(body))
    closes = CLOSE_RE.findall(body)
    if len(closes) < len(opens):
        problems.append("开闭标签不配平：%d 个隐藏容器，仅 %d 个 </div>" % (len(opens), len(closes)))
    for m in opens:
        if 'markdown="1"' not in m.group("attrs"):
            problems.append("第 %d 行的隐藏容器缺少 markdown=\"1\"" % (body[: m.start()].count("\n") + 1))

    # 2) 容器前不得有可见标题 / 悬空引导句
    for i, ln in enumerate(lines):
        if not ln.strip().startswith('<div class="srp-backstage'):
            continue
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        prev = lines[j].strip() if j >= 0 else ""
        if prev.startswith("#"):
            problems.append("第 %d 行隐藏容器前有可见标题：%s" % (i + 1, prev))
        elif prev and prev.endswith(DANGLING_TAIL):
            problems.append("第 %d 行隐藏容器前有悬空引导句：…%s" % (i + 1, prev[-36:]))

    # 3) 不得把调取方式写进公开正文 / frontmatter
    for pat in LEAK_PATTERNS:
        for m in re.finditer(pat, raw, re.IGNORECASE):
            ln = raw[: m.start()].count("\n") + 1
            problems.append("第 %d 行出现调取方式字样：%s" % (ln, m.group(0)))

    return problems


def main():
    quiet = "--quiet" in sys.argv
    files = sorted(glob.glob(os.path.join(WIKI_DIR, "*.md")))
    total = 0
    touched = 0
    for path in files:
        problems = check_file(path)
        if problems:
            touched += 1
            total += len(problems)
            print("[!] %s" % os.path.basename(path))
            for p in problems:
                print("      - %s" % p)
    if not quiet:
        print("-" * 56)
        print("  扫描条目：%d" % len(files))
        print("  含隐藏容器的条目：%d" % sum(1 for f in files if 'class="srp-backstage' in open(f, encoding="utf-8").read()))
        print("  问题条目：%d，问题数：%d" % (touched, total))
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
