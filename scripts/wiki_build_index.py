# -*- coding: utf-8 -*-
"""
Wiki 主页条目索引生成器
========================

功能：
  1. 读取 wiki/wiki_catalog.json 中的分类目录（分类 + 条目 slug + 显示名），决定每个条目的分类顺序与显示名
  2. 扫描 wiki/ 目录下所有 .md 条目文件，从 YAML frontmatter 读取 title，首段摘取 140 字摘要
  3. 输出 wiki/wiki_index.json：{categories, entries}，供 wiki_intro.html 在线 fetch 读取
  4. 同步输出 wiki/wiki_index_snapshot.js：把索引挂在 window.WIKI_INDEX_SNAPSHOT 上，
     作为 fetch JSON 失败时的兜底（例如直接 file:// 打开 HTML 时），让页面始终能渲染。

说明：
  - 分类与排序完全由 wiki_catalog.json 决定（手改该文件即可调整分类/顺序/显示名）。
  - wiki_intro.md 仅是旧的"链接索引"页，已不在索引管线中，仅通过 EXCLUDE_SLUGS 防止被收录。

用法：
  python scripts/wiki_build_index.py        # 默认生成 JSON + JS 快照
  python scripts/wiki_build_index.py --dry  # 只打印，不写文件
"""

import os
import re
import json
import sys
from html import escape

# 脚本位于根目录 scripts/ 下，wiki 目录在 scripts 的上一级
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WIKI_DIR = os.path.join(PROJECT_ROOT, 'wiki')
CATALOG_JSON = os.path.join(WIKI_DIR, 'wiki_catalog.json')
OUT_JSON = os.path.join(WIKI_DIR, 'wiki_index.json')
OUT_JS   = os.path.join(WIKI_DIR, 'wiki_index_snapshot.js')

EXCLUDE_SLUGS = {'wiki_intro'}


def extract_frontmatter_and_body(content):
    if not content.startswith('---'):
        return {}, content
    lines = content.split('\n')
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return {}, content
    fm_text = '\n'.join(lines[1:end])
    body = '\n'.join(lines[end + 1:]).lstrip('\n')

    # 极简 YAML 解析（只处理 title/author/keywords 等单行键值，customExtraContent 多行忽略）
    fm = {}
    multi_key = None
    multi_buf = []
    for raw in fm_text.split('\n'):
        line = raw.rstrip()
        # 跳过多行竖线块（如 customExtraContent: |）
        if multi_key is not None:
            if line and not line.startswith(' ') and not line.startswith('\t') and ':' in line:
                fm[multi_key] = '\n'.join(multi_buf).strip()
                multi_key = None
                multi_buf = []
            else:
                multi_buf.append(line)
                continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # aliases 允许「空值 + 缩进 - 列表」或单行逗号分隔，统一走多行收集
        if key == 'aliases' and not val:
            multi_key = key
            multi_buf = []
            continue
        if val == '|' or val == '>':
            multi_key = key
            multi_buf = []
            continue
        # 去引号
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        fm[key] = val
    if multi_key is not None:
        fm[multi_key] = '\n'.join(multi_buf).strip()
    return fm, body


def load_catalog(catalog_path):
    """读取 wiki_catalog.json 的分类目录。

    返回:
      categories: [{id, name, order, entries:[{slug, display}]}]
      slug_to_category: {slug: category_id}
      slug_to_display: {slug: display_name_override}
    """
    with open(catalog_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    categories = []
    slug_to_category = {}
    slug_to_display = {}
    for order, cat in enumerate(data.get('categories', [])):
        cid = cat.get('id') or 'cat_' + re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5]+', '_', cat.get('name', '')).strip('_')
        entries = []
        for e in cat.get('entries', []):
            slug = e.get('slug', '')
            display = e.get('display', slug)
            entries.append({'slug': slug, 'display': display})
            slug_to_category[slug] = cid
            slug_to_display[slug] = display
        categories.append({'id': cid, 'name': cat.get('name', ''), 'order': order, 'entries': entries})
    return categories, slug_to_category, slug_to_display


def extract_summary(body, limit=140):
    """摘取正文第一段有意义的纯文本作为摘要。"""
    # 去掉 frontmatter 后的内容，先按段落拆分
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    for para in paragraphs:
        # 跳过纯标题、纯分隔线、表格、列表占位
        if re.match(r'^(#{1,6}\s|\||---+|-{3,})', para):
            continue
        # 去掉 markdown 标记
        text = para
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'\[(.+?)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            continue
        if len(text) > limit:
            text = text[:limit] + '…'
        return text
    return ''


