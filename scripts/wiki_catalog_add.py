# -*- coding: utf-8 -*-
"""
Wiki 目录收录工具
==================

功能：
  1. 扫描 wiki/ 目录下所有 .md 词条文件，从 YAML frontmatter 提取 title 作为 display
  2. 与 wiki/wiki_catalog.json 中已收录的 slug 集合比对，找出尚未收录的新词条
  3. 交互式让添加者选择类别（数字选择 / 跳过此项 / 新建类别），把新词条追加到对应类别
  4. 写回 wiki_catalog.json（保留原有结构、缩进 2 空格、ensure_ascii=False）

用法（在项目根目录执行）：
  python scripts/wiki_catalog_add.py
"""

import os
import re
import json
import sys

# 脚本位于根目录 scripts/ 下，wiki 目录在 scripts 的上一级
# 不依赖 CWD，按脚本自身路径解析
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, 'wiki')
CATALOG_JSON = os.path.join(WIKI_DIR, 'wiki_catalog.json')

# 排除的非词条文件（slug，不含 .md 后缀）：编写规范与主页都不是词条
EXCLUDE_SLUGS = {'wikiRule', 'wiki_intro'}


def extract_title(content):
    """从 Markdown 文件的 YAML frontmatter 中提取 title 字段值。

    处理 customExtraContent: | 这类多行块（其后缩进行属于该块），避免误取块内文本。
    返回 title 字符串；若无 frontmatter 或无 title 字段，返回 None。
    """
    if not content.startswith('---'):
        return None
    lines = content.split('\n')
    # 找到闭合的 --- 行
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return None
    fm_lines = lines[1:end]

    # 极简 YAML 解析：只关心单行键值，跳过 | / > 多行块
    multi_key = None
    for raw in fm_lines:
        line = raw.rstrip()
        if multi_key is not None:
            # 多行块内：缩进行属于该块；
            # 遇到「非缩进 + 含冒号」的行视为新键，结束块并继续处理本行
            if line and not line.startswith(' ') and not line.startswith('\t') and ':' in line:
                multi_key = None
            else:
                continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # 值以 | 或 > 开头（可能带行内注释，如 "| # 说明"）表示多行块
        if val.startswith('|') or val.startswith('>'):
            multi_key = key
            continue
        if key == 'title':
            # 去掉首尾配对引号
            if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                val = val[1:-1]
            return val
    return None


