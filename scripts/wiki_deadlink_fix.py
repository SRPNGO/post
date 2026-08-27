# -*- coding: utf-8 -*-
"""
Wiki 断链 / 死链 · 交互式修复操作台
=====================================

功能：
  1. 扫描 wiki/ 下所有 .md 条目，检测 Wiki 内链（/wiki/slug 或 ./slug）的目标是否真实存在
  2. 以「操作台」形式**逐条展示**每个断链的：所在文件 / 行号 / 显示文本 / 原 URL / 上下文 / 同类数量
  3. 为每个断链提供 6 种处置方式：
        1  搜索候选 slug   → 在存在的条目中按关键词模糊搜索（默认用显示文本当关键词），选编号即修复
        2  输入正确 slug   → 手敲正确文件名（不含 .md）
        3  改为外部链接    → 替换为完整 https://... URL
        4  解除链接（留文本）→ 把 [text](坏链) → 只剩 text
        5  跳过本条目
        6  批量处理全部同类 → 对 URL 完全相同的所有断链一次性操作
        l  全局列表        → 一览所有断链
        q  保存并退出

用法：
  python scripts/wiki_deadlink_fix.py           # 操作台模式（交互式修复，自动写文件）
  python scripts/wiki_deadlink_fix.py --dry     # 只读模式，只列出所有断链，不允许操作不写文件
                                                 （供 workflow --dry 集成时使用）
"""

import os
import re
import sys
import json
import glob

# ========= 路径解析 =========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, 'wiki')
CATALOG_JSON = os.path.join(WIKI_DIR, 'wiki_catalog.json')

EXCLUDE_FILES = {'wikiRule.md'}   # 非条目文件

# 与 wiki_normalize_links.py 保持一致的行内链接正则（排除图片、URL 不含 )）
_LINK_RE = re.compile(
    r'(?<!\!)\[([^\]]*)\]\(([^\s)]+)(?:\s+("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'))?\)'
)


# ============================================================
# 数据加载
# ============================================================

def _slug_target_exists(slug):
    """判断一个 slug 的目标是否真实存在（wiki/ 下 .md 或 .html 任一存在即可）。

    例如 wiki_intro 没有 wiki_intro.md，但有 wiki_intro.html，一样算合法目标，
    不应被判定为断链。
    """
    if not slug:
        return False
    md = os.path.join(WIKI_DIR, f'{slug}.md')
    html = os.path.join(WIKI_DIR, f'{slug}.html')
    return os.path.isfile(md) or os.path.isfile(html)


def load_slugs_pages():
    """加载 {slug: info} 映射，包括：
      * wiki/*.md       → 真实条目（主目标）
      * wiki/*.html     → 纯 HTML 页面（例如 wiki_intro.html），同样是合法目标

    info 结构：
      {'title': str, 'display': str, 'category': str,
       'kind': 'md' | 'html'}    # kind 用于 UI 上一眼区分
    """
    slug_info = {}  # slug -> info dict

    # ---- (A) 先收集 .md 条目 ----
    for fname in sorted(os.listdir(WIKI_DIR)):
        if not fname.endswith('.md'):
            continue
        slug = fname[:-3]
        title = slug
        display = slug
        try:
            with open(os.path.join(WIKI_DIR, fname), 'r', encoding='utf-8') as f:
                content = f.read()
            if content.startswith('---'):
                lines = content.split('\n')
                for i in range(1, len(lines)):
                    if lines[i].strip() == '---':
                        break
                    m = re.match(r'^title\s*:\s*(.*)$', lines[i])
                    if m:
                        v = m.group(1).strip()
                        if v.startswith('"') and v.endswith('"'):
                            v = v[1:-1]
                        if v:
                            title = v
        except Exception:
            pass
        slug_info[slug] = {
            'title': title,
            'display': display,
            'category': '',
            'kind': 'md',
        }

    # ---- (B) catalog.json 补 display / category（只对 .md 条目生效） ----
    if os.path.isfile(CATALOG_JSON):
        try:
            with open(CATALOG_JSON, 'r', encoding='utf-8') as f:
                cat = json.load(f)
            for c in cat.get('categories', []):
                cname = c.get('name', '')
                for e in c.get('entries', []):
                    slug = e.get('slug', '')
                    if slug and slug in slug_info and slug_info[slug]['kind'] == 'md':
                        if e.get('display'):
                            slug_info[slug]['display'] = e['display']
                        slug_info[slug]['category'] = cname
        except Exception:
            pass

    # ---- (C) 再补 .html 页面（只加没被 md 占用的 slug，避免冲突） ----
    for fname in sorted(os.listdir(WIKI_DIR)):
        if not fname.endswith('.html'):
            continue
        slug = fname[:-5]
        if slug in slug_info:
            # 同名 slug 已经是 md 条目（md 优先，因为 Jekyll 生成 md→html）
            continue
        # 从 <title> 标签里读一个友好名，读不到就用 slug 本身
        friendly = slug
        try:
            with open(os.path.join(WIKI_DIR, fname), 'r', encoding='utf-8') as f:
                html_text = f.read()
            m = re.search(r'<title>([^<]*)</title>', html_text, flags=re.IGNORECASE)
            if m and m.group(1).strip():
                friendly = m.group(1).strip()
        except Exception:
            pass
        slug_info[slug] = {
            'title': friendly,
            'display': friendly,
            'category': '(HTML 页面)',
            'kind': 'html',
        }

    return slug_info


