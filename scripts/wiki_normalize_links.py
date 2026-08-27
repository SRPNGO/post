# -*- coding: utf-8 -*-
"""
Wiki 内链格式规范化脚本
========================

将 Wiki 条目 Markdown 文件中的 Wiki 内链统一为 `/wiki/<slug>` 根相对路径格式。

处理范围：
  1. 把 `./slug` 形式的相对内链 → `/wiki/slug`
  2. 顺带校验 `/wiki/slug` 目标文件是否真实存在（避免把断链"规范化"后更隐蔽）
  3. 外链、锚点、图片、站内 `/img/` 等非 Wiki 内链 → 保持原样不动

明确不处理的内容：
  - YAML frontmatter 整块（包括 customExtraContent 里的 `<a href="./wiki_intro">`，
    Jekyll 要求保留 `./` 前缀）
  - 图片标记 `![alt](url)`
  - 代码块内的文本（``` 包裹）——为简单起见，按行处理时凡处于代码块中的行均跳过

用法（在项目根目录执行）：
  python scripts/wiki_normalize_links.py             # 预览模式，仅打印 diff 和统计，不写文件
  python scripts/wiki_normalize_links.py --apply     # 实际写入文件（确认无误后使用）
"""

import os
import re
import sys
import glob

# ========= 路径解析（不依赖 CWD） =========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, 'wiki')

# 非条目文件（这些 md 文件不需要规范化处理，通常作为模板/规范本身）
EXCLUDE_FILES = {'wikiRule.md'}


# ============================================================
# 工具函数
# ============================================================

def load_existing_slugs():
    """扫描 wiki/ 下所有 .md，返回 {slug_without_ext} 集合，用于白名单校验。"""
    slugs = set()
    for f in os.listdir(WIKI_DIR):
        if f.endswith('.md'):
            slugs.add(f[:-3])
    return slugs


def split_frontmatter_and_body(content):
    """把 Markdown 文本拆成 (frontmatter_str, body_str)。

    frontmatter 原样保留，包含开头和结尾的 ---；
    若无 frontmatter 则 frontmatter_str 为空串。
    """
    if not content.startswith('---'):
        return '', content
    lines = content.split('\n')
    if len(lines) < 2:
        return '', content
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end_idx = i
            break
    if end_idx is None:
        return '', content
    fm = '\n'.join(lines[:end_idx + 1])
    body = '\n'.join(lines[end_idx + 1:])
    return fm, body


# 匹配 Markdown 行内链接（不含图片）。
#   负向断言 (?<!\!) 排除 ![alt](url)
#   捕获组 1 = 显示文本方括号内内容（不含方括号）
#   捕获组 2 = URL（**必须排除 )**，否则 ][多个链接挨在一起时 会串成一个URL）
#   捕获组 3 = 可选标题（带引号），若不存在则为 None
_LINK_RE = re.compile(
    r'(?<!\!)\[([^\]]*)\]\(([^\s)]+)(?:\s+("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'))?\)'
)


def _strip_md_suffix(s):
    if s.endswith('.md'):
        return s[:-3]
    return s


def normalize_url(url, existing_slugs):
    """对单个 URL 做规范化处理。

    返回 (new_url, status, note)：
      status ∈ {'changed', 'kept', 'warn'}
      note   : 警告信息（仅当 status='warn' 时），否则空串
    """
    # === 1. customExtraContent 中的 ./wiki_intro：不动（由整体跳过 frontmatter 保护，
    #        这里再加一道保险：显式匹配） ===
    if url == './wiki_intro' or url.startswith('./wiki_intro#'):
        return url, 'kept', ''

    # === 2. 相对路径内链 `./slug` → `/wiki/slug` ===
    if url.startswith('./') and not url.startswith('.//'):
        remainder = url[2:]  # 去掉 ./
        # 分离锚点
        anchor = ''
        if '#' in remainder:
            slug_part, anchor = remainder.split('#', 1)
            anchor = '#' + anchor
        else:
            slug_part = remainder
        # 去掉可能的 .md 后缀
        slug_part = _strip_md_suffix(slug_part)

        # 空 slug（如 `./#xxx`）或纯锚点：不处理
        if not slug_part:
            return url, 'kept', ''

        # 白名单校验：slug 必须真实存在
        new_url = f'/wiki/{slug_part}{anchor}'
        if slug_part in existing_slugs:
            return new_url, 'changed', ''
        else:
            # 目标不存在：不做转换，转为警告，保留原链接以便人工定位断链
            return url, 'warn', (
                f'目标页面 wiki/{slug_part}.md 不存在，跳过转换以避免掩盖断链。'
                f'请确认是拼写错误还是页面尚未创建。原链接：{url}'
            )

    # === 3. 已是规范格式 `/wiki/slug`：顺便校验目标存在性 ===
    if url.startswith('/wiki/'):
        remainder = url[len('/wiki/'):]
        # 分离锚点
        if '#' in remainder:
            slug_part, _ = remainder.split('#', 1)
        else:
            slug_part = remainder
        slug_part = _strip_md_suffix(slug_part)
        if slug_part and slug_part not in existing_slugs:
            return url, 'warn', (
                f'目标页面 wiki/{slug_part}.md 不存在（已写成 /wiki/… 格式但无对应文件）。'
                f'疑似断链或拼写错误。原链接：{url}'
            )
        return url, 'kept', ''

    # === 其余情况：外链、锚点、/img/、/docs/、mailto: 等 → 不动 ===
    return url, 'kept', ''


