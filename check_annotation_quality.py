#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标注质检：检查 xlsx 标注的题干/答案/最终答案的定界符配对、裸命令、KaTeX 渲染。

用法（profile 模式，一键跑）:
    python check_annotation_quality.py --profile batch1
    python check_annotation_quality.py --profile biology2 --no-katex --limit 100

用法（显式参数模式）:
    python check_annotation_quality.py \
        --xlsx /path/to/x.xlsx --sheet 第1批 \
        --col-question question --col-answer user_answer --col-final final_user_answer

输出:
    qc_out/<xlsx_stem>__<sheet>/problems.csv   每行一条问题
    qc_out/<xlsx_stem>__<sheet>/summary.txt    终端摘要镜像
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd  # type: ignore

from _latex_utils import (
    KatexValidator,
    in_spans,
    marker_matches,
    scan_delimiters,
)

EXCEL_DIR_DEFAULT = (
    "/mnt/afs_ocr/tongronglei/workspace/mathocr/1_Data/08_multisubject_data/excels"
)

ColRef = Union[str, int, None]


@dataclass
class Profile:
    xlsx: str
    sheet: str
    col_question: ColRef
    col_answer: ColRef
    col_final: ColRef


# col_* 优先用列名（更稳健，避免 xlsx 改结构后失效）；None = 该 tag 缺失，跳过。
PROFILES: Dict[str, Profile] = {
    # 数学/多学科早期 4 批（结构: question / user_answer / final_user_answer）
    "batch1":   Profile("1.xlsx", "第1批", "question", "user_answer", "final_user_answer"),
    "batch2":   Profile("2.xlsx", "第2批", "question", "user_answer", "final_user_answer"),
    "batch3":   Profile("3.xlsx", "第3批", "question", "user_answer", "final_user_answer"),
    "batch4":   Profile("4.xlsx", "第4批", "question", "user_answer", "final_user_answer"),
    # 多学科副本系列（结构: ocr结果精标 / 判题结果 / 最终解题答案）
    "multi_v1":  Profile("多学科ocr标注交付表格 副本.xlsx",  "第1批2000题(已复核)",  "ocr结果精标", "判题结果",   "最终解题答案"),
    "multi_v2":  Profile("多学科ocr标注交付表格 副本2.xlsx", "第2批4000题(已复核)",  "ocr结果精标", "判题结果",   "最终解题答案"),
    "multi_v3":  Profile("多学科ocr标注交付表格 副本3.xlsx", "第3批5996题(已复核)",  "ocr结果精标", "判题结果",   "最终解题答案"),
    "multi_v3_extra": Profile("多学科ocr标注交付表格 副本4.xlsx", "第3批（迁出20题已复核）", "ocr结果精标", None,        None),
    "multi_v4":  Profile("多学科ocr标注交付表格 副本5.xlsx", "第4批3608题(已复核)",  "ocr结果精标", None,        None),
    "multi_v5":  Profile("多学科ocr标注交付表格 副本6.xlsx", "第5批3591题(预标已修改)", "ocr结果精标", None,        None),
    # 生物
    "biology1":  Profile("生物1.xlsx", "生物1000题补充(缺解判预标注)",       "ocr结果精标", None,        None),
    "biology2":  Profile("生物2.xlsx", "【生物】（已剔除测试集数据）",       "question",   "user_answer", "final_user_answer"),
}

TAG_NAMES = ("题干", "答案", "最终答案")  # (col_question, col_answer, col_final)

# 豁免：这些 KaTeX 错误不计入问题（化学/历史豁免命令）
KATEX_EXEMPT_ERR_PATTERNS = [
    re.compile(r"Undefined control sequence:?\s*\\ce\b"),
    re.compile(r"Undefined control sequence:?\s*\\pu\b"),
    re.compile(r"Undefined control sequence:?\s*\\uwave\b"),
    re.compile(r"Undefined control sequence:?\s*\\bcancel\b"),
]
# 裸命令豁免：不该当作 LaTeX 命令的转义字符
BARE_CMD_EXEMPT = {"\\n", "\\r", "\\t"}


# ---------------- core check ----------------

def resolve_col(df: pd.DataFrame, ref: ColRef) -> Optional[int]:
    """把列名 / 列序号统一解析为 0-based 列序号；None / 缺失返回 None。"""
    if ref is None:
        return None
    if isinstance(ref, int):
        if 0 <= ref < len(df.columns):
            return ref
        raise ValueError(f"列序号 {ref} 越界（共 {len(df.columns)} 列）")
    # str
    if ref in df.columns:
        return df.columns.get_loc(ref)
    raise ValueError(
        f"列名 {ref!r} 不存在；可用列: {list(df.columns)[:10]}..."
    )


def cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    s = str(v)
    return "" if s.strip().lower() == "nan" else s


