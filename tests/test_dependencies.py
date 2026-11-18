"""REQ-033 的自动化守卫：离线可用 / 零第三方依赖 / Python 标准库单体。

本文件只做静态证据，不起服务、不联网、不改任何被测文件。三条判据各自可独立报红：

1. ``pyapp/*.py`` 与 ``server.py`` 的每一条 import（含函数体内的延迟 import）
   必须落在 ``sys.stdlib_module_names`` 或内部模块（``pyapp`` / 相对导入）内。
   用 AST 而非正则：正则会把注释、字符串里的 ``import requests`` 当成真导入，
   也会漏掉 ``if x: import requests`` 这种缩进写法。
2. ``index.html`` 的 src/href 不允许指向外部资源。判据是 URL 的 **scheme/netloc**，
   不是全文搜索 ``http://``——favicon 的 data URI 里合法地含有 ``http://www.w3.org``。
3. ``Dockerfile`` 的有效指令里不允许安装 Python 包；注释里的 “不需要 pip install”
   不算，因为注释不是指令。基础镜像 apt 装系统工具（curl/ca-certificates 等）不算，
   apt 装 python*/pip*/pypy*/虚拟环境工具算。

跑法：``python -m unittest tests.test_dependencies -v``
"""

import ast
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PYAPP_DIR = ROOT / 'pyapp'
SERVER_PATH = ROOT / 'server.py'
INDEX_PATH = ROOT / 'index.html'
DOCKERFILE_PATH = ROOT / 'Dockerfile'

# 内部模块根：from pyapp import x / from pyapp.config import y 属于本项目代码，不是第三方。
INTERNAL_ROOTS = frozenset({'pyapp'})
# data: URI 里的 http:// 是命名空间字符串，不是网络请求；mailto/tel 同理不是外链资源。
LOCAL_URI_SCHEMES = frozenset({'', 'data', 'mailto', 'tel'})


# ---------------------------------------------------------------------------
# 判据 1：import 白名单
# ---------------------------------------------------------------------------

def stdlib_names():
    """返回可判定的标准库顶层模块名集合；缺失则不猜，直接判失败。"""
    names = getattr(sys, 'stdlib_module_names', None)
    return frozenset(names) if names is not None else frozenset()


def source_files():
    """被测源码清单：pyapp 下所有 .py + server.py（排除 __pycache__ 等非源码）。"""
    files = sorted(p for p in PYAPP_DIR.rglob('*.py') if '__pycache__' not in p.parts)
    if SERVER_PATH.exists():
        files.append(SERVER_PATH)
    return files


def collect_import_issues(sources, names=None):
    """扫描 AST 中的 Import / ImportFrom / 动态 import，返回违规列表。

    每项为 (label, lineno, name, kind)：
      third_party  —— 顶层模块既不在标准库白名单，也不是内部模块；
      dynamic      —— __import__/importlib.import_module 无法静态证明，一律判违规；
      syntax       —— 源码无法解析，白名单检查失去意义，判违规。
    相对导入（level>0）与 pyapp.* 绝对导入记为 internal，直接放行。
    """
    names = stdlib_names() if names is None else names
    issues = []
    for path in sources:
        label = str(path)
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as exc:
            issues.append((label, 0, str(exc), 'unreadable'))
            continue
        try:
            tree = ast.parse(text, filename=label)
        except SyntaxError as exc:
            issues.append((label, exc.lineno or 0, exc.msg, 'syntax'))
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split('.')[0]
                    if root not in names and root not in INTERNAL_ROOTS:
                        issues.append((label, node.lineno, alias.name, 'third_party'))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # from . import x / from .config import y —— 内部模块
                root = (node.module or '').split('.')[0]
                if root not in names and root not in INTERNAL_ROOTS:
                    issues.append((label, node.lineno, node.module or '<empty>', 'third_party'))
            elif isinstance(node, ast.Call) and _is_dynamic_import(node.func):
                # 动态 import 会把白名单绕过（例如 importlib.import_module('requests')）。
                issues.append((label, node.lineno, ast.dump(node)[:120], 'dynamic'))
    return issues


