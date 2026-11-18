"""docs/process/*.md 表格列数一致性守卫（零依赖、标准库 unittest）。

背景：旧检测器把 ``is_sep('')`` 判为 True，空行被当成分隔行，误报 34 处；
另有两种“解释”（含全角｜、部分行无结尾竖线）从未核实，本文件不采用。

本文件的口径，全部写死在代码里、可由自检用例证明：
  * 表格块 = 连续的、去掉每层 ``>`` 引用前缀后以 ``|`` 开头的行；空行直接结束块。
  * 分隔行 = 每个单元格都完整匹配 ``^:?-{3,}:?$``；空串/纯引用符号一律不是分隔行。
  * ``` / ~~~ 代码围栏内的管道行不算表格。
  * 单元格切分时忽略 ``\\|`` 转义与反引号代码段内的 ``|``（否则误报成列数不一致）。
  * 三个判据独立实现：①逐行 split 计数（期望来自表头）②分隔行横杠计数 +
    分隔符计数法（期望来自正则）③引用块归一化去重 + 正则切分（期望来自简单 split）。
    三者结果必须完全相等；一方为 0、另一方非 0 时显式抛“检测器自身不一致”。

历史基线说明：QA 修正后曾报“56 块 / 列数不一致 0”。实测 56 = 10_CR(27) +
11_需求跟踪矩阵(29) 两份账号文档之和，并非 docs/process/*.md 全量（本次实测 830 块）。
本测试不硬编码这些数字为断言，而是打印诊断值；发现的新缺陷必须凭证据登记，
不允许为让测试变绿而静默放宽判据。

跑法：``python -m unittest tests.test_markdown_tables -v``
"""

import re
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / 'docs' / 'process'
MD_FILES = sorted(DOCS_DIR.glob('*.md'))
BASELINE_FILES = [DOCS_DIR / '10_CR-2026-001_账号体系变更.md',
                  DOCS_DIR / '11_需求跟踪矩阵.md']

SEP_CELL_RE = re.compile(r'^:?-{3,}:?$')
QUOTE_RE = re.compile(r'^\s*>\s?')
FENCE_RE = re.compile(r'^\s*(`{3,}|~{3,})')

# 已确认的真实文档缺陷（2026-11-18 实测），逐条附证据；只豁免这些，不豁免“新出现的”。
# 键：文件名 + 行文本中的稳定特征串；值：缺陷说明。
KNOWN_DEFECTS = (
    ('01_现状评估与改善路线图.md', 'P0-8',
     '第47行行末多出一段文字并多一个 |，该行 5 列而表头 4 列'),
    ('04_TDS_技术设计说明书-update 11.18.md', '合并的后果',
     '第1485行只有 2 列，表头 3 列（缺一个单元格）'),
    ('11_需求跟踪矩阵.md', '首版（产品经理许清楚）',
     '§7 变更记录 v1.0 行 2 列，表头 4 列'),
    ('11_需求跟踪矩阵.md', '| **v1.1** | 2026-11-18 |',
     '§7 变更记录 v1.1 行 6 列，表头 4 列'),
)


@dataclass(frozen=True)
class Block:
    path: Path
    line: int                 # 块首物理行号（1-based）
    rows: tuple               # ((lineno, text), ...)，text 已去掉引用前缀


# ---------------------------------------------------------------------------
# 块提取
# ---------------------------------------------------------------------------

def strip_quote(line):
    """逐层去掉 > 引用前缀；普通行原样返回。空行仍为空串，绝不变成分隔行。"""
    text = line
    while True:
        match = QUOTE_RE.match(text)
        if not match:
            return text.strip()
        text = text[match.end():]


def is_table_row(line):
    return strip_quote(line).startswith('|')


def is_separator_row(line):
    """修正旧缺陷：'' 与 '>' 都不可能是分隔行。"""
    body = strip_quote(line)
    if not body.startswith('|'):
        return False
    cells = [cell.strip() for cell in body.strip('|').split('|')]
    return bool(cells) and all(SEP_CELL_RE.fullmatch(cell) for cell in cells)


def scan_blocks(files):
    """扫描文件列表，返回只含“至少一条分隔行”的表格块；跳过代码围栏。"""
    blocks = []
    for path in files:
        lines = path.read_text(encoding='utf-8').splitlines()
        i = 0
        fence = None
        while i < len(lines):
            fence_match = FENCE_RE.match(lines[i])
            if fence_match:
                marker, size = fence_match.group(1)[0], len(fence_match.group(1))
                if fence is None:
                    fence = (marker, size)
                elif fence[0] == marker and size >= fence[1]:
                    fence = None
                i += 1
                continue
            if fence is not None or not is_table_row(lines[i]):
                i += 1
                continue
            start = i + 1
            rows = []
            while i < len(lines) and is_table_row(lines[i]) and not FENCE_RE.match(lines[i]):
                rows.append((i + 1, strip_quote(lines[i])))
                i += 1
            if any(is_separator_row(text) for _, text in rows):
                blocks.append(Block(path, start, tuple(rows)))
    return blocks


