# -*- coding: utf-8 -*-
"""
星球阁Wiki 词条自索引脚本
=========================
根据 wikiRule.md 中的「首次出现加互链」原则，自动为 Wiki 条目添加交叉索引链接。

功能：
  1. 扫描 wiki/ 目录下所有 .md 文件，从 YAML frontmatter 的 title 字段提取专有名词
  2. 从首段定义句中额外提取别名（又称**XX**、英文 **XX**、缩写 **XX** 等模式）
  3. 对每个 Wiki 文件的纯正文（排除 frontmatter、## 各级标题、相关页面节、页脚注释）进行扫描
  4. 对首次出现的、尚未添加链接的专有名词自动加链接
  5. 若术语出现时已加粗（**术语**），则链接格式为 **[术语](url)**，即星号在外

用法：
  python scripts/wiki_auto_link.py              # 预览模式（dry-run），只打印修改不写文件
  python scripts/wiki_auto_link.py --apply      # 真正写入文件
"""

import os
import re
import sys
import glob

# ========= 配置 =========
# 脚本位于根目录 scripts/ 下，wiki 目录在 scripts 的上一级
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, 'wiki')

# 无需处理的文件
EXCLUDE_FILES = ['wikiRule.md', 'wiki_intro.md']

# 术语最小字符长度（避免单字误匹配）
MIN_TERM_LENGTH = 2

# 术语黑名单：这些术语不自动加链接（通常因为缩写有歧义，指向多个条目）
# 例如 "SRPG" 同时是「星球编程」(SRPNGO) 和「星球地理」(SRPG) 的缩写，无法确定指向谁
TERM_BLACKLIST = {'SRPG'}

# 合并交替正则（惰性构建，避免每个文件重复编译）
# 将全部术语按长度降序合并为一个交替正则，用一次 finditer 扫出整行所有术语命中，
# 取代原先对每个术语分别 re.finditer 扫描整行的做法，把每行代价从 O(T*L) 降到约 O(L)。
_TERM_MERGE_RE = None


def ensure_merged_re(sorted_terms):
    """按长度降序把术语合并成单个交替正则。长词放前，用贪心保证命中最长的术语。"""
    global _TERM_MERGE_RE
    if _TERM_MERGE_RE is None:
        _TERM_MERGE_RE = re.compile('|'.join(re.escape(t) for t in sorted_terms))
    return _TERM_MERGE_RE


# ============================================================
# 工具函数：Frontmatter 解析
# ============================================================

def extract_frontmatter_and_body(content):
    """将 Markdown 文本分离为 YAML frontmatter 和正文两部分。

    返回:
        frontmatter (str): 包含开头 --- 和结尾 --- 的完整 YAML 块
        body (str): 正文部分（不含 frontmatter 后的首空行）
    """
    if not content.startswith('---'):
        return '', content

    # 找到第二个 ---（结束标记）
    lines = content.split('\n')
    if len(lines) < 2:
        return '', content

    fm_end_line_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            fm_end_line_idx = i
            break

    if fm_end_line_idx is None:
        return '', content

    frontmatter = '\n'.join(lines[:fm_end_line_idx + 1])
    body = '\n'.join(lines[fm_end_line_idx + 1:])

    # 去掉 body 开头的空行
    body = body.lstrip('\n')

    return frontmatter, body