# ============================================================
# 断链扫描
# ============================================================

def _parse_wiki_slug_from_url(url):
    """如果 URL 是 wiki 内链，返回 slug（不含 /wiki/ 和锚点和 .md）；否则返回 None。"""
    slug = None
    # 形式 A: /wiki/slug
    if url.startswith('/wiki/'):
        slug = url[len('/wiki/'):]
    # 形式 B: ./slug   （仅 wiki 互链，非锚点非纯锚点）
    elif url.startswith('./') and not url.startswith('.//'):
        slug = url[2:]
    if slug is None:
        return None
    # 分离锚点
    if '#' in slug:
        slug, _ = slug.split('#', 1)
    # 去掉 .md 后缀
    if slug.endswith('.md'):
        slug = slug[:-3]
    if not slug:
        return None
    return slug


def extract_frontmatter_and_body(content):
    if not content.startswith('---'):
        return '', content
    lines = content.split('\n')
    if len(lines) < 2:
        return '', content
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return '', content
    fm = '\n'.join(lines[:end + 1])
    body = '\n'.join(lines[end + 1:])
    return fm, body


def scan_dead_links(slug_info):
    """扫描所有 wiki/*.md，返回 list[断链字典]。

    断链字典键：
      file       : 文件名 (abc.md)
      abs_path   : 绝对路径
      line_no    : 1-based 行号
      line_text  : 整行原始文本（用于定位、之后写回时匹配）
      link_text  : 显示文本
      link_title : 可选链接标题（带引号），空串表示无
      link_url   : 原始 URL（可能是 /wiki/xxx 或 ./xxx）
      broken_slug: 真正缺失的 slug 关键字（用于「同类」聚合）
      context    : 简短上下文预览（含前后 20 字）
    """
    broken = []
    existing_slugs = set(slug_info.keys())

    md_files = sorted(glob.glob(os.path.join(WIKI_DIR, '*.md')))
    total_links = 0
    for md_path in md_files:
        fname = os.path.basename(md_path)
        if fname in EXCLUDE_FILES:
            continue
        with open(md_path, 'r', encoding='utf-8') as f:
            original = f.read()
        fm, body = extract_frontmatter_and_body(original)

        # frontmatter 有多少行（决定正文行号从几开始）
        fm_line_count = 0 if not fm else fm.count('\n') + 1

        in_code_block = False
        # body 可能是从 frontmatter 之后开始的；为了行号正确，要把 fm 的行数 + 正文行号
        for body_offset, raw_line in enumerate(body.split('\n'), start=0):
            line_no = fm_line_count + body_offset + 1  # 1-based
            line = raw_line
            stripped = line.lstrip()
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            for m in _LINK_RE.finditer(line):
                link_text = m.group(1)
                link_url = m.group(2)
                link_title = m.group(3) or ''
                total_links += 1
                slug = _parse_wiki_slug_from_url(link_url)
                if slug is None:
                    continue  # 非 wiki 内链
                if _slug_target_exists(slug):
                    continue  # md 或 html 任一存在 → 不是断链
                # ===== 这是断链 =====
                # 构造上下文：链接前后各 18 字
                start = max(0, m.start() - 20)
                end = min(len(line), m.end() + 20)
                context = line[start:end].replace('\t', ' ')
                if start > 0:
                    context = '...' + context
                if end < len(line):
                    context = context + '...'
                broken.append({
                    'file': fname,
                    'abs_path': md_path,
                    'line_no': line_no,
                    'line_text': line,
                    'link_text': link_text,
                    'link_title': link_title,
                    'link_url': link_url,
                    'broken_slug': slug,
                    'context': context,
                    '_match_start': m.start(),   # 行内链接起点
                    '_match_end': m.end(),       # 行内链接终点
                    '_match_obj_start': m.start(),  # 为了之后 _apply_decisions_to_line 不重复调用 finditer
                })

    return broken, total_links, len(md_files) - len(EXCLUDE_FILES)


