"""LaTeX / KaTeX 校验工具：定界符配对 + 裸命令检测 + KaTeX 沙箱渲染。

复用自:
  - 1_Data/06_internvl35_jsonl/math_data_v17/260416_delimiter_experiment/check_delimiter_quality_v2.py
  - 1_Data/06_internvl35_jsonl/math_data_v17/260416_after_human_check/latex_check.py

精简版：仅暴露 scan_delimiters / marker_matches / in_spans / need_render_content / KatexValidator。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Tuple, Optional


# ---------- 定界符 + 裸命令检测 ----------

DELIM_TOK_RE = re.compile(r"(?<!\\)\$\$|(?<!\\)\$|\\\(|\\\)|\\\[|\\\]")
OPEN_CLOSE = {"$": "$", "$$": "$$", r"\(": r"\)", r"\[": r"\]"}

LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+(?:\*|\b)")
LATEX_POWSUB_RE = re.compile(
    r"(?<![\\\w])(?:[A-Za-z0-9\)\]]\s*\^\s*[A-Za-z0-9\(\[]"
    r"|[A-Za-z0-9\)\]]\s*_\s*[A-Za-z0-9\(\[])"
)
LATEX_FRAC_RE = re.compile(
    r"(?<![\\\w])(?:\d+\s*/\s*\d+|[A-Za-z]\s*/\s*[A-Za-z0-9]|[A-Za-z0-9]\s*/\s*[A-Za-z])"
)

RENDER_HINT_RE = re.compile(
    r"(\\[A-Za-z]+|[_\^{}]|[=<>±×÷]|\d\s*[+\-*/=<>]\s*\d|[A-Za-z]\s*[+\-*/=<>]\s*[A-Za-z0-9])"
)
SIMPLE_ATOM_RE = re.compile(r"^[A-Za-z0-9]+$")


@dataclass
class PairIssue:
    issue_type: str  # cross_pair / orphan_close / unclosed_open
    detail: str
    pos: int


def scan_delimiters(text: str) -> Tuple[List[Tuple[int, int, str]], List[PairIssue]]:
    """扫描定界符，返回 (合法成对区间, 配对问题列表)。"""
    stack: List[Tuple[str, int, int]] = []
    valid_spans: List[Tuple[int, int, str]] = []
    issues: List[PairIssue] = []

    for m in DELIM_TOK_RE.finditer(text):
        tok = m.group(0)
        s, e = m.start(), m.end()

        if tok in ("$", "$$"):
            if stack and stack[-1][0] == tok:
                otok, os_, _oe = stack.pop()
                valid_spans.append((os_, e, otok))
            else:
                stack.append((tok, s, e))
            continue

        if tok in (r"\(", r"\["):
            stack.append((tok, s, e))
            continue

        if not stack:
            issues.append(PairIssue("orphan_close", tok, s))
            continue

        otok, os_, _oe = stack.pop()
        expected = OPEN_CLOSE.get(otok)
        if tok != expected:
            issues.append(PairIssue("cross_pair", f"{otok} -> {tok}", s))
            continue

        valid_spans.append((os_, e, otok))

    for otok, os_, _oe in stack:
        issues.append(PairIssue("unclosed_open", otok, os_))

    valid_spans.sort(key=lambda x: x[0])
    return valid_spans, issues


def in_spans(s: int, e: int, spans: List[Tuple[int, int, str]]) -> bool:
    for a, b, _ in spans:
        if s >= a and e <= b:
            return True
    return False


# 英语/语文填空题里的下划线模式：t_ _cher / p_p_l / b_s_de / sch_ _l
# 特征：(a) 上下文连续出现多个 _ ; 或 (b) 包含 "_ _" / "_  _" / "_ a"（letter 前空格）
# 这种 token 不应被当作 LaTeX 下标报错。
ENGLISH_FILL_BLANK_RE = re.compile(
    r"[A-Za-z]?[_\s]*_(?:[\s_]*[A-Za-z]){0,4}[_\s]*_[A-Za-z\s]"
)


def _is_english_fill_blank(text: str, pos: int, end: int) -> bool:
    """判断 powsub 匹配是否属于英语填空模式。

    取匹配前后 ±8 字符窗口；若窗口内存在 >=2 个 `_` 间隔字母/空格，
    或匹配本身的右侧紧跟着 `_`（连续下划线），视为英语填空。
    """
    lo = max(0, pos - 8)
    hi = min(len(text), end + 8)
    ctx = text[lo:hi]
    if ctx.count("_") >= 2:
        return True
    if ENGLISH_FILL_BLANK_RE.search(ctx):
        return True
    return False


def marker_matches(text: str):
    """扫描可能需要 math 渲染的 token: \\cmd / 上下标 / 分式。"""
    for m in LATEX_CMD_RE.finditer(text):
        cmd = m.group(0)
        if cmd in (r"\n", r"\r", r"\t"):
            continue
        yield ("cmd", m.start(), m.end(), cmd)
    for m in LATEX_POWSUB_RE.finditer(text):
        # 跳过英语/语文填空的下划线模式
        if "_" in m.group(0) and _is_english_fill_blank(text, m.start(), m.end()):
            continue
        yield ("powsub", m.start(), m.end(), m.group(0))
    for m in LATEX_FRAC_RE.finditer(text):
        yield ("frac", m.start(), m.end(), m.group(0))


def need_render_content(inner: str) -> bool:
    s = inner.strip()
    if not s:
        return False
    if SIMPLE_ATOM_RE.fullmatch(s) and len(s) <= 3:
        return False
    return bool(RENDER_HINT_RE.search(s))


# ---------- KaTeX 沙箱渲染 ----------

class KatexValidator:
    """用 py_mini_racer 在 V8 沙箱里跑 KaTeX 0.16.9 + mhchem 渲染。

    依赖：py-mini-racer、requests。首次运行会下载 katex.min.js / mhchem.min.js
    到 ``folder``（默认 ./katex_assets）。
    """

    BASE_URL = "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/"
    FILES = {"katex": "katex.min.js", "mhchem": "contrib/mhchem.min.js"}

    def __init__(self, folder: str = "katex_assets"):
        try:
            from py_mini_racer import MiniRacer  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "py-mini-racer 未安装；如需 KaTeX 校验请 `pip install py-mini-racer`，"
                "或加 --no-katex 跳过。"
            ) from e

        self.folder = folder
        self._prepare_env()
        self.ctx = MiniRacer()
        self._load_js_files()

        # 分割定界符的正则（与前端 KaTeX-auto-render 一致）
        self._split_pattern = re.compile(
            r"\$\$([\s\S]+?)\$\$"
            r"|\\\[([\s\S]+?)\\\]"
            r"|\$([^\$\n]+?)\$"
            r"|\\\(([\s\S]+?)\\\)"
        )

    def _prepare_env(self) -> None:
        import requests  # type: ignore
        if not os.path.exists(self.folder):
            os.makedirs(self.folder)
        for name, subpath in self.FILES.items():
            local = os.path.join(self.folder, os.path.basename(subpath))
            if not os.path.exists(local):
                url = self.BASE_URL + subpath
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                with open(local, "wb") as f:
                    f.write(resp.content)

    def _load_js_files(self) -> None:
        for filename in ("katex.min.js", "mhchem.min.js"):
            path = os.path.join(self.folder, filename)
            with open(path, "r", encoding="utf-8") as f:
                self.ctx.eval(f.read())

    def _split_parts(self, text: str):
        parts = []
        last = 0
        for m in self._split_pattern.finditer(text):
            if m.start() > last:
                parts.append({"type": "text", "content": text[last:m.start()]})
            is_display = m.group(1) is not None or m.group(2) is not None
            math = next(g for g in m.groups() if g is not None)
            parts.append({"type": "math", "content": math.strip(), "display": is_display})
            last = m.end()
        if last < len(text):
            parts.append({"type": "text", "content": text[last:]})
        return parts

    def validate(self, text: str) -> List[dict]:
        """返回每段公式的渲染状态。"""
        results: List[dict] = []
        for part in self._split_parts(text):
            if part["type"] != "math":
                continue
            formula = part["content"]
            formula_json = json.dumps(formula)
            display_mode = "true" if part["display"] else "false"
            js = (
                f"katex.renderToString({formula_json}, "
                f"{{ throwOnError: true, displayMode: {display_mode} }})"
            )
            try:
                self.ctx.eval(js)
                results.append({"formula": formula, "status": 1, "error": None})
            except Exception as e:
                err = str(e).split("\n")[0]
                results.append({"formula": formula, "status": 0, "error": err})
        if not results:
            return [{"formula": None, "status": 1, "error": None}]
        return results