def load_catalog(catalog_path):
    """读取 wiki_catalog.json。

    返回 (data, existing_slugs)：
      data           原始结构（含 categories 列表，写回时基于此修改）
      existing_slugs 已收录 slug 集合
    """
    with open(catalog_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    existing_slugs = set()
    for cat in data.get('categories', []):
        for e in cat.get('entries', []):
            existing_slugs.add(e.get('slug', ''))
    return data, existing_slugs


def scan_wiki_files():
    """扫描 wiki/ 下所有 .md 文件，返回 [(slug, display, has_title), ...]。

    display 取自 frontmatter 的 title；无 title 则用 slug 作为 display 并标记 has_title=False。
    排除 EXCLUDE_SLUGS 中的文件。结果按文件名排序，稳定可重现。
    """
    result = []
    if not os.path.isdir(WIKI_DIR):
        return result
    md_files = sorted(f for f in os.listdir(WIKI_DIR) if f.endswith('.md'))
    for fname in md_files:
        slug = re.sub(r'\.md$', '', fname)
        if slug in EXCLUDE_SLUGS:
            continue
        path = os.path.join(WIKI_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue
        title = extract_title(content)
        if title:
            result.append((slug, title, True))
        else:
            # 无 title，用文件名（slug）作为 display 并标记
            result.append((slug, slug, False))
    return result


def print_categories(categories):
    """打印当前所有类别（带数字序号，0 起），并附加「跳过此项」与「新建类别」选项。"""
    print('\n当前类别：')
    for idx, cat in enumerate(categories):
        name = cat.get('name', '(未命名)')
        cnt = len(cat.get('entries', []))
        print(f'  {idx}. {name}  (现有 {cnt} 条)')
    print('  s. 跳过此项')
    print('  n. 新建类别')


def choose_category(categories):
    """让用户为一个新词条选择类别。

    返回 (action, value)：
      ('append', 类别索引 int)  追加到已有类别
      ('skip',   None)           跳过此项
      ('new',    新类别名 str)    新建类别并追加
    对非法输入容错重新提示，不崩溃。
    """
    n = len(categories)
    while True:
        print_categories(categories)
        choice = input('请输入选项（数字 / s / n）: ').strip().lower()
        if choice in ('s', 'skip', '跳过'):
            return ('skip', None)
        if choice in ('n', 'new', '新建'):
            name = input('请输入新类别名称: ').strip()
            if not name:
                print('类别名不能为空，请重新选择。')
                continue
            return ('new', name)
        # 数字选择
        if choice.isdigit():
            num = int(choice)
            if 0 <= num < n:
                return ('append', num)
            print(f'数字超出范围（0 - {n - 1}），请重新输入。')
        else:
            print('无法识别的输入，请重新输入。')


def make_category_id(name):
    """按现有约定生成类别 id：cat_ + 名称中非字母数字汉字字符替换为 _。"""
    return 'cat_' + re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5]+', '_', name).strip('_')


def main():
    # 1. 读取 catalog（用 setdefault 确保 categories 列表挂在 data 上，后续修改可写回）
    if not os.path.isfile(CATALOG_JSON):
        print(f'[错误] 找不到 catalog 文件: {CATALOG_JSON}')
        sys.exit(1)
    data, existing_slugs = load_catalog(CATALOG_JSON)
    categories = data.setdefault('categories', [])

    # 2. 扫描 wiki 文件
    files = scan_wiki_files()

    # 3. 找出新词条：在 wiki/ 但不在 catalog 中的
    new_entries = [(slug, display, has_title)
                   for slug, display, has_title in files
                   if slug not in existing_slugs]

    if not new_entries:
        print('没有新词条需要录入。')
        return

    print(f'\n发现 {len(new_entries)} 个新词条：')
    for i, (slug, display, has_title) in enumerate(new_entries):
        tag = '' if has_title else '  [无title，用文件名]'
        print(f'  {i + 1}. {slug}.md  ->  display="{display}"{tag}')

    # 4. 逐个处理
    pending = []  # 记录将录入的 (slug, display, 类别名) 供预览
    for slug, display, has_title in new_entries:
        print(f'\n--- 处理: {slug}.md (display="{display}") ---')
        if not has_title:
            print('  [提示] 该文件未找到 frontmatter title 字段，暂以文件名作为 display，请确认。')
        action, value = choose_category(categories)
        if action == 'skip':
            print(f'  已跳过 {slug}')
            continue
        if action == 'new':
            # 新建类别：追加到 categories 末尾，order 取当前长度（即新索引）
            new_cat = {
                'id': make_category_id(value),
                'name': value,
                'order': len(categories),
                'entries': [],
            }
            categories.append(new_cat)
            cat_idx = len(categories) - 1
            print(f'  已新建类别「{value}」(序号 {cat_idx})')
        else:  # append
            cat_idx = value
        # 追加到对应 category 的 entries 数组末尾
        categories[cat_idx].setdefault('entries', []).append({'slug': slug, 'display': display})
        existing_slugs.add(slug)
        cat_name = categories[cat_idx].get('name', '')
        print(f'  -> 录入到类别「{cat_name}」')
        pending.append((slug, display, cat_name))

    if not pending:
        print('\n没有词条被录入（全部跳过），不修改 catalog。')
        return

    # 5. 预览
    print('\n========== 预览：将录入以下内容 ==========')
    for slug, display, cat_name in pending:
        print(f'  [{cat_name}] {slug}  ->  display="{display}"')
    print('==========================================')

    # 6. 确认写入
    confirm = input('\n确认写入 wiki_catalog.json？(y 写入 / 其他取消): ').strip().lower()
    if confirm != 'y':
        print('已取消，未写入任何修改。')
        return

    # 7. 写回（保留原有结构、缩进 2 空格、ensure_ascii=False）
    #    原文件使用 CRLF 行尾且无末尾换行；序列化后统一替换为 CRLF 以保持一致、避免大面积 diff。
    #    （JSON 字符串值里的真实换行会被转义成字面 \n，因此此处替换 \n 不会破坏字符串内容。）
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace('\n', '\r\n')
    with open(CATALOG_JSON, 'wb') as f:
        f.write(payload.encode('utf-8'))
    print(f'[OK] 已写入: {CATALOG_JSON}')
    print(f'     本次录入 {len(pending)} 条')


if __name__ == '__main__':
    main()