def _is_dynamic_import(func):
    if isinstance(func, ast.Name):
        return func.id == '__import__'
    if isinstance(func, ast.Attribute) and func.attr in ('import_module', 'reload'):
        # 只认 importlib 链；self.import_module() 之类不是动态导入原语。
        node = func
        while isinstance(node, ast.Attribute):
            node = node.value
        return isinstance(node, ast.Name) and node.id == 'importlib'
    return False


# ---------------------------------------------------------------------------
# 判据 2：index.html 外部资源
# ---------------------------------------------------------------------------

class _ResourceRefs(HTMLParser):
    """收集所有标签的 src/href 原始值及其行号。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key.lower() in ('src', 'href') and value:
                self.refs.append((tag, key, value, self.getpos()[0]))


def is_external_ref(value):
    """按 URL 结构判定外链，避免 data URI 中的 http:// 造成误报。"""
    raw = value.strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme in ('http', 'https'):
        return True
    if not scheme and raw.startswith('//'):
        return True  # 协议相对地址 //cdn.example.com/x.js
    return bool(scheme) and scheme not in LOCAL_URI_SCHEMES


def collect_html_ref_issues(text):
    parser = _ResourceRefs()
    parser.feed(text)
    parser.close()
    return [(tag, key, value, line)
            for tag, key, value, line in parser.refs if is_external_ref(value)]


# ---------------------------------------------------------------------------
# 判据 3：Dockerfile 不装 Python 包
# ---------------------------------------------------------------------------

# 装 Python 包的命令（含常见别名/包装器）。注释行在 collect_docker_issues 中先行剔除。
_PY_INSTALLER_RE = re.compile(
    r'''(?ix)
    \b pip\d*(?:\.\d+)? \s+ install \b
    | \b python(?:[0-9]+(?:\.[0-9]+)?)? \s+ -m \s+ pip \b
    | \b uv \s+ pip \s+ install \b
    | \b (?:poetry|pipenv|conda) \s+ install \b
    | \b python \s+ setup\.py \s+ install \b
    '''
)
_APT_INSTALL_RE = re.compile(r'(?i)\b(?:apt|apt-get)\s+(?:-y\s+)?install\b')
# apt 装的是“系统工具”则放行；装的是 Python 运行时/开发包/虚拟环境工具则违规。
_PY_APT_PACKAGE_RE = re.compile(
    r'''(?ix)
    ^ (?: python [0-9.]* (?:[-_].*)?
        | libpython .*
        | pypy [0-9]*
        | pip [0-9.]*
        | virtualenv | pipenv | poetry )
    $'''
)


def logical_instructions(text):
    """把续行合并成逻辑指令，跳过注释与空行，保留起始物理行号。"""
    result = []
    buf = ''
    start = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith('#')):
            continue
        if not buf:
            start = lineno
        if stripped.endswith('\\'):
            buf += stripped[:-1].rstrip() + ' '
            continue
        buf += stripped
        result.append((start, buf))
        buf = ''
    if buf.strip():
        result.append((start, buf))
    return result