def canonical_rows(rows):
    """引用块归一化：连续重复行只留首次出现（身份用首次行号，保证三判据可比）。"""
    result = []
    previous = None
    for lineno, text in rows:
        normalized = text.strip()
        if normalized == previous:
            continue
        result.append((lineno, normalized))
        previous = normalized
    return result


# ---------------------------------------------------------------------------
# 三种独立切分/计数实现
# ---------------------------------------------------------------------------

def split_cells_loop(body):
    """判据①：逐字符扫描，处理转义竖线与反引号代码段（含多反引号）。"""
    cells, current = [], []
    i, ticks = 0, 0
    while i < len(body):
        char = body[i]
        if char == '\\' and i + 1 < len(body) and body[i + 1] == '|':
            current.append('|')
            i += 2
            continue
        if char == '`':
            j = i
            while j < len(body) and body[j] == '`':
                j += 1
            run = j - i
            if ticks == 0:
                ticks = run
            elif ticks == run:
                ticks = 0
            current.append(body[i:j])
            i = j
            continue
        if char == '|' and ticks == 0:
            cells.append(''.join(current).strip())
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    cells.append(''.join(current).strip())
    # 只去掉“边框管道”产生的那一个空段；`| | a | b |` 的首列是显式空单元格，必须保留。
    if cells and cells[0] == '':
        cells.pop(0)
    if cells and cells[-1] == '':
        cells.pop()
    return cells


def cell_count_by_split(body):
    """判据①的计数出口：上面逐字符切分的单元格个数。"""
    return len(split_cells_loop(body))


def cell_count_by_delimiters(body):
    """判据②：先正则遮蔽代码段/转义，再数分隔符个数推列数（不切分内容）。"""
    masked = re.sub(r'\\\|', '\u0000', body)
    masked = re.sub(r'`+[^`]*`+', lambda m: '\u0001' * len(m.group()), masked)
    pipes = masked.count('|')
    stripped = masked.strip()
    leading, trailing = stripped.startswith('|'), stripped.endswith('|')
    if leading and trailing:
        count = pipes - 1
    elif leading or trailing:
        count = pipes
    else:
        count = pipes + 1 if pipes else 0
    return max(count, 0)


def cell_count_by_regex(body):
    """判据③：正则切分（与①②不同的实现路径），用于引用块归一化后的复核。"""
    masked = re.sub(r'\\\|', '\u0000', body)
    masked = re.sub(r'`[^`]*`', lambda m: '\u0002' * len(m.group()), masked)
    parts = re.split(r'\|', masked)
    if parts and parts[0].strip() == '':
        parts = parts[1:]
    if parts and parts[-1].strip() == '':
        parts = parts[:-1]
    return len(parts)


def _expected_from_header(rows, separator, counter):
    """判据①的期望值来自表头行（与判据②③的分隔行来源不同，便于交叉发现偏差）。"""
    for _, text in rows:
        if not is_separator_row(text):
            return len(split_cells_loop(text))
    return len(split_cells_loop(separator))


def _expected_from_separator_regex(rows, separator, counter):
    # 只数分隔行里的横杠段：`|---|:---:|` -> 2，与切分单元格的实现无关。
    return len(re.findall(r':?-{3,}:?', separator))


def _expected_from_separator_split(rows, separator, counter):
    return len([cell for cell in separator.strip().strip('|').split('|') if cell.strip()])


def _issues(blocks, counter, expected):
    found = set()
    for block in blocks:
        rows = canonical_rows(block.rows)
        separators = [text for _, text in rows if is_separator_row(text)]
        if not separators:
            continue
        try:
            wanted = expected(rows, separators[0], counter)
        except (ValueError, IndexError):
            continue
        for lineno, text in rows:
            if is_separator_row(text):
                continue
            if counter(text) != wanted:
                found.add((block.path.name, lineno, text))
    return found


def check_by_line_split(blocks):
    return _issues(blocks, cell_count_by_split, _expected_from_header)


def check_by_separator(blocks):
    return _issues(blocks, cell_count_by_delimiters, _expected_from_separator_regex)


def check_by_quote_normalization(blocks):
    return _issues(blocks, cell_count_by_regex, _expected_from_separator_split)


def run_checkers(blocks):
    return {
        '逐行split': check_by_line_split(blocks),
        '分隔行推导': check_by_separator(blocks),
        '引用块去重': check_by_quote_normalization(blocks),
    }