def check_cell(text: str, validator: Optional[KatexValidator]) -> List[dict]:
    """返回该单元格的问题清单。空字符串 → 单条 empty_annotation。"""
    if not text.strip():
        return [{"type": "empty_annotation", "detail": "字段为空", "exempt": False}]

    issues: List[dict] = []

    # E2: 定界符配对
    spans, pair_issues = scan_delimiters(text)
    for it in pair_issues:
        issues.append({
            "type": "delim_pair",
            "subtype": it.issue_type,
            "detail": f"{it.issue_type}: {it.detail} @pos{it.pos}",
            "exempt": False,
        })

    # E3: 裸命令
    for tp, s, e, raw in marker_matches(text):
        if raw in BARE_CMD_EXEMPT:
            continue
        if in_spans(s, e, spans):
            continue
        issues.append({
            "type": "bare_cmd",
            "subtype": tp,
            "detail": f"{tp}: {raw} @pos{s}",
            "exempt": False,
        })

    # E4: KaTeX 渲染
    if validator is not None:
        for r in validator.validate(text):
            if r["status"] == 0 and r["error"]:
                exempt = any(p.search(r["error"]) for p in KATEX_EXEMPT_ERR_PATTERNS)
                snippet = (r["formula"] or "")[:60].replace("\n", " ")
                issues.append({
                    "type": "katex_render",
                    "subtype": "",
                    "detail": f"{snippet} → {r['error']}",
                    "exempt": exempt,
                })

    return issues


# ---------------- run ----------------

def run_check(profile: Profile, *, excel_dir: Path, use_katex: bool,
              limit: Optional[int], output_dir: Path) -> None:
    xlsx_path = Path(profile.xlsx)
    if not xlsx_path.is_absolute():
        xlsx_path = excel_dir / xlsx_path
    if not xlsx_path.exists():
        sys.exit(f"[ERR] xlsx 不存在: {xlsx_path}")

    print(f"[load] {xlsx_path.name} / sheet={profile.sheet}")
    df = pd.read_excel(xlsx_path, sheet_name=profile.sheet, engine="openpyxl")
    total_rows = len(df)
    print(f"[load] {total_rows} 行 × {len(df.columns)} 列")

    col_refs = (profile.col_question, profile.col_answer, profile.col_final)
    col_idx: List[Optional[int]] = []
    for ref in col_refs:
        try:
            col_idx.append(resolve_col(df, ref))
        except ValueError as e:
            sys.exit(f"[ERR] {e}")

    print("[cols] " + " / ".join(
        f"{name}={df.columns[i]!r}(列{i})" if i is not None else f"{name}=<skip>"
        for name, i in zip(TAG_NAMES, col_idx)
    ))

    validator: Optional[KatexValidator] = None
    if use_katex:
        print("[katex] 加载 KaTeX 沙箱（首次会下载 katex.min.js / mhchem.min.js）...")
        try:
            assets_dir = Path(__file__).parent / "katex_assets"
            validator = KatexValidator(folder=str(assets_dir))
            print("[katex] ok")
        except Exception as e:
            print(f"[katex] 初始化失败，退化为 --no-katex: {e}")

    if limit and limit < total_rows:
        df = df.head(limit)
        print(f"[limit] 仅检查前 {limit} 行")

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "problems.csv"
    summary_path = output_dir / "summary.txt"

    counters = {
        "total_rows": len(df),
        "rows_with_issue": 0,
        "rows_all_full": 0,
        "by_type": Counter(),
        "by_subtype": Counter(),
        "exempt_count": 0,
        "top_cmds": Counter(),
    }

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["row_index", "tag", "issue_type", "issue_detail", "text_excerpt"])

        for sheet_row, (_, row) in enumerate(df.iterrows(), start=2):  # Excel 行号 = sheet_row（含表头第1行）
            row_has_issue = False
            row_all_full = True

            for tag_name, ci in zip(TAG_NAMES, col_idx):
                if ci is None:
                    continue
                text = cell_text(row.iloc[ci])
                if not text.strip():
                    row_all_full = False
                issues = check_cell(text, validator)
                for iss in issues:
                    if iss.get("exempt"):
                        counters["exempt_count"] += 1
                        continue
                    row_has_issue = True
                    counters["by_type"][iss["type"]] += 1
                    sub = iss.get("subtype") or ""
                    if sub:
                        counters["by_subtype"][f"{iss['type']}.{sub}"] += 1
                    # 高频错误命令
                    m = re.search(r"\\[A-Za-z]+", iss["detail"])
                    if m:
                        counters["top_cmds"][m.group(0)] += 1
                    writer.writerow([
                        sheet_row,
                        tag_name,
                        iss["type"],
                        iss["detail"],
                        text[:200].replace("\n", " "),
                    ])

            if row_has_issue:
                counters["rows_with_issue"] += 1
            if row_all_full:
                counters["rows_all_full"] += 1

    summary = render_summary(profile, counters, csv_path)
    summary_path.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"详单已写入: {csv_path}")