# ============================================================
# 搜索候选 slug
# ============================================================

def search_slug_candidates(keyword, slug_info, limit=15):
    """返回匹配的 slug 列表，按相关度从高到低。"""
    keyword = (keyword or '').strip()
    if not keyword:
        return []
    k_lower = keyword.lower()

    # 候选集：每个条目计算得分（越高越相关）
    scored = []
    for slug, info in slug_info.items():
        title = info['title'] or slug
        display = info['display'] or title
        s_lower = slug.lower()
        t_lower = title.lower()
        d_lower = display.lower()

        score = 0
        # 完全相等（优先级最高）
        if slug == keyword:
            score += 10000
        if title == keyword or display == keyword:
            score += 8000
        # slug 完全相等（大小写不敏感）
        if s_lower == k_lower:
            score += 4000
        # title / display 完全相等（大小写不敏感）
        if t_lower == k_lower or d_lower == k_lower:
            score += 3000
        # slug 包含关键词
        if k_lower in s_lower:
            score += 500 + (100 - abs(len(slug) - len(keyword)))
        # title / display 包含关键词
        if k_lower in t_lower or k_lower in d_lower:
            score += 300 + (100 - abs(len(title) - len(keyword)))
        # 关键词是拼音或子串拼合的字符完全交集（简单点：至少 60% 字符都存在）
        common = sum(1 for c in set(keyword.lower()) if c in s_lower + t_lower + d_lower)
        if keyword and common / max(1, len(set(keyword.lower()))) >= 0.6:
            score += 50

        if score > 0:
            scored.append((score, slug, info))

    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


# ============================================================
# 决策应用（批量将决定写回文件）
# ============================================================