ALIAS_HINT_PATTERNS = [
    r'[（(](?:又称|亦称|亦作|又名|简称|俗称|缩写|原名|别名|英文|英语)\s*[）)]?[:：]?\s*(.+?)[）)]',
    r'(?:又称|亦称|亦作|又名|简称|俗称|缩写|原名|别名|英文|英语)\s*[为叫称作：:]?\s*(.+?)[，。；,;]',
]
# "账号赋值"模式：抖音ID：**Sw天白** / 群聊中常写作**473wr** / 昵称**XXX** / 早期抖音账号名 **沙雕土星**
# 关键词后面允许「冒号/空格/空」再接一个（也可能带 @/# 等前缀的）粗体账号名。
META_KEY_WORDS = (
    '抖音ID', '抖音账号', '抖音昵称', '抖音号', 'B账号', 'B站ID', 'B站账号', 'B站昵称',
    '账号名', '账号', 'ID', 'id', '群昵称', '群号', '昵称', '早期抖音账号名', '早期账号名',
    '群聊中常写作', '群聊常写作', '现名', '又名', '原名', '亦称', '全称', '缩写', '简称',
    '英文', '英语', '官方',
)
META_ASSIGN_RE = re.compile(
    r'(?:' + '|'.join(re.escape(k) for k in sorted(META_KEY_WORDS, key=len, reverse=True)) + r')'
    r'\s*[:：]?\s*'
    r'(\*\*(.+?)\*\*)'
)


def collect_aliases(body, title):
    """精确抽取别名。

    只在首段定义句中识别「显式别名语义信号」，不兜底扫描任意粗体——
    原兜底分支把首段所有加粗（时间、概念、他人名字等）都当别名候选，
    误报远多于正确命中（如「首个引入恒星演化历程」「2024年7月中旬」「大Q」）。

    识别模式：
      1. 「又称 / 简称 / 缩写 / 英文…」等显式触发词 → 取后续粗体作为别名。
      2. 「账号赋值」模式：XX：**YY**（XX 为 META_KEY_WORDS 之一，如抖音ID、昵称…）→
         把 YY 当别名。用于补偿「（抖音ID：**Sw天白**）」这类元信息括号段——
         此前因括号含 META_HINT 关键词整段被跳过，导致可直接被读者搜索到的 ID 进不了搜索域。
    """
    aliases = []
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    first_para = ''
    for p in paragraphs:
        if not re.match(r'^(#{1,6}\s|\||---+)', p):
            first_para = p
            break
    if not first_para:
        return aliases

    # 1) 从「又称/简称…」片段内的粗体中提取（显式触发词）
    seed_spans = []
    for pat in ALIAS_HINT_PATTERNS:
        for m in re.finditer(pat, first_para):
            seed_spans.append(m.group(1))
    for span in seed_spans:
        for b in re.findall(r'\*\*(.+?)\*\*', span):
            if _is_valid_alias(b, title):
                aliases.append(b)

    # 2) 账号赋值型匹配（无视括号：无论在括号内还是正文里都提取）
    for m in META_ASSIGN_RE.finditer(first_para):
        b = m.group(2)
        if _is_valid_alias(b, title):
            aliases.append(b)

    return _dedup(aliases)[:5]


def _dedup(lst):
    seen = set()
    out = []
    for x in lst:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _parse_aliases(val):
    """解析 frontmatter aliases 字段：单行逗号/顿号/斜杠分隔，或多行「- xxx」列表。

    手动指定的别名不受自动抽取的严格规则限制（长度、字符集等），原样保留。
    """
    if not val:
        return []
    out = []
    for line in val.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('- '):
            line = line[2:].strip()
        for part in re.split(r'[,，、;；/]', line):
            part = part.strip().strip('"\'')
            if part and part not in out:
                out.append(part)
    return out


def _is_valid_alias(b, title):
    if not b or b == title:
        return False
    # 别名不应是标题的子串/超集：标题已包含该词，当作别名没有增量，
    # 还会让搜索命中错误条目（如「星球阁与星球圈的关系」误收「星球阁」）
    if b in title or title in b:
        return False
    if re.search(r'[\[\]\(\)]', b):
        return False
    # 别名不应含引号、括号、逗号等标点残片（之前会先 strip 这些再校验，
    # 导致 "\"（及行星球的本源概念" 被 strip 成 "及行星球的本源概念" 后误判通过）
    if re.search(r'["\'""''「」『』（）()【】《》〈〉，。、；：,;.!！？?]', b):
        return False
    b = b.strip()
    if not b or b == title:
        return False
    if len(b) < 2:
        return False
    if re.fullmatch(r'[0-9约〜~—－\-至年日月时分秒万百千万亿.%％]+', b):
        return False
    # 过滤明显是描述短语：含中文动词/形容词标志或含 "中" "群" "内" "等" 作尾缀的长片段
    if len(b) > 10:
        return False
    # 避免 "（抖音ID" / "（群内全称" / "亦作" 这类句式残留
    if re.fullmatch(r'[（(].{0,10}', b):
        return False
    if b in {'亦作', '又名', '又称', '简称', '缩写', '俗称', '原名'}:
        return False
    # 汉字/字母/数字/点/下划线/短横 组成才合格（必须至少有一个汉字或字母）
    if not re.fullmatch(r'[\u4e00-\u9fa5A-Za-z0-9·・._\-]+', b):
        return False
    if not re.search(r'[\u4e00-\u9fa5A-Za-z]', b):
        return False
    return True


