# 标注质检脚本 (check_annotation_quality.py)

对原始标注 Excel 的"题干 / 答案 / 最终答案"列做：
- **完整性**：是否有内容
- **LaTeX 定界符**：`$...$`、`$$...$$`、`\(...\)`、`\[...\]` 是否成对
- **裸命令**：`\frac` 之类应该被定界符包裹但漏掉的命令
- **KaTeX 渲染**：每段公式扔进 KaTeX 0.16.9 + mhchem 沙箱试渲染

## 安装

```bash
cd 1_Data/08_multisubject_data/scripts/qc
pip install -r requirements.txt
```

> `py-mini-racer` 需要本地有 V8 二进制，离线环境失败可用 `--no-katex` 跳过 KaTeX 一层。

## 一键跑（profile 模式）

预置 12 个 profile，对应原始 xlsx：

| profile | xlsx |
|---|---|
| `batch1` / `batch2` / `batch3` / `batch4` | `1.xlsx` ~ `4.xlsx` |
| `multi_v1` ~ `multi_v5` | 多学科ocr标注交付表格 副本 / 副本2 / 副本3 / 副本5 / 副本6 |
| `multi_v3_extra` | 多学科ocr标注交付表格 副本4 |
| `biology1` / `biology2` | 生物1.xlsx / 生物2.xlsx |

```bash
# 全量检查
python check_annotation_quality.py --profile batch3

# 调试：只跑前 100 行 + 关闭 KaTeX
python check_annotation_quality.py --profile batch3 --limit 100 --no-katex
```

## 自定义参数（绕过 profile）

```bash
python check_annotation_quality.py \
    --xlsx /path/to/any.xlsx \
    --sheet 第1批 \
    --col-question question \
    --col-answer user_answer \
    --col-final final_user_answer
```

列参数支持 **列名**（推荐）或 **0-based 列号**。

## 输出

默认输出到 `./qc_out/<xlsx_stem>__<sheet>/`：

- `problems.csv`：每条问题一行（`row_index, tag, issue_type, issue_detail, text_excerpt`），按 Excel 行号升序，方便直接回查
- `summary.txt`：终端摘要镜像

终端打印示例：

```
=== 标注质检报告: 3.xlsx / 第3批 ===
扫描行数:        3163
完整无缺字段:    3120 (98.6%)
———— 公式 ————
正常行（无任何异常）:  2891
异常行:              272
行级错误率:          8.6%

异常类型分布（按 issue 计）:
  delim_pair         134  (cross_pair 32 / orphan_close 71 / unclosed_open 31)
  bare_cmd            89
  katex_render        49
  empty_annotation    43

豁免（未计入异常）: 17
Top 5 高频错误命令: \frc(7), \sqr(5), \rightarow(3), ...
```

## 豁免清单

下列 KaTeX 报错不计入问题（mhchem / 历史豁免）：

- `\ce`、`\pu`（化学式 / 单位）
- `\uwave`、`\bcancel`
- 裸文本里的 `\n`、`\r`、`\t`（转义字符，不当 LaTeX 命令）

## FAQ

**Q: 第一次跑提示下载 katex.min.js 失败？**
A: 检查能否访问 `cdn.jsdelivr.net`。无外网时手动把 `katex.min.js` 和 `mhchem.min.js` 放到 `katex_assets/` 目录即可，或加 `--no-katex` 跳过。

**Q: 为什么 problems.csv 行号比 xlsx 显示行号小 / 大？**
A: `row_index` 已经按 **Excel 实际行号**（含表头第 1 行）输出，可直接在 Excel 里用「定位 → 行号」回查。