def process_body_lines(body, existing_slugs, warnings_per_file):
    """处理正文 body 字符串，返回 (new_body, change_stats)。

    change_stats = {
        'links_changed': int,   # 成功规范化的链接数
        'links_checked': int,   # 共扫描多少个链接
        'code_skipped_lines': int,  # 处于代码块中被跳过的行数（仅统计）
    }
    warnings_per_file: list[str]，本函数往里 append 警告
    """
    stats = {'links_changed': 0, 'links_checked': 0, 'code_skipped_lines': 0}
    out_lines = []
    in_code_block = False  # 是否位于 ``` 代码块内

    for raw_line in body.split('\n'):
        line = raw_line

        # —— 代码块保护：``` 开始/结束切换状态；处于代码块中时整行跳过 ——
        stripped = line.lstrip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            out_lines.append(line)
            continue
        if in_code_block:
            stats['code_skipped_lines'] += 1
            out_lines.append(line)
            continue

        # —— 逐链接替换（通过 _LINK_RE.sub 的回调） ——
        line_warnings = []

        def _link_sub(m):
            text = m.group(1)
            url = m.group(2)
            title = m.group(3) or ''  # 含引号或为空
            stats['links_checked'] += 1
            new_url, status, note = normalize_url(url, existing_slugs)
            if status == 'warn':
                line_warnings.append(note)
            elif status == 'changed':
                stats['links_changed'] += 1
            # 重建链接：[text](new_url "title"?)  —— title 含引号或空串直接拼接
            if title:
                return f'[{text}]({new_url} {title})'
            else:
                return f'[{text}]({new_url})'

        new_line = _LINK_RE.sub(_link_sub, line)

        for w in line_warnings:
            warnings_per_file.append(w)

        out_lines.append(new_line)

    return '\n'.join(out_lines), stats


# ============================================================
# 主流程
# ============================================================