def collect_docker_issues(text):
    """返回 (lineno, instruction_excerpt, reason) 列表。"""
    issues = []
    for lineno, instruction in logical_instructions(text):
        if _PY_INSTALLER_RE.search(instruction):
            issues.append((lineno, instruction[:160], '安装 Python 包的命令'))
            continue
        apt = _APT_INSTALL_RE.search(instruction)
        if apt:
            # 只检查 install 之后的包名，避免把 apt-get update -y 之类误判。
            tail = instruction[apt.end():]
            tokens = re.split(r'[\s&;]+', tail)
            py_pkgs = [t for t in tokens if _PY_APT_PACKAGE_RE.fullmatch(t.strip('-'))]
            if py_pkgs:
                issues.append((lineno, instruction[:160],
                               'apt 安装 Python 相关包：' + ','.join(py_pkgs)))
    return issues


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class DependencyGuardTests(unittest.TestCase):

    def test_import_whitelist(self):
        """REQ-033：pyapp/ 与 server.py 的每个 import 都必须是标准库或内部模块。"""
        files = source_files()
        self.assertGreaterEqual(len(files), 10, '被测源码清单异常缩小，白名单检查可能空跑')
        issues = collect_import_issues(files)
        detail = '\n'.join(
            f'{path}:{line} -> {name} [{kind}]' for path, line, name, kind in issues
        )
        self.assertEqual([], issues, 'REQ-033 违反：发现非标准库/不可证明的导入\n' + detail)

    def test_import_scanner_can_report_red(self):
        """自检：检测器必须能抓到第三方 import、动态 import，且不误伤内部/标准库导入。"""
        bad = '''import requests\nfrom flask import Flask\nif True:\n    import yaml\nimportlib.import_module('requests')\n'''
        # 用临时文件喂给真实扫描器，避免“自检用另一套逻辑”自欺。
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'synthetic.py'
            p.write_text(bad, encoding='utf-8')
            issues = collect_import_issues([p])
        names = {i[2] for i in issues}
        self.assertTrue({'requests', 'flask', 'yaml'}.issubset(names), issues)
        self.assertTrue(any(i[3] == 'dynamic' for i in issues), issues)
        self.assertEqual([], [i for i in issues if i[3] == 'syntax'], issues)

        good = 'import os\nfrom pathlib import Path\nfrom . import APP_VERSION\nfrom pyapp.config import Config\n'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'synthetic.py'
            p.write_text(good, encoding='utf-8')
            self.assertEqual([], collect_import_issues([p]))

    def test_index_html_has_no_external_assets(self):
        """index.html 不引用外部脚本/样式；相对路径 ?v= 与 data URI 合法。"""
        issues = collect_html_ref_issues(INDEX_PATH.read_text(encoding='utf-8'))
        detail = '\n'.join(f'{tag} {key}={value!r} (line {line})' for tag, key, value, line in issues)
        self.assertEqual([], issues, 'index.html 出现外部资源引用（REQ-033）\n' + detail)

    def test_html_detector_can_report_red(self):
        """自检：外链 http/https/协议相对必须报红，data URI 中的 http:// 必须放行。"""
        sample = '''<link rel="stylesheet" href="./css/app.css?v=20261118a">\n'''
        sample += '''<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'></svg>">\n'''
        sample += '''<script src="https://cdn.example.com/app.js"></script>\n'''
        sample += '''<link href="//cdn.example.com/app.css">\n'''
        issues = collect_html_ref_issues(sample)
        self.assertEqual(2, len(issues), issues)
        self.assertEqual(['https://cdn.example.com/app.js', '//cdn.example.com/app.css'],
                         [i[2] for i in issues])

    def test_dockerfile_installs_no_python_packages(self):
        """Dockerfile 有效指令不安装 Python 包；注释与 apt 系统工具不算。"""
        issues = collect_docker_issues(DOCKERFILE_PATH.read_text(encoding='utf-8'))
        detail = '\n'.join(f'line {line}: {text} —— {reason}' for line, text, reason in issues)
        self.assertEqual([], issues, 'Dockerfile 出现 Python 包安装指令（REQ-033）\n' + detail)

    def test_dockerfile_detector_can_report_red(self):
        """自检：pip / apt python3-* 报红；注释与 apt 系统工具放行。"""
        sample = '''# pip install requests  ← 注释不是指令，不计\n'''
        sample += '''RUN apt-get update && apt-get install -y curl ca-certificates\n'''
        sample += '''RUN pip install requests\n'''
        sample += '''RUN apt-get install -y python3-requests curl\n'''
        issues = collect_docker_issues(sample)
        self.assertEqual(2, len(issues), issues)
        self.assertEqual([3, 4], [i[0] for i in issues])
        self.assertIn('安装 Python 包的命令', issues[0][2])
        self.assertIn('python3-requests', issues[1][2])


if __name__ == '__main__':
    unittest.main()