def render_summary(profile: Profile, c: dict, csv_path: Path) -> str:
    total = c["total_rows"] or 1
    ratio_issue = c["rows_with_issue"] / total * 100
    ratio_full = c["rows_all_full"] / total * 100

    lines: List[str] = []
    lines.append(f"=== 标注质检报告: {profile.xlsx} / {profile.sheet} ===")
    lines.append(f"扫描行数:        {c['total_rows']}")
    lines.append(f"完整无缺字段:    {c['rows_all_full']} ({ratio_full:.1f}%)")
    lines.append(f"———— 公式 ————")
    lines.append(f"正常行（无任何异常）:  {c['total_rows'] - c['rows_with_issue']}")
    lines.append(f"异常行:              {c['rows_with_issue']}")
    lines.append(f"行级错误率:          {ratio_issue:.1f}%")
    lines.append("")
    lines.append("异常类型分布（按 issue 计）:")
    for tp in ("delim_pair", "bare_cmd", "katex_render", "empty_annotation"):
        n = c["by_type"].get(tp, 0)
        sub_lines = []
        for k, v in c["by_subtype"].items():
            if k.startswith(tp + "."):
                sub_lines.append(f"{k.split('.', 1)[1]} {v}")
        sub = f"  ({' / '.join(sub_lines)})" if sub_lines else ""
        lines.append(f"  {tp:18s} {n}{sub}")
    lines.append("")
    lines.append(f"豁免（未计入异常）: {c['exempt_count']}")
    if c["top_cmds"]:
        top = ", ".join(f"{k}({v})" for k, v in c["top_cmds"].most_common(5))
        lines.append(f"Top 5 高频错误命令: {top}")
    lines.append("")
    lines.append(f"详单: {csv_path}")
    return "\n".join(lines)


# ---------------- cli ----------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(PROFILES.keys()),
                    help="使用预置 profile（覆盖 --xlsx/--sheet/--col-*）")
    ap.add_argument("--xlsx", help="xlsx 路径（profile 之外使用）")
    ap.add_argument("--sheet", help="sheet 名（或 0-based 索引）")
    ap.add_argument("--col-question", default=None, help="题干列：列名 或 0-based 列号")
    ap.add_argument("--col-answer", default=None, help="答案列：列名 或 0-based 列号")
    ap.add_argument("--col-final", default=None, help="最终答案列：列名 或 0-based 列号")
    ap.add_argument("--katex", dest="katex", action="store_true", default=True,
                    help="启用 KaTeX 渲染检查（默认）")
    ap.add_argument("--no-katex", dest="katex", action="store_false",
                    help="禁用 KaTeX 渲染检查（仅正则）")
    ap.add_argument("--limit", type=int, default=None, help="调试用：仅检查前 N 行")
    ap.add_argument("--excel-dir", default=EXCEL_DIR_DEFAULT, help="profile 模式的 xlsx 根目录")
    ap.add_argument("--output-dir", default=None, help="输出目录（默认 ./qc_out/<xlsx>__<sheet>/）")
    return ap.parse_args()


def _coerce_col(s: Optional[str]) -> ColRef:
    if s is None:
        return None
    if s.lstrip("-").isdigit():
        return int(s)
    return s


def build_profile_from_cli(args) -> Profile:
    if not args.xlsx or not args.sheet:
        sys.exit("[ERR] 非 profile 模式必须同时指定 --xlsx 和 --sheet")
    sheet = args.sheet
    if isinstance(sheet, str) and sheet.lstrip("-").isdigit():
        sheet = int(sheet)
    return Profile(
        xlsx=args.xlsx,
        sheet=sheet,
        col_question=_coerce_col(args.col_question),
        col_answer=_coerce_col(args.col_answer),
        col_final=_coerce_col(args.col_final),
    )


def main() -> None:
    args = parse_args()
    if args.profile:
        prof = PROFILES[args.profile]
        # 显式参数可覆盖 profile 字段
        if args.xlsx: prof = Profile(args.xlsx, prof.sheet, prof.col_question, prof.col_answer, prof.col_final)
        if args.sheet:
            sheet = int(args.sheet) if args.sheet.lstrip("-").isdigit() else args.sheet
            prof = Profile(prof.xlsx, sheet, prof.col_question, prof.col_answer, prof.col_final)
        if args.col_question is not None:
            prof = Profile(prof.xlsx, prof.sheet, _coerce_col(args.col_question), prof.col_answer, prof.col_final)
        if args.col_answer is not None:
            prof = Profile(prof.xlsx, prof.sheet, prof.col_question, _coerce_col(args.col_answer), prof.col_final)
        if args.col_final is not None:
            prof = Profile(prof.xlsx, prof.sheet, prof.col_question, prof.col_answer, _coerce_col(args.col_final))
    else:
        prof = build_profile_from_cli(args)

    if args.output_dir:
        out = Path(args.output_dir)
    else:
        stem = Path(prof.xlsx).stem
        sheet_safe = re.sub(r"[^\w一-鿿]+", "_", str(prof.sheet)).strip("_")
        out = Path("qc_out") / f"{stem}__{sheet_safe}"

    run_check(
        prof,
        excel_dir=Path(args.excel_dir),
        use_katex=args.katex,
        limit=args.limit,
        output_dir=out,
    )


if __name__ == "__main__":
    main()