def main():
    apply_mode = '--apply' in sys.argv

    print('=' * 64)
    print('  Wiki 内链格式规范化工具')
    print('  模式   :', '实际写入 (--apply)' if apply_mode else '预览模式 (dry-run，加 --apply 生效)')
    print('  目标   : 将 `./slug` → `/wiki/slug`，并校验目标文件存在')
    print('  排除   :', ', '.join(EXCLUDE_FILES) if EXCLUDE_FILES else '无')
    print('=' * 64)

    if not os.path.isdir(WIKI_DIR):
        print(f'\n[ERROR] Wiki 目录不存在：{WIKI_DIR}')
        sys.exit(1)

    existing_slugs = load_existing_slugs()
    print(f'\n[1/3] 已加载白名单：wiki/ 下共 {len(existing_slugs)} 个 .md 条目')

    md_files = sorted(glob.glob(os.path.join(WIKI_DIR, '*.md')))
    total_files = len(md_files)

    # 汇总统计
    summary = {
        'files_scanned': 0,
        'files_changed': 0,
        'files_with_warn': 0,
        'total_links_checked': 0,
        'total_links_changed': 0,
    }
    per_file_details = []     # 每个有变化/警告的文件的详情列表
    all_warnings = []         # 全局警告汇总（文件 + 警告文本）

    print(f'\n[2/3] 扫描 {total_files} 个 Markdown 文件...\n')

    for md_path in md_files:
        fname = os.path.basename(md_path)
        if fname in EXCLUDE_FILES:
            print(f'   [-] 跳过 {fname}（排除列表）')
            continue

        summary['files_scanned'] += 1

        with open(md_path, 'r', encoding='utf-8') as f:
            original = f.read()

        fm, body = split_frontmatter_and_body(original)

        file_warnings = []
        new_body, stats = process_body_lines(body, existing_slugs, file_warnings)

        # 重组：frontmatter + \n\n + body，保持末尾换行风格一致
        if fm:
            new_content = fm.rstrip('\n') + '\n\n' + new_body
        else:
            new_content = new_body
        if original.endswith('\n') and not new_content.endswith('\n'):
            new_content += '\n'
        if not original.endswith('\n') and new_content.endswith('\n'):
            new_content = new_content.rstrip('\n')

        has_change = (new_content != original)
        has_warn = bool(file_warnings)

        summary['total_links_checked'] += stats['links_checked']
        summary['total_links_changed'] += stats['links_changed']

        detail_line_prefix = '   '
        if has_change and has_warn:
            status_tag = '[Δ+!]'
            summary['files_changed'] += 1
            summary['files_with_warn'] += 1
            print(f'{detail_line_prefix}{status_tag} {fname}: '
                  f'转换{stats["links_changed"]}个链接，{len(file_warnings)}个警告')
        elif has_change:
            status_tag = '[Δ]  '
            summary['files_changed'] += 1
            print(f'{detail_line_prefix}{status_tag} {fname}: '
                  f'转换{stats["links_changed"]}个链接')
        elif has_warn:
            status_tag = '[!]  '
            summary['files_with_warn'] += 1
            print(f'{detail_line_prefix}{status_tag} {fname}: '
                  f'{len(file_warnings)}个警告（疑似断链/拼写错误）')
        else:
            print(f'{detail_line_prefix}-     {fname}')
            continue

        # 记录详情
        for w in file_warnings:
            all_warnings.append((fname, w))
        per_file_details.append({
            'fname': fname,
            'changed': has_change,
            'warn_count': len(file_warnings),
            'links_changed': stats['links_changed'],
        })

        # 写文件（仅 apply 模式且有变化时）
        if apply_mode and has_change:
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

    # ============== 汇总报告 ==============
    print(f'\n[3/3] 完成！')
    print('-' * 48)
    print(f'  扫描文件数      : {summary["files_scanned"]}')
    print(f'  有改动文件数    : {summary["files_changed"]}'
          + ('' if apply_mode else '  (预览未写入，加 --apply 生效)'))
    print(f'  有警告文件数    : {summary["files_with_warn"]}')
    print(f'  共扫描链接数    : {summary["total_links_checked"]}')
    print(f'  成功规范化数    : {summary["total_links_changed"]}')
    print('-' * 48)

    if all_warnings:
        print(f'\n⚠️  共 {len(all_warnings)} 条警告（疑似断链/目标不存在，未转换，请人工复核）：')
        cur_f = None
        for fname, w in all_warnings:
            if fname != cur_f:
                print(f'\n  ● {fname}')
                cur_f = fname
            print(f'      - {w}')
        print()

    if summary['files_changed'] == 0 and summary['files_with_warn'] == 0:
        print('  ✨ 所有 Wiki 内链均已符合规范，且无断链告警。')
        return

    # 预览模式下：有改动时提示确认（与 wiki_auto_link.py 风格一致）
    if not apply_mode and summary['files_changed'] > 0:
        print('\n[i] 当前是预览模式，未写入任何文件。')
        print('    请仔细核对上方的「成功规范化数」与「警告列表」无误后：')
        try:
            choice = input('    输入 apply 执行写入，其他任意内容退出 → ').strip()
        except (EOFError, KeyboardInterrupt):
            # 非交互终端（管道 / 无人值守脚本）或 Ctrl+C：默认视为「取消」
            print('\n  检测到非交互环境 / 用户取消 → 已退出，未写入任何文件。')
            return
        if choice == 'apply':
            print('\n  正在写入文件...')
            written = 0
            for md_path in md_files:
                fname = os.path.basename(md_path)
                if fname in EXCLUDE_FILES:
                    continue
                with open(md_path, 'r', encoding='utf-8') as f:
                    original = f.read()
                fm, body = split_frontmatter_and_body(original)
                file_warnings = []
                new_body, _ = process_body_lines(body, existing_slugs, file_warnings)
                if fm:
                    new_content = fm.rstrip('\n') + '\n\n' + new_body
                else:
                    new_content = new_body
                if original.endswith('\n') and not new_content.endswith('\n'):
                    new_content += '\n'
                if not original.endswith('\n') and new_content.endswith('\n'):
                    new_content = new_content.rstrip('\n')
                if new_content != original:
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    written += 1
            print(f'  ✔ 写入完成，共修改 {written} 个文件。')
        else:
            print('  已退出，未写入任何文件。')


if __name__ == '__main__':
    main()