def cross_validate(results):
    """三判据结果必须一致；不一致时显式报“检测器自身不一致”，绝不静默通过。"""
    sets = {name: set(value) for name, value in results.items()}
    first = next(iter(sets.values()))
    if any(value != first for value in sets.values()):
        empty = [name for name, value in sets.items() if not value]
        nonempty = [name for name, value in sets.items() if value]
        sample = {name: sorted(value)[:3] for name, value in sets.items()}
        raise AssertionError(
            '检测器自身不一致：报 0 的判据=%s，报非 0 的判据=%s；样本=%s'
            % (empty, nonempty, sample)
        )
    return first


def is_known(path_name, text):
    return any(path_name == name and needle in text for name, needle, _ in KNOWN_DEFECTS)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class MarkdownTableTests(unittest.TestCase):

    def test_blank_line_is_never_a_separator(self):
        """旧缺陷回归：空串/空行/纯引用符号都不能被判成分隔行。"""
        for candidate in ('', '   ', '>', '>   ', '\t'):
            self.assertFalse(is_separator_row(candidate), repr(candidate))
        self.assertTrue(is_separator_row('|---|---|'))
        self.assertTrue(is_separator_row('> | :--- | ---: |'))

        text = '| A | B |\n|---|---|\n| 1 | 2 |\n\n| X | Y | Z |\n'
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.md'
            path.write_text(text, encoding='utf-8')
            blocks = scan_blocks([path])
        self.assertEqual(1, len(blocks))
        self.assertEqual(3, len(blocks[0].rows))

    def test_blockquote_table_is_scanned(self):
        """引用块表格必须被识别为一整块，且三判据判为合格。"""
        text = '> | 项 | 值 |\n> |---|---|\n> | a | 1 |\n> | b | 2 |\n'
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'quoted.md'
            path.write_text(text, encoding='utf-8')
            blocks = scan_blocks([path])
        self.assertEqual(1, len(blocks))
        self.assertEqual(4, len(blocks[0].rows))
        self.assertEqual(set(), cross_validate(run_checkers(blocks)))

    def test_detectors_report_red_on_injected_defect(self):
        """自检：故意注入 2 列表头 + 3 列数据行，三判据必须一致报红。"""
        text = '| A | B |\n|---|---|\n| 1 | 2 | 3 |\n'
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.md'
            path.write_text(text, encoding='utf-8')
            blocks = scan_blocks([path])
        results = run_checkers(blocks)
        bad = cross_validate(results)
        self.assertEqual({('bad.md', 3, '| 1 | 2 | 3 |')}, bad)
        # 再自检“不一致”分支：人为让一个判据为空，必须显式抛指定文案。
        broken = dict(results)
        broken['引用块去重'] = set()
        with self.assertRaisesRegex(AssertionError, '检测器自身不一致'):
            cross_validate(broken)

    def test_real_corpus_three_judges_agree(self):
        """真实语料：三判据结果必须完全一致（含都为 0 的情况）。"""
        self.assertTrue(MD_FILES, 'docs/process/*.md 没有匹配文件')
        cross_validate(run_checkers(scan_blocks(MD_FILES)))

    def test_real_corpus_only_known_defects(self):
        """真实语料：列数不一致只能是已登记并附证据的缺陷；新增即失败。"""
        bad = cross_validate(run_checkers(scan_blocks(MD_FILES)))
        unknown = [(name, line, text[:100]) for name, line, text in sorted(bad)
                   if not is_known(name, text)]
        detail = '\n'.join(f'{n}:{l} {t}' for n, l, t in unknown)
        self.assertEqual([], unknown, '出现未登记的表格列数不一致（先查证据，再决定修文档还是修检测器）\n' + detail)

    def test_inventory_diagnostic(self):
        """打印可复核的盘点数字；数字本身不作为断言，避免把历史口径当真理。"""
        blocks = scan_blocks(MD_FILES)
        bad = cross_validate(run_checkers(blocks))
        bad_blocks = len({(name, line) for name, line, _ in bad})
        baseline_blocks = scan_blocks([p for p in BASELINE_FILES if p.exists()])
        baseline_bad = cross_validate(run_checkers(baseline_blocks))
        print('\n[诊断] docs/process/*.md：文件 %d，表格块 %d，不一致行 %d（分布在 %d 块）'
              % (len(MD_FILES), len(blocks), len(bad), bad_blocks))
        print('[诊断] 历史 56 块口径（10_CR + 11_RTM）：表格块 %d，不一致行 %d'
              % (len(baseline_blocks), len(baseline_bad)))
        self.assertGreater(len(blocks), 0)


if __name__ == '__main__':
    unittest.main()