def build_index():
    categories, slug_to_cat, slug_to_display = load_catalog(CATALOG_JSON)

    entries = []
    md_files = sorted([f for f in os.listdir(WIKI_DIR) if f.endswith('.md')])
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
        fm, body = extract_frontmatter_and_body(content)
        title = fm.get('title') or slug_to_display.get(slug) or slug
        author = fm.get('author', '')
        summary = extract_summary(body)
        display_name = slug_to_display.get(slug)
        # 别名来源：frontmatter 手动指定 + 正文自动抽取。
        # 不再把 display_name 当别名——display 本身已是 entries 的独立字段，
        # 前端搜索 buildSearchable 已覆盖它；若再把 display 塞进 aliases，
        # 像 wikiRule（display="Wiki编写规范"）这类条目会出现"别名=展示名"的冗余。
        aliases = _parse_aliases(fm.get('aliases', '')) + collect_aliases(body, title)
        # 若别名与最终展示名（display 优先，否则 title）相同则视为无效数据剔除
        shown_name = display_name or title
        aliases = [a for a in aliases if a != shown_name]
        aliases = _dedup(aliases)[:5]
        category = slug_to_cat.get(slug, '')
        entries.append({
            'slug': slug,
            'title': title,
            'display': display_name or title,
            'category': category,
            'author': author,
            'summary': summary,
            'aliases': aliases,
            'url': f'./{slug}',
        })

    # 按照 wiki_catalog.json 中的分类顺序对 entries 排序（未分类的排到最后）
    rank = {}
    for cidx, cat in enumerate(categories):
        for eidx, e in enumerate(cat['entries']):
            rank[e['slug']] = (cidx, eidx)
    entries.sort(key=lambda x: rank.get(x['slug'], (9999, 9999)))

    result = {
        'generated_at_hint': '请运行 scripts/wiki_build_index.py 重新生成此文件',
        'category_order': [c['id'] for c in categories],
        'categories': {c['id']: {'name': c['name'], 'order': c['order']} for c in categories},
        'entries': entries,
    }
    return result


def main():
    data = build_index()
    if '--dry' in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print(f'\n[INFO] 共 {len(data["entries"])} 条，{len(data["categories"])} 个分类')
        return

    # 1) JSON 版本：给前端 fetch 用（主路径）
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'[OK] 已生成: {OUT_JSON}')
    print(f'     条目数: {len(data["entries"])}, 分类数: {len(data["categories"])}')

    # 2) JS 快照版本：兜底路径。把数据挂到全局变量，不依赖 fetch / 同源策略。
    #    文件末尾附加 CRC 字节数和条目数，便于肉眼快速比对版本。
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    size = len(payload.encode('utf-8'))
    js = (
        '// Wiki 条目快照（自动生成，勿手改。运行 python scripts/wiki_build_index.py 重新生成）\n'
        '// 此脚本会在 wiki_intro.html 中被 <script src="./wiki_index_snapshot.js"> 加载，\n'
        '// 作为 fetch(./wiki_index.json) 失败时的降级数据源，确保即便在 file:// 直开\n'
        '// 或某些静态托管缺失 JSON mime 类型的情况下也能渲染条目。\n'
        '(function () {\n'
        '  try {\n'
        '    window.WIKI_INDEX_SNAPSHOT = ' + payload + ';\n'
        '  } catch (e) {\n'
        '    console.error("[wiki] snapshot failed to parse", e);\n'
        '  }\n'
        '})();\n'
        '// meta: entries=' + str(len(data['entries'])) +
        ' categories=' + str(len(data['categories'])) +
        ' size=' + str(size) + 'B\n'
    )
    with open(OUT_JS, 'w', encoding='utf-8') as f:
        f.write(js)
    print(f'[OK] 已生成: {OUT_JS}')
    print(f'     大小: {size}B ({len(data["entries"])} 条 / {len(data["categories"])} 类)')


if __name__ == '__main__':
    main()
