# -*- coding: utf-8 -*-
"""
Wiki 维护一条龙工作流（workflow.py）
=====================================

按正确顺序串联 5 个 Wiki 维护脚本，免去记命令的烦恼。

执行顺序（默认完整版）：
  Step 1  wiki_catalog_add.py       ← 交互式，把新出现的 .md 条目录入 wiki_catalog.json（可逐个跳过）
  Step 2  wiki_normalize_links.py   ← 把旧格式 `./slug` 内链统一规范化为 `/wiki/slug`，并校验目标存在
  Step 3  wiki_deadlink_fix.py      ← ★ 交互式断链修复操作台：展示所有死链、搜索候选、批量修、解除链接
  Step 4  wiki_auto_link.py         ← 给正文中首次出现的专有名词自动补互链（首次出现加链接）
  Step 5  wiki_build_index.py       ← 依据 catalog + .md 内容重新生成 wiki_index.json + snapshot 供主页渲染

两种模式：
  python scripts/workflow.py            # 完整流程（含 Step 1 交互，新词条首次入库时使用）
  python scripts/workflow.py --quick    # 快速模式：跳过 Step 1 catalog 交互（日常刷新链接/索引用）
  python scripts/workflow.py --dry      # 预览模式：Step 2/3/4 走 dry-run（Step 3/4 只读不操作，
                                         Step 5 走 --dry 只打印），**全程不写文件**
                                         （可与 --quick 叠加：--quick --dry）

中途任何一步子脚本异常退出（return code ≠ 0），整条流水线立即中止并报错，避免错误状态继续传导。
"""

import os
import sys
import subprocess


# ============================================================
# Windows 控制台兼容：开启 ANSI VT 颜色 + UTF-8 代码页
# ============================================================