def apply_decisions(decisions):
    """decisions: list[(record_dict, action, value)]

    action ∈ { 'slug', 'external', 'unlink', 'skip' }
    value  : slug、完整URL、无（unlink/skip 时 None）
    注意：同一行可能有多个链接同时被替换，必须用 re.sub + 回调逐链接判断。
    """
    # 先按 abs_path 分组，逐个文件处理
    by_file = {}
    for rec, action, value in decisions:
        by_file.setdefault(rec['abs_path'], []).append((rec, action, value))

    written_count = 0
    changed_files = 0
    for abs_path, items in by_file.items():
        # 先读原文件
        with open(abs_path, 'r', encoding='utf-8') as f:
            original = f.read()
        lines = original.split('\n')

        # 为了按行号精确定位，先建立 (line_no-1) 的替换映射
        # 但同一行可能多个链接，所以对每个修改行我们用 re.sub 回调
        line_modify_map = {}  # line_idx(0-based) -> list[(rec, action, value)]
        for rec, action, value in items:
            line_idx = rec['line_no'] - 1
            line_modify_map.setdefault(line_idx, []).append((rec, action, value))

        def _line_sub_cb(m, pending_items_for_line):
            """对一行内的单个链接回调：看是否在 pending 修改列表中。

            匹配方式：用 link_text + link_url（+title）三重比对。
            """
            link_text = m.group(1)
            link_url = m.group(2)
            link_title = m.group(3) or ''
            # 找对应的决定
            for i, (rec, action, value) in enumerate(pending_items_for_line):
                if (rec['link_text'] == link_text
                        and rec['link_url'] == link_url
                        and rec['link_title'] == link_title):
                    # 命中：用掉这个决定（pop）
                    pending_items_for_line.pop(i)
                    if action == 'slug':
                        new_url = f'/wiki/{value}'
                        # 保留原锚点
                        if '#' in rec['link_url']:
                            anchor = rec['link_url'].split('#', 1)[1]
                            new_url += '#' + anchor
                        if link_title:
                            return f'[{link_text}]({new_url} {link_title})'
                        else:
                            return f'[{link_text}]({new_url})'
                    elif action == 'external':
                        if link_title:
                            return f'[{link_text}]({value} {link_title})'
                        else:
                            return f'[{link_text}]({value})'
                    elif action == 'unlink':
                        # [text](url) → text
                        return link_text
                    elif action == 'skip':
                        return m.group(0)
            # 没命中 → 原样返回
            return m.group(0)

        for line_idx, pending in line_modify_map.items():
            if 0 <= line_idx < len(lines):
                old_line = lines[line_idx]
                new_line = _LINK_RE.sub(lambda m: _line_sub_cb(m, pending), old_line)
                lines[line_idx] = new_line

        new_content = '\n'.join(lines)
        if new_content != original:
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            written_count += sum(1 for r, a, v in items if a != 'skip')
            changed_files += 1

    return changed_files, written_count


# ============================================================
# 操作台 TUI（交互循环）+ Windows ANSI VT 兼容
# ============================================================

