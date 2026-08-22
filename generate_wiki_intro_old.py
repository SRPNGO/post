#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os

def generate_wiki_intro():
    # 读取目录下的 wiki_catalog.json
    with open('./wiki/wiki_catalog.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    categories = data.get('categories', [])
    # 按 order 排序（若 JSON 中已有序，此步仍可确保顺序）
    categories_sorted = sorted(categories, key=lambda c: c.get('order', 0))

    # 构建 Markdown 内容
    lines = []

    # 固定头部
    lines.append('---')
    lines.append('title: 星球阁Wiki主界面')
    lines.append('author: Nsakrty')
    lines.append('---')
    lines.append('')  # 空行分隔

    # 欢迎语
    lines.append('欢迎！这是星球阁的Wiki主界面。这个页面包含了星球阁的所有Wiki页面以便于读者迅速查阅。你可以在页面列表中找到你需要的信息。[新测试主页](./wiki_intro_new.html)。')
    lines.append('')  # 空行

    # 遍历每个分类
    for cat in categories_sorted:
        cat_name = cat.get('name', '未命名分类')
        entries = cat.get('entries', [])

        lines.append(f"### {cat_name}")
        for entry in entries:
            display = entry.get('display', '未命名')
            slug = entry.get('slug', '')
            lines.append(f"- [{display}](./{slug})")
        lines.append('')  # 每个分类后空一行

    # 固定脚注
    lines.append('注：以上Wiki页面由AI经《星球阁存档计划》资料整理而生成，并经过Nsakrty的审核和修改。内容仅供参考，不代表官方立场，更多详情请参考星球阁官网([srpn.top](https://srpn.top))')

    # 确保目标目录存在
    os.makedirs('./wiki', exist_ok=True)

    # 写入文件
    with open('./wiki/wiki_intro.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print("✅ wiki_intro.md 生成成功！")

if __name__ == '__main__':
    generate_wiki_intro()