def _setup_windows_console():
    """在 Windows CMD / PowerShell 中做两件事：
      1) 把控制台代码页切换到 65001 (UTF-8)，避免 ╔═╗ 框线和中文变问号乱码；
      2) 显式开启 ENABLE_VIRTUAL_TERMINAL_PROCESSING，让 ANSI 颜色真正渲染（
         否则子脚本的 \033[91m 会直接显示为字面字符串或被吞掉不生效）。
    非 Windows 平台啥也不做，直接返回。
    """
    if os.name != 'nt' or sys.platform != 'win32':
        return

    # --- (A) UTF-8 代码页 ---
    # 子进程继承父进程的控制台代码页，所以在主 workflow 里设一次即可生效到 5 个子脚本
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # SetConsoleOutputCP(65001 = UTF-8)
        if hasattr(kernel32, 'SetConsoleOutputCP'):
            kernel32.SetConsoleOutputCP(ctypes.c_uint(65001))
        # SetConsoleCP(65001) —— 输入代码页也顺带设成 UTF-8
        if hasattr(kernel32, 'SetConsoleCP'):
            kernel32.SetConsoleCP(ctypes.c_uint(65001))
    except Exception:
        # 再兜一层：os.system('chcp 65001 >nul')（旧版 Windows）
        try:
            os.system('chcp 65001 >nul 2>nul')
        except Exception:
            pass

    # --- (B) 先跑一次 os.system('') 老技巧，部分版本 CMD/PowerShell 能触发 VT ---
    try:
        os.system('')
    except Exception:
        pass

    # --- (C) 显式开 VT 模式（ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004） ---
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        STD_OUTPUT_HANDLE = wintypes.DWORD(-11)
        STD_ERROR_HANDLE  = wintypes.DWORD(-12)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        ENABLE_PROCESSED_OUTPUT = 0x0001

        for std_id in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            h = kernel32.GetStdHandle(std_id)
            if h in (None, 0, -1):
                continue
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)) == 0:
                continue  # 可能是重定向到文件 / 管道，不是真实 console，跳过
            new_mode = wintypes.DWORD(
                mode.value
                | ENABLE_PROCESSED_OUTPUT
                | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            kernel32.SetConsoleMode(h, new_mode)
    except Exception:
        pass


# ========= 路径解析 =========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

WORKFLOW_PY = os.path.abspath(__file__)
CATALOG_PY    = os.path.join(SCRIPT_DIR, 'wiki_catalog_add.py')
NORMALIZE_PY  = os.path.join(SCRIPT_DIR, 'wiki_normalize_links.py')
DEADLINK_PY   = os.path.join(SCRIPT_DIR, 'wiki_deadlink_fix.py')
AUTOLINK_PY   = os.path.join(SCRIPT_DIR, 'wiki_auto_link.py')
BUILDINDEX_PY = os.path.join(SCRIPT_DIR, 'wiki_build_index.py')


# ============================================================
# 子脚本运行器
# ============================================================

def run_step(step_num, step_name, script_path, extra_argv):
    """运行一个子脚本。stdout/stderr 继承当前终端以便交互查看。

    extra_argv: list[str]，附加的命令行参数（如 ['--apply']、['--dry'] 等）。
    子脚本失败时抛出 SystemExit 终止 workflow。
    """
    print()
    print('=' * 64)
    print(f'  Workflow Step {step_num} · {step_name}')
    print(f'  命令  : python "{os.path.basename(script_path)}" {" ".join(extra_argv)}')
    print(f'  脚本  : {os.path.relpath(script_path, PROJECT_ROOT)}')
    print('=' * 64)
    sys.stdout.flush()

    argv = [sys.executable, script_path] + extra_argv
    # 统一以项目根目录为 CWD，与单独调用时的约定一致
    result = subprocess.run(argv, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print()
        print('❗ ' + '=' * 58 + ' ❗')
        print(f'  Step {step_num} 失败！子脚本返回码 = {result.returncode}')
        print(f'  失败位置：{step_name} （{os.path.basename(script_path)}）')
        print('  流水线中止，请先排查上面错误再重跑。')
        print('❗ ' + '=' * 58 + ' ❗')
        sys.exit(result.returncode)
    else:
        print(f'\n✔ Step {step_num} 完成：{step_name}')


# ============================================================
# 主流程
# ============================================================

def main():
    # --- 第一件事：Windows 控制台兼容（UTF-8 + ANSI VT 颜色） ----------------------
    # 必须在任何 print 之前调用，否则 ╔═╗ 框线、中文、ANSI 颜色都会乱码。
    # 所有子进程会继承：UTF-8 代码页 + 父进程已打开的 VT 模式（子脚本也会各自再开一次，双保险）。
    _setup_windows_console()

    quick_mode = '--quick' in sys.argv or '-q' in sys.argv
    dry_mode   = '--dry'   in sys.argv or '-d' in sys.argv

    # 先校验所有 5 个脚本文件都在，以免跑到中间缺文件
    required = [
        ('wiki_catalog_add.py',      CATALOG_PY),
        ('wiki_normalize_links.py',  NORMALIZE_PY),
        ('wiki_deadlink_fix.py',     DEADLINK_PY),
        ('wiki_auto_link.py',        AUTOLINK_PY),
        ('wiki_build_index.py',      BUILDINDEX_PY),
    ]
    missing = [name for name, p in required if not os.path.isfile(p)]
    if missing:
        print('[ERROR] 以下 workflow 依赖脚本缺失：')
        for name in missing:
            print(f'   - {name}  （期望在 scripts/ 目录下）')
        sys.exit(2)

    # ========= 头部信息 =========
    print()
    print('╔' + '═' * 62 + '╗')
    print('║          ★  星球阁 Wiki 维护 · 一条龙工作流  ★          ║')
    print('╚' + '═' * 62 + '╝')
    print(f'  模式 : {"快速模式 (--quick，跳过 catalog 交互)" if quick_mode else "完整模式（含新词条录入）"}')
    print(f'  写入 : {"预览模式 (--dry，全部步骤不写文件)" if dry_mode else "实际写入 (确认后会修改 wiki/ 下文件)"}')
    print(f'  步骤 : 共 5 步（{"跳过 Step 1，" if quick_mode else ""}Step 2-5 依次执行）')
    print(f'  根目录: {PROJECT_ROOT}')

    if not dry_mode:
        # 仅在非 dry 且非 quick（即完整模式且要写）时提示一次
        print()
        print('[提示] 正式写入前，各脚本仍会在其自身流程内给出预览并等待你确认。')
        print('       若你只想看会发生什么而不改动任何文件，请使用 --dry 参数。')

    step_counter = 0

    # ---- Step 1. catalog 录入（仅完整模式；本身就是交互式，无 apply/dry 概念）----
    if not quick_mode:
        step_counter += 1
        # catalog_add 是纯交互式，无 apply/dry 参数，直接跑即可
        run_step(
            step_counter,
            '录入新词条到 wiki_catalog.json（交互式，每一项可按 s 跳过）',
            CATALOG_PY,
            []
        )

    # ---- Step 2. 内链规范化 ----
    step_counter += 1
    normalize_argv = [] if dry_mode else ['--apply']
    run_step(
        step_counter,
        '内链格式规范化（./slug → /wiki/slug，并校验目标存在）',
        NORMALIZE_PY,
        normalize_argv
    )

    # ---- Step 3. 交互式断链修复操作台 ★新增 ----
    step_counter += 1
    # --dry 时：只读模式（只列出断链清单，不操作不写）
    # 正常时 ：进入交互式操作台逐条修复（末尾会单独确认写回）
    deadlink_argv = ['--dry'] if dry_mode else []
    run_step(
        step_counter,
        '断链 / 死链修复操作台（展示所有断链 + 搜索候选 + 批量同类处置）',
        DEADLINK_PY,
        deadlink_argv
    )

    # ---- Step 4. 自动加首次出现互链 ----
    step_counter += 1
    autolink_argv = [] if dry_mode else ['--apply']
    run_step(
        step_counter,
        '正文首次出现术语自动加互链（长短语优先 / 已链接不重复）',
        AUTOLINK_PY,
        autolink_argv
    )

    # ---- Step 5. 重新生成索引 ----
    step_counter += 1
    build_argv = ['--dry'] if dry_mode else []
    run_step(
        step_counter,
        '重新生成 wiki_index.json + wiki_index_snapshot.js（主页数据源）',
        BUILDINDEX_PY,
        build_argv
    )

    # ========= 全部完成 =========
    print()
    print('🎉 ' + '=' * 58 + ' 🎉')
    print('  Wiki 一条龙工作流全部执行完毕！')
    print(f'  共完成 {step_counter} 个步骤。')
    print()
    print('  建议接下来做的事：')
    print('    1. git diff / 用 IDE 查看改动，确认链接修改符合预期')
    print('    2. 若使用 Jekyll 本地预览：bundle exec jekyll serve 后抽查几个页面')
    print('    3. 重点核对 workflow 输出中任何 [warn] / [!] 提示（如疑似断链）')
    print('🎉 ' + '=' * 58 + ' 🎉')


if __name__ == '__main__':
    main()