def extract_title_from_frontmatter(frontmatter):
    """从 YAML frontmatter 中提取 title 字段的值。"""
    match = re.search(r'^title:\s*(.+?)\s*$', frontmatter, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


# ============================================================
# 工具函数：别名提取（从首段定义句）
# ============================================================

def extract_first_paragraph(body):
    """取正文首段定义句，到第一个空行 / --- / 标题行为止。"""
    lines = body.split('\n')
    para_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == '' or stripped == '---' or stripped.startswith('#'):
            break
        para_lines.append(line)
    return ' '.join(para_lines)


def extract_aliases_from_first_para(first_para, main_title):
    """从首段定义中提取所有别名，返回 set。

    识别的模式（只在「首段定义括号 (又称 ... 缩写 **XX**)」区域内 / 或紧接在定义关键词后）：
      - 又称**别名**
      - 、**别名**（括号内连续多个顿号分隔别名的列表）
      - 英文 **EnglishName** / 英文 Name
      - 缩写 **CODE** / 缩写 CODE
    所有提取结果会额外剔除：
      - 已被写成链接格式的文本 [xxx]、[/wiki] 等残余
      - 含常见标点/分隔符（逗号、顿号、冒号、括号、方括号）的长句子（不是别名是描述句）
      - 长度超过主标题 4 倍的异常值
    """
    aliases = set()

    if not first_para:
        return aliases

    # --- 辅助：清理候选别名，若不能通过过滤则返回 None ---
    _DISALLOW_IN_ALIAS = set('[]()（），、：:；;，。.!！？"\'')

    def _clean(cand):
        # 剔除包含链接格式残余的（比如 物理[抽象科普](/wiki/CXKP)创始人 → 整条剔除）
        if '[' in cand or ']' in cand or 'http' in cand or '/wiki' in cand:
            return None
        cand = cand.strip().strip('*').strip()
        if not cand or cand == main_title or len(cand) < MIN_TERM_LENGTH:
            return None
        # 过滤包含常见不允许字符的候选
        if any(ch in _DISALLOW_IN_ALIAS for ch in cand):
            return None

        # -------- 日期/编号过滤器：超过50%字符是 数字/年月日-/— 的通常是时间范围，不是别名 --------
        date_chars = set('0123456789年月日-/——至~约')
        meaningful_chars = [c for c in cand if not c.isspace()]
        if meaningful_chars:
            date_ratio = sum(1 for c in meaningful_chars if c in date_chars) / len(meaningful_chars)
            if date_ratio >= 0.5:
                return None
        # 完全由 数字+符号 构成的（如 2022.11、473w-1）跳过：如果全是非中文且不含英文字母，则跳过
        if not re.search(r'[\u4e00-\u9fffA-Za-z]', cand):
            return None
        # "约2021年12月" 这种近似日期也跳过
        if re.match(r'^约?\d{4}年', cand):
            return None
        # "2022年初 / 2023年上半年" 类日期
        if re.match(r'^\d{4}年(初|末|上|下|前|后|中|春|夏|秋|冬|上半年|下半年)', cand):
            return None

        # 长度过大通常是描述句不是别名
        if len(main_title) >= 2 and len(cand) > max(14, len(main_title) * 3):
            return None
        return cand

    # -------- 加粗别名（又称**XXX** / 英文 **XXX** / 缩写 **XXX**）--------
    keyword_bold_patterns = [
        r'又称\*\*(.+?)\*\*',        # 又称**别名**
        r'亦作\*\*(.+?)\*\*',        # 亦作**别名**
        r'英文 \*\*(.+?)\*\*',       # 英文 **Name**
        r'缩写 \*\*(.+?)\*\*',       # 缩写 **CODE**
    ]
    for pat in keyword_bold_patterns:
        for m in re.finditer(pat, first_para):
            a = _clean(m.group(1))
            if a:
                aliases.add(a)

    # -------- 「、**XXX**」连续别名列表：仅在括号别名范围内生效 --------
    # 首段定义句通常是：**标题**（又称**A**、**B**，英文 **C**，缩写 **D**）是...
    # 先找到全角括号范围
    paren_match = re.search(r'（(.+?)）', first_para)
    if paren_match:
        inside_paren = paren_match.group(1)
        # 在括号内部找所有 **加粗项** 中由顿号分隔的
        # 方法：找出所有 \*\*(.+?)\*\*，检查它们之前是否是 又称/顿号/英文/缩写
        for bm in re.finditer(r'\*\*(.+?)\*\*', inside_paren):
            bold_content = bm.group(1)
            start = bm.start()
            before = inside_paren[max(0, start - 5):start]
            # 如果前面5字符内含有 又称/、/英文 /缩写 这些分隔标记，才认为是别名
            if ('又称' in before) or ('亦作' in before) or ('、' in before) \
               or before.rstrip().endswith('，') or before.strip() == '':
                a = _clean(bold_content)
                if a:
                    aliases.add(a)
        # 括号内的 英文 PlainName / 缩写 CODE 模式
        for pat in [r'英文 ([A-Za-z0-9][A-Za-z0-9\-_\.]{1,})',
                    r'缩写 ([A-Za-z0-9][A-Za-z0-9\-_\.]{1,})']:
            for m in re.finditer(pat, inside_paren):
                a = _clean(m.group(1))
                if a:
                    aliases.add(a)

    return aliases


# ============================================================
# Step 1：构建术语数据库
# ============================================================

def build_term_database(wiki_dir):
    """扫描 Wiki 目录，构建术语 -> (文件名, URL路径) 的映射。

    返回:
        term_db:   {term_str: (filename_without_ext, url_path)}
        file_titles: {filename_without_ext: title_str}  （用于排除自身术语）
    """
    term_db = {}          # term -> (fname, url)
    file_titles = {}      # fname -> title

    md_files = sorted(glob.glob(os.path.join(wiki_dir, '*.md')))

    print(f'\n[1/3] 扫描 Wiki 条目提取术语（共 {len(md_files)} 个文件）')

    for md_path in md_files:
        fname = os.path.basename(md_path)
        if fname in EXCLUDE_FILES:
            continue

        name_noext = os.path.splitext(fname)[0]
        url = f'/wiki/{name_noext}'

        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()

        fm, body = extract_frontmatter_and_body(content)
        title = extract_title_from_frontmatter(fm)
        if not title:
            print(f'   [!] {fname}: 未找到 title 字段，跳过')
            continue

        file_titles[name_noext] = title

        # --- 主标题入库 ---
        if len(title) >= MIN_TERM_LENGTH:
            if title not in term_db:
                term_db[title] = (name_noext, url)
                print(f'   + {title} → {url}')
            else:
                existing_fname, _ = term_db[title]
                print(f'   [!] 术语「{title}」冲突：{fname} vs {existing_fname}.md，保留前者')

        # --- 从首段提别名 ---
        first_para = extract_first_paragraph(body)
        aliases = extract_aliases_from_first_para(first_para, title)
        for alias in aliases:
            if alias in TERM_BLACKLIST:
                print(f'   [-] 别名「{alias}」在黑名单中，跳过注册 （来自 {fname}）')
                continue
            if alias not in term_db:
                term_db[alias] = (name_noext, url)
                print(f'   + 别名「{alias}」 → {url} （来自 {fname}）')
            else:
                # 别名冲突通常是因为多个条目的括号里写了同一个通用词（比如"星球圈"），
                # 这里保留第一次注册的映射
                pass

    return term_db, file_titles


# ============================================================
# Step 2：单个文件正文逐行处理
# ============================================================

def find_protected_spans(line):
    """找出一行中已有 Markdown 链接「[text](url)」的完整保护范围。

    保护范围包括方括号内文本 AND 圆括号内 URL，防止在已有链接的文字或
    URL 部分内匹配到术语并替换（会破坏链接）。

    返回 list[(start, end)]，坐标是相对于 line 的偏移量。
    """
    spans = []
    # 匹配 [text](url) 的整体范围（含方括号和圆括号）
    for m in re.finditer(r'\[([^\]]*)\]\(([^)]*)\)', line):
        spans.append((m.start(), m.end()))
    return spans


def collect_linked_files_from_line(line, term_db):
    """扫描一行中已有的 Markdown 链接，提取已链接的目标文件名。

    从 URL 中提取目标文件（/wiki/xxx → xxx），不依赖显示文本是否与
    注册术语完全匹配。这样即使用户写了 [Sw天白](/wiki/TB) 这样的变体
    链接文本，也能正确识别出 TB 已被链接过。

    同时也通过显示文本匹配术语，双重保险。

    返回: set[str] —— 已链接的目标文件名集合
    """
    linked = set()

    # 方法1：从 URL 中提取文件名（最可靠）
    # 匹配 [text](url)，提取 url 部分
    for m in re.finditer(r'\[([^\]]*)\]\(([^)]+)\)', line):
        url = m.group(2).strip()
        # /wiki/xxx → xxx
        wiki_match = re.match(r'^/wiki/(.+)$', url)
        if wiki_match:
            linked.add(wiki_match.group(1))
        # /docs/xxx → 不加入（不是 wiki 条目）
        # ./xxx → 不加入（相关页面节中的相对路径，不是正文互链）

    # 方法2：从显示文本中匹配术语（补充）
    # 处理 **[text](url)** 加粗在外
    for m in re.finditer(r'\*\*\[([^\]]+)\]\([^)]+\)\*\*', line):
        core = m.group(1).strip().strip('*').strip()
        if core in term_db:
            f, _ = term_db[core]
            linked.add(f)

    # 处理 [text](url)（含 [**text**](url)）
    for m in re.finditer(r'\[([^\]]+)\]\([^)]+\)', line):
        core = m.group(1).strip().strip('*').strip()
        if core in term_db:
            f, _ = term_db[core]
            linked.add(f)

    return linked


def pos_inside_any_span(pos, spans):
    return any(s <= pos < e for s, e in spans)


def is_inside_unclosed_bold_before(line, pos):
    """检查 pos 位置之前的 ** 对数，如果是奇数说明 pos 位于加粗块内部。"""
    prefix = line[:pos]
    return prefix.count('**') % 2 == 1


def process_line_for_terms(line, merged_re, term_db, linked_files, self_fname):
    """在一行正文中替换首次出现的术语为链接（合并正则单遍扫描，约 O(L)）。

    用编译好的合并交替正则一次 finditer 扫出该行所有术语命中，
    取代原先对每个术语分别 re.finditer 扫描整行的 O(T*L) 做法，
    显著降低页面数量增长时的时间复杂度（约 O(n) per line）。

    返回 (new_line, newly_linked_terms_in_this_line)
    """
    processed = line
    link_spans = find_protected_spans(processed)
    linked_now = []
    replacements = []  # (start, end, replacement_text)，按出现顺序收集

    for m in merged_re.finditer(processed):
        start, end = m.span()
        term = m.group(0)

        # 黑名单术语跳过（缩写有歧义，无法确定指向哪个条目）
        if term in TERM_BLACKLIST:
            continue
        # 该命中落在已有 [[text](url)] 链接内部，跳过
        if pos_inside_any_span(start, link_spans):
            continue

        # 判定是否为完整加粗 token（**term**）：前后紧邻各一对星号
        is_whole_bold = (start >= 2 and processed[start-2:start] == '**'
                         and processed[end:end+2] == '**')
        # 若不是完整加粗 token，但位于某个更大加粗块的内部（如 **大术语** 中的"术语"），
        # 属于加粗子串，跳过（原逻辑一致）
        if not is_whole_bold and is_inside_unclosed_bold_before(processed, start):
            continue

        term_fname, term_url = term_db.get(term, (None, None))
        if term_fname is None:
            continue

        # 不链接到自身
        if term_fname == self_fname:
            continue

        # 英文/字母数字术语需要独立左右边界
        if re.match(r'^[A-Za-z0-9_\-]', term):
            left_ok = (start == 0) or (not processed[start-1:start].isalnum())
            right_ok = (end >= len(processed)) or (not processed[end].isalnum())
            if not (left_ok and right_ok):
                continue

        # 按目标文件去重：该文件已链接过（主标题或别名任一）则跳过
        if term_fname in linked_files:
            continue

        # 组合替换文本：加粗星号在外
        if is_whole_bold:
            # start 指向术语文本起点（在 ** 之后），需把整段 **term**（含两侧星号）一并替换，
            # 避免在已有 ** 之上再叠加 [term](url) 而出现 **** 星号叠加
            s, e = start - 2, end + 2
            text = f'**[{term}]({term_url})**'
        else:
            s, e = start, end
            text = f'[{term}]({term_url})'
        replacements.append((s, e, text))
        linked_files.add(term_fname)
        linked_now.append(term)

    # 从右到左应用替换，避免坐标随文本长度变化而错位
    for start, end, text in sorted(replacements, reverse=True):
        processed = processed[:start] + text + processed[end:]

    return processed, linked_now


def process_body(body, term_db, self_fname, file_titles):
    """处理完整正文。

    返回:
        new_body (str):         处理后的正文
        script_added (set[str]): 本次处理真正由脚本新添加的链接术语（不含原有的人工链接）
    """
    lines = body.split('\n')
    result = []
    linked_files = set()       # 按目标文件追踪（同一条目的主标题/别名只需链接一次）
    script_added = set()       # 仅脚本本次新增的术语（用于汇报统计）

    # 术语按长度降序（长短语优先匹配，避免「星球圈」优先于「星球圈低质低俗化」）
    sorted_terms = sorted(term_db.keys(), key=len, reverse=True)
    merged_re = ensure_merged_re(sorted_terms)

    # 节状态：进入「## 相关页面」或「注：本文由AI...」后停止处理
    in_related_or_footer = False

    for line in lines:
        stripped = line.strip()

        # 检测是否进入跳过区域
        if not in_related_or_footer:
            if (stripped.startswith('## 相关页面')
                    or stripped.startswith('### 相关页面')
                    or stripped.startswith('## 参考')
                    or stripped.startswith('## 参考文献')
                    or stripped.startswith('注：本文由AI')
                    or stripped.startswith('注:本文由AI')):
                in_related_or_footer = True

        if in_related_or_footer:
            result.append(line)
            continue

        # 跳过 Markdown 标题行
        if stripped.startswith('#'):
            result.append(line)
            continue

        # ——关键：先登记这一行中已经存在的人工链接——
        # 从 URL 中提取目标文件名，直接加入 linked_files
        # 这样即使链接显示文本是变体（如 [Sw天白](/wiki/TB)），也能正确识别已链接
        already_linked_files = collect_linked_files_from_line(line, term_db)
        linked_files.update(already_linked_files)

        # 真正处理（尝试给首次出现的纯文本术语加链接）
        new_line, line_added_terms = process_line_for_terms(
            line, merged_re, term_db, linked_files, self_fname
        )
        result.append(new_line)

        # 将本行脚本新增的术语记入 script_added
        script_added.update(line_added_terms)

    return '\n'.join(result), script_added


# ============================================================
# Step 3：主流程
# ============================================================

def main():
    apply_mode = '--apply' in sys.argv

    print('=' * 60)
    print('  星球阁 Wiki 词条自索引工具')
    print('  模式:', '写入模式 (--apply)' if apply_mode else '预览模式 (dry-run, 加 --apply 写入)')
    print('=' * 60)

    if not os.path.isdir(WIKI_DIR):
        print(f'\n[ERROR] Wiki 目录不存在：{WIKI_DIR}')
        sys.exit(1)

    # ---- 1. 构建术语数据库 ----
    term_db, file_titles = build_term_database(WIKI_DIR)
    print(f'\n  [OK] 共注册 {len(term_db)} 个术语 / {len(file_titles)} 个条目')

    # ---- 2. 处理每个文件 ----
    print(f'\n[2/3] 扫描正文添加首次出现链接...')
    all_changes = {}   # fname -> list[terms_added]

    md_files = sorted(glob.glob(os.path.join(WIKI_DIR, '*.md')))

    for md_path in md_files:
        fname = os.path.basename(md_path)
        if fname in EXCLUDE_FILES:
            print(f'   [-] 跳过 {fname}（排除列表）')
            continue

        name_noext = os.path.splitext(fname)[0]
        if name_noext not in file_titles:
            print(f'   [-] 跳过 {fname}（无有效 title）')
            continue

        with open(md_path, 'r', encoding='utf-8') as f:
            original = f.read()

        fm, body = extract_frontmatter_and_body(original)
        new_body, added_terms = process_body(body, term_db, name_noext, file_titles)

        # 重组：frontmatter + \n + body
        new_content = fm.rstrip('\n') + '\n\n' + new_body
        # 末尾换行对齐
        if not new_content.endswith('\n') and original.endswith('\n'):
            new_content += '\n'

        if new_content != original:
            all_changes[fname] = sorted(added_terms, key=len, reverse=True)
            # 写入（仅 apply 模式）
            if apply_mode:
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f'   [+] {fname}: 新增 {len(added_terms)} 个链接 -> {added_terms}')
            else:
                print(f'   [*] {fname}: 将新增 {len(added_terms)} 个链接 -> {added_terms}')
        else:
            print(f'   - {fname}: 无变化')

    # ---- 3. 汇总 ----
    print(f'\n[3/3] 完成！')
    if not all_changes:
        print('  本次运行未发现需要补充的链接。')
    else:
        print(f'  共 {"写入" if apply_mode else "预计修改"} {len(all_changes)} 个文件：')
        for fname, terms in all_changes.items():
            print(f'    {fname}: 新增链接 {len(terms)} 个（{", ".join(terms)}）')
        if not apply_mode:
            print('\n  [!] 这是预览模式！')
            choice = input('  输入 apply 执行写入，输入其他任意内容退出：').strip()
            if choice == 'apply':
                print('\n  正在写入文件...')
                for md_path in md_files:
                    fname = os.path.basename(md_path)
                    if fname in EXCLUDE_FILES:
                        continue
                    name_noext = os.path.splitext(fname)[0]
                    if name_noext not in file_titles:
                        continue
                    if fname not in all_changes:
                        continue
                    with open(md_path, 'r', encoding='utf-8') as f:
                        original = f.read()
                    fm, body = extract_frontmatter_and_body(original)
                    new_body, _ = process_body(body, term_db, name_noext, file_titles)
                    new_content = fm.rstrip('\n') + '\n\n' + new_body
                    if not new_content.endswith('\n') and original.endswith('\n'):
                        new_content += '\n'
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                print('  写入完成！')
            else:
                print('  已退出，未写入任何文件。')

if __name__ == '__main__':
    main()