def _enable_vt_mode():
    """在 Windows 上启用虚拟终端（VT）模式 + UTF-8 代码页，让 CMD / PowerShell 正确渲染颜色 / 框线 / 中文。

    返回 True 表示 VT 模式已启用（或不需要启用，如非 Windows）；
    返回 False 表示当前终端不支持 ANSI 颜色，上层应剥离颜色代码避免乱码。
    """
    # 非 Windows 平台（macOS / Linux）默认 ANSI 就可用
    if os.name != 'nt' or sys.platform != 'win32':
        return True

    # --- (A) UTF-8 代码页（chcp 65001）防止 ═╔╗ 等 box-drawing 字符和中文变问号 ---
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        if hasattr(kernel32, 'SetConsoleOutputCP'):
            kernel32.SetConsoleOutputCP(ctypes.c_uint(65001))
        if hasattr(kernel32, 'SetConsoleCP'):
            kernel32.SetConsoleCP(ctypes.c_uint(65001))
    except Exception:
        try:
            os.system('chcp 65001 >nul 2>nul')
        except Exception:
            pass

    # 方法 1：Windows 经典「os.system('')」技巧，某些 CMD 版本会触发 VT 模式
    try:
        os.system('')
    except Exception:
        pass

    # 方法 2：用 ctypes 调用 Kernel32.SetConsoleMode 显式开启 ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004)
    vt_ok = False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # STD_OUTPUT_HANDLE = -11；顺便也给 STD_ERROR_HANDLE (-12) 开一下
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        ENABLE_PROCESSED_OUTPUT = 0x0001

        for std_id in (-11, -12):
            h_std = kernel32.GetStdHandle(wintypes.DWORD(std_id))
            if h_std in (None, 0, -1):
                continue
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(h_std, ctypes.byref(mode)) == 0:
                # 失败通常是因为输出被重定向到文件 / 管道（不是真实控制台）
                continue
            new_mode = wintypes.DWORD(
                mode.value
                | ENABLE_PROCESSED_OUTPUT
                | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            if kernel32.SetConsoleMode(h_std, new_mode) != 0:
                vt_ok = True
    except Exception:
        pass

    return vt_ok


# 根据 VT 是否可用，动态赋值颜色常量（不可用时全部设为空串，避免输出 \033[xxm 乱码字符）
_VT_OK = _enable_vt_mode()
if _VT_OK:
    _CLR_BOLD   = '\033[1m'
    _CLR_RED    = '\033[91m'
    _CLR_GREEN  = '\033[92m'
    _CLR_YELLOW = '\033[93m'
    _CLR_CYAN   = '\033[96m'
    _CLR_RESET  = '\033[0m'
else:
    _CLR_BOLD = _CLR_RED = _CLR_GREEN = _CLR_YELLOW = _CLR_CYAN = _CLR_RESET = ''


def _print_banner(broken, total_links, scanned_files, dry_mode):
    print()
    print('═' * 66)
    print('  ★  星球阁 Wiki · 断链 / 死链修复操作台  ★')
    print('═' * 66)
    print(f'  扫描文件数  : {scanned_files}')
    print(f'  检测内链数  : {total_links}')
    if broken:
        unique_slugs = len({r['broken_slug'] for r in broken})
        unique_files = len({r['file'] for r in broken})
        print(f'  发现断链数  : {_CLR_RED}{_CLR_BOLD}{len(broken)}{_CLR_RESET}'
              f'（{unique_files} 个文件，{unique_slugs} 个不同坏链 slug）')
    else:
        print(f'  发现断链数  : {_CLR_GREEN}{_CLR_BOLD}0 ✨{_CLR_RESET}')
    if dry_mode:
        print(f'  运行模式    : {_CLR_YELLOW}只读预览（--dry，不操作不写文件）{_CLR_RESET}')
    print('═' * 66)


def _print_record_card(idx, total, rec, same_url_count):
    print()
    print('┌─ [' + _CLR_BOLD + f'断链 {idx}/{total}' + _CLR_RESET + '] ─' + '─' * 44 + '┐')
    print(f'│  文件       : {_CLR_CYAN}{rec["file"]}{_CLR_RESET}')
    print(f'│  行号       : 第 {_CLR_CYAN}{rec["line_no"]}{_CLR_RESET} 行')
    print(f'│  显示文本   : {_CLR_YELLOW}{rec["link_text"]}{_CLR_RESET}')
    print(f'│  原链接 URL : {_CLR_RED}{rec["link_url"]}{_CLR_RESET}    ← 目标文件不存在！')
    print(f'│  上下文     : {rec["context"]}')
    if same_url_count > 1:
        print(f'│                                                              │')
        print(f'│  ⚠️  同类数量   : 还有 {_CLR_BOLD}{same_url_count - 1}{_CLR_RESET} 条断链 URL 完全相同')
        print(f'│            （broken_slug = {rec["broken_slug"]}）')
    print('└' + '─' * 62 + '┘')


def _print_menu(same_url_count, dry_mode):
    print('你想怎么修？')
    print('─' * 62)
    if dry_mode:
        print(f'  {_CLR_YELLOW}[只读模式 --dry] 以下选项不允许执行，仅列出清单。{_CLR_RESET}')
        print('  n  下一条   l  全局列表   q  退出')
    else:
        print(f'  1  搜索候选 slug        （在所有存在的条目中按关键词搜）')
        print(f'  2  输入正确 slug        （手敲文件名，不含 .md，大小写敏感）')
        print(f'  3  改为外部链接         （替换为完整 https://... URL）')
        print(f'  4  解除链接（仅留文本）  （把 [xxx](坏链) → 只剩 xxx）')
        print(f'  5  跳过本条目')
        if same_url_count > 1:
            print(f'  {_CLR_BOLD}6  批量处理全部同类{_CLR_RESET}     （{same_url_count} 条相同坏链 slug 一次性操作）')
        print('─' * 62)
        print('  l  全局断链一览   q  保存已处理修改并退出')


def _do_search(slug_info, default_keyword, existing_slugs):
    """执行「搜索候选 slug」交互，返回 (action='slug', value=slug) 或 None 表示取消。"""
    print()
    prompt = f'  请输入搜索关键词（直接回车默认用 "{default_keyword}"，输入 q 取消）: '
    try:
        k = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if k.lower() == 'q':
        return None
    keyword = k if k else default_keyword
    cands = search_slug_candidates(keyword, slug_info, limit=20)
    if not cands:
        print(f'  {_CLR_YELLOW}未找到与 "{keyword}" 匹配的 slug。{_CLR_RESET}')
        return None
    print(f'  找到 {len(cands)} 个候选（按相关度排序）：')
    print(f'  {"#":<3}{"Slug (类型)":<28}{"Display/Title":<30}{"分类":<14}')
    print('  ' + '-' * 72)
    for i, (score, slug, info) in enumerate(cands, start=1):
        kind = info.get('kind', 'md')
        kind_tag = f'  [{_CLR_CYAN}HTML{_CLR_RESET}]' if kind == 'html' else '  [md]'
        slug_display = f'{slug}{kind_tag}'
        disp = info.get('display') or info.get('title') or slug
        cat = info.get('category') or '-'
        print(f'  {i:<3}{slug_display:<28}{disp:<30}{cat:<14}')
    print()
    try:
        choice = input(f'  请输入候选编号（1-{len(cands)}，0 或 q 取消）: ').strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice.lower() in ('q', '0', ''):
        return None
    if choice.isdigit():
        n = int(choice)
        if 1 <= n <= len(cands):
            selected_slug = cands[n - 1][1]
            return ('slug', selected_slug)
    print('  无效输入，取消本操作。')
    return None


def _prompt_action_menu(allowed_options, same_url_count, slug_info, display_text, existing_slugs):
    """针对单个断链条目询问用户选择，返回 (action, value) 或 (None, None) 表示跳过。

    allowed_options: set，例如 {'1','2','3','4','5','6'}
    """
    # 记忆：对相同 broken_slug + display_text 的情况，应用之前相同决定的缓存不在本函数处理
    while True:
        try:
            sel = input('请输入选项: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            return ('quit', None)
        if not sel:
            continue

        # --- 全局选项 ---
        if sel == 'l':
            return ('list_all', None)
        if sel == 'q':
            return ('quit', None)

        # --- 只读模式 ---
        if sel == 'n':
            return ('next', None)

        # --- 写操作 ---
        if sel not in allowed_options:
            print(f'  无效选项，请输入 {"/".join(sorted(allowed_options))}。也可 l=列表 / q=退出')
            continue

        if sel == '1':
            res = _do_search(slug_info, display_text, existing_slugs)
            if res is not None:
                return res
            # None = 取消，重新问
            continue

        if sel == '2':
            try:
                v = input('  请输入正确 slug（不含 .md；q 取消）: ').strip()
            except (EOFError, KeyboardInterrupt):
                return None, None
            if v.lower() == 'q' or not v:
                continue
            # 去掉可能的 .md /wiki/ 前缀
            v = v.strip()
            if v.startswith('/wiki/'):
                v = v[len('/wiki/'):]
            if v.endswith('.md'):
                v = v[:-3]
            if not _slug_target_exists(v):
                print(f'  {_CLR_YELLOW}警告：slug "{v}" 在 wiki/ 下找不到对应的 .md 或 .html。是拼写错误还是想稍后创建？{_CLR_RESET}')
                c = input('  仍然使用？(y 强行使用 / n 重新输入 / q 取消): ').strip().lower()
                if c == 'q':
                    continue
                if c != 'y':
                    continue
            return ('slug', v)

        if sel == '3':
            try:
                v = input('  请输入完整 URL（https://...  q 取消）: ').strip()
            except (EOFError, KeyboardInterrupt):
                return None, None
            if v.lower() == 'q' or not v:
                continue
            if not (v.startswith('http://') or v.startswith('https://')):
                print('  外链必须以 http:// 或 https:// 开头，取消。')
                continue
            return ('external', v)

        if sel == '4':
            y = input(f'  确认把 [{display_text}](坏链) → 只剩 "{display_text}" 纯文本？(y/n): ').strip().lower()
            if y == 'y':
                return ('unlink', None)
            continue

        if sel == '5':
            return ('skip', None)

        if sel == '6':
            return ('batch_same_slug', None)


def _print_all_broken_list(broken):
    print()
    print('═' * 80)
    print('  📋  全局断链一览（共 %d 条）' % len(broken))
    print('═' * 80)
    # 按 file 分组打印
    by_file = {}
    for r in broken:
        by_file.setdefault(r['file'], []).append(r)
    total = 0
    for fname in sorted(by_file.keys()):
        recs = sorted(by_file[fname], key=lambda x: x['line_no'])
        print(f'\n  📁 {_CLR_CYAN}{fname}{_CLR_RESET}（{len(recs)} 条）')
        for r in recs:
            total += 1
            print(f'    L{r["line_no"]:<4}  [{r["link_text"]:<18}]({_CLR_RED}{r["link_url"]}{_CLR_RESET})')
    print('\n' + '═' * 80)
    print(f'  合计: {total} 条断链')
    print('═' * 80)


# ============================================================
# 主入口
# ============================================================

def main():
    dry_mode = '--dry' in sys.argv or '-d' in sys.argv

    # ========== 1. 加载 & 扫描 ==========
    print('\n[1/3] 加载 Wiki 条目数据（含 title / display / category，包括 HTML 页面）...')
    slug_info = load_slugs_pages()
    existing_slugs = set(slug_info.keys())

    print('[2/3] 扫描 Wiki 内链断链...')
    broken, total_links, scanned_files = scan_dead_links(slug_info)

    # ========== 2. 操作台主循环 ==========
    _print_banner(broken, total_links, scanned_files, dry_mode)

    if not broken:
        print(f'\n  ✨ 没有任何断链。无需修复，直接退出。')
        return

    # 同类聚合：相同 broken_slug 的计数
    same_url_counter = {}
    for r in broken:
        same_url_counter[r['broken_slug']] = same_url_counter.get(r['broken_slug'], 0) + 1

    # 只读模式：只列清单，不操作
    if dry_mode:
        _print_all_broken_list(broken)
        print('\n  💡 提示：去掉 --dry 参数即可进入交互式修复操作台。')
        return

    # 决策列表：[(record_dict, action, value)]
    decisions = []
    # 已处理索引集合（防止重复处理，比如批量同类时跳过）
    handled_indices = set()
    # 记忆：相同 (broken_slug, display_text) → (action, value) 的历史选择，用于「沿用上次」
    history = {}  # (broken_slug, link_text) -> (action, value)

    total = len(broken)
    i = 0
    while i < total:
        if i in handled_indices:
            i += 1
            continue
        rec = broken[i]
        same_count = same_url_counter[rec['broken_slug']]

        # --- 打印卡片 ---
        _print_record_card(i + 1, total, rec, same_count)

        # --- 记忆检查 ---
        hist_key = (rec['broken_slug'], rec['link_text'])
        if hist_key in history:
            prev_act, prev_val = history[hist_key]
            hint = {'slug': f'/wiki/{prev_val}', 'external': prev_val,
                    'unlink': '解除链接→只留文本', 'skip': '跳过'}.get(prev_act, prev_act)
            print(f'  {_CLR_CYAN}💡 记忆：同样的坏链 slug「{rec["broken_slug"]}」+ 显示文本「{rec["link_text"]}」你上次选择 → {hint}{_CLR_RESET}')
            a = input(f'  是否沿用上次选择？(y 沿用 / n 重新选择 / q 退出): ').strip().lower()
            if a == 'y':
                decisions.append((rec, prev_act, prev_val))
                handled_indices.add(i)
                i += 1
                continue
            elif a == 'q':
                break

        allowed = {'1', '2', '3', '4', '5'}
        if same_count >= 2:
            allowed.add('6')
        _print_menu(same_count, dry_mode=False)

        act, val = _prompt_action_menu(allowed, same_count, slug_info,
                                        rec['link_text'], existing_slugs)

        # --- 全局动作 ---
        if act == 'quit':
            break
        if act == 'list_all':
            _print_all_broken_list(broken)
            continue  # 重新展示当前卡片
        if act == 'next':
            i += 1
            continue

        # --- 批量同类 ---
        if act == 'batch_same_slug':
            target_slug = rec['broken_slug']
            group_indices = [j for j, r in enumerate(broken)
                             if r['broken_slug'] == target_slug and j not in handled_indices]
            print(f'\n  🔧 批量模式：坏链 slug = {target_slug}，本组共 {len(group_indices)} 条')
            print('  请为这一组选择**相同的处置动作**：')
            print('    1 搜索候选   2 输入slug   3 外链   4 解除链接   5 全部跳过   q 取消批量')
            batch_allowed = {'1', '2', '3', '4', '5'}
            b_act, b_val = _prompt_action_menu(batch_allowed, 0, slug_info,
                                                rec['link_text'], existing_slugs)
            if b_act == 'quit' or b_act == 'next' or b_act == 'list_all':
                print('  批量已取消。')
                continue
            if b_act in {'slug', 'external', 'unlink', 'skip'}:
                for j in group_indices:
                    decisions.append((broken[j], b_act, b_val))
                    handled_indices.add(j)
                # 写入记忆（用当前 rec 的文本和 slug 做 key）
                if b_act in {'slug', 'external', 'unlink', 'skip'}:
                    history[hist_key] = (b_act, b_val)
                print(f'  ✔ 已批量处置 {len(group_indices)} 条')
                i += 1
                continue
            else:
                print('  批量取消。')
                continue

        # --- 单个处置 ---
        if act in {'slug', 'external', 'unlink', 'skip'}:
            decisions.append((rec, act, val))
            handled_indices.add(i)
            # 记忆（skip 也记，避免重复问）
            history[hist_key] = (act, val)
            i += 1
            continue

        # 其他情况（取消）就重新显示菜单
        continue

    # ========== 3. 写回文件 ==========
    print()
    if not any(a not in ('skip',) for r, a, v in decisions):
        print('  所有条目都被跳过，没有改动。')
        return

    effective = [(r, a, v) for r, a, v in decisions if a != 'skip']
    skip_cnt = len(decisions) - len(effective)
    print(f'  处理汇总：')
    print(f'    共决定处置 {len(decisions)} 条（其中 skip {skip_cnt} 条，实际修复 {len(effective)} 条）')
    if effective:
        show_n = min(5, len(effective))
        for r, a, v in effective[:show_n]:
            print(f'    · {r["file"]}:L{r["line_no"]}  [{r["link_text"]}]({r["link_url"]}) → ', end='')
            if a == 'slug':
                print(f'/wiki/{v}')
            elif a == 'external':
                print(v)
            elif a == 'unlink':
                print(f'解除链接→纯文本「{r["link_text"]}」')
        if len(effective) > show_n:
            print(f'    ... 另外还有 {len(effective) - show_n} 条，不一一列出')

    confirm = input('\n  是否写回文件？(y 写入 / 其他取消): ').strip().lower()
    if confirm != 'y':
        print('  取消，未写入任何文件。')
        return

    changed_files, written_count = apply_decisions(decisions)
    print(f'\n  ✔ 写入完成！共修改 {changed_files} 个文件，实际替换 / 解除 {written_count} 个链接。')
    # 统计：处理后如果还有断链（比如没处理完 / 跳过的），提示数量
    remain = len([j for j in range(total) if j not in handled_indices])
    if remain > 0:
        print(f'  ⚠️  还有 {remain} 条断链未处置（你手动跳过或中途退出），重跑本脚本可继续修。')


if __name__ == '__main__':
    main()
