# AGENTS.md — 后续 agent 接手指南

> 这份是给**后续接手的 AI agent / 工程师**看的文档，不是用户文档。用户文档在 [README.md](README.md)。

## 1. 这个 repo 是什么

一个**离线、单目录、零依赖于母项目**的 xlsx 标注质检脚本，用来检查数据标注同学交付的 Excel 里 "题干 / 答案 / 最终答案" 三列的 LaTeX 质量。检查 4 类问题：
- 字段空缺 (`empty_annotation`)
- 定界符不配对 (`delim_pair`)
- 裸命令未包裹 (`bare_cmd`)
- KaTeX 渲染失败 (`katex_render`)

**Provenance**：从 `mathocr/1_Data/08_multisubject_data/scripts/qc/` 抽离出来独立 push 到 GitHub，用于交付给数据测同学。母项目里有大量 v1-0-9 训练数据 build 上下文，但**这个 repo 不依赖母项目任何文件**，可以独立 clone 跑。

## 2. 代码地图

```
check_annotation_quality.py    主入口；argparse + PROFILES dict + 检查循环 + CSV/摘要输出
_latex_utils.py                定界符扫描、裸命令检测、KatexValidator（V8 沙箱渲染）
katex_assets/                  KaTeX 0.16.9 + mhchem 静态 JS（已 commit，离线可用）
requirements.txt               pandas / openpyxl / py-mini-racer / requests
README.md                      用户文档
.gitignore                     忽略 qc_out/、__pycache__/、.DS_Store
```

**核心函数串联**：

| 调用链 | 来源 |
|---|---|
| `main()` → `run_check()` → `check_cell()` | `check_annotation_quality.py` |
| `check_cell()` → `scan_delimiters()` / `marker_matches()` / `KatexValidator.validate()` | `_latex_utils.py` |

`_latex_utils.py` 的 `scan_delimiters` 和 `marker_matches` 移植自母项目
`1_Data/06_internvl35_jsonl/math_data_v17/260416_delimiter_experiment/check_delimiter_quality_v2.py`，
`KatexValidator` 移植自 `260416_after_human_check/latex_check.py`。母项目脚本是一次性实验脚本，本 repo 取了核心可复用部分并稳定下来。

## 3. 维护场景

### 3.1 加一个新的 xlsx profile

只需改 `check_annotation_quality.py` 顶部的 `PROFILES` dict，照葫芦画瓢。**强烈建议用列名（str）而非列号（int）**，列名在 Excel 里更稳定，列号一改 Excel 结构就废。如果某 tag 列不存在，写 `None`（脚本会自动跳过该 tag）。

校准列名的最简方法：
```bash
python3 -c "import pandas as pd; df=pd.read_excel('新.xlsx', sheet_name=0, nrows=1); [print(i,c) for i,c in enumerate(df.columns)]"
```

### 3.2 加一条豁免规则

在 `check_annotation_quality.py` 的 `KATEX_EXEMPT_ERR_PATTERNS`（KaTeX 报错正则）或 `BARE_CMD_EXEMPT`（裸命令字面量集合）里追加。

判断什么该豁免的准则：
1. **历史已知 FP**：mhchem 的 `\ce` / `\pu`，KaTeX 在没装 mhchem 时会报但实际正确
2. **数据特征 FP**：英语填空题里的 `t_ _cher`（已在 `_latex_utils._is_english_fill_blank` 启发式过滤）
3. **转义字符**：`\n` / `\r` / `\t` 不是 LaTeX 命令

豁免不当**会让真问题漏报**。新增豁免时务必给一两条**复现样本**（标注 Excel 的具体行号 + 原文）记到 commit message。

### 3.3 加一类新检查

例：检查"图片列是否为 s3 路径"或"题型枚举是否在允许清单内"。

在 `check_cell()` 里追加一段并加 `issue_type` 名字（如 `"image_path"`），然后在 `render_summary()` 的 `异常类型分布` 列表里补上同名。CSV 列结构不变，新类型自动出现。

### 3.4 关闭 KaTeX（依赖装不上时）

用户加 `--no-katex` 即可。或者整个把 `KatexValidator` 初始化包到 try 里——脚本已经这么做（[check_annotation_quality.py:119-126](check_annotation_quality.py#L119-L126)），py-mini-racer 装不上会自动退化为正则模式。

## 4. 已踩过的坑

| # | 坑 | 现象 | 处理 |
|---|---|---|---|
| 1 | **英语填空假阳性** | 英语题 `p_p_l` / `b_s_de` 被 `LATEX_POWSUB_RE` 当成 LaTeX 下标 | `_latex_utils._is_english_fill_blank` 启发式：上下文窗口含 ≥2 个 `_` 则豁免 |
| 2 | **GitHub Fine-grained PAT 默认无权限** | 用 `github_pat_...` 推送报 `Permission denied to <username>` | Fine-grained PAT 必须显式把目标 repo 加进 Repository access + Contents 设为 R/W；新手用 Classic PAT 勾 `repo` 一项最简 |
| 3 | **github.com:22 在内网偶尔抖** | SSH push 间歇性 `Connection reset by peer` | remote 改用 `ssh://git@ssh.github.com:443/...`（SSH-over-HTTPS），现已是 origin 默认值 |
| 4 | **openpyxl read_only 模式 max_row 不准** | `wb[sn].max_row` 返回 1 | 用 `pd.read_excel(nrows=N)` 探列、`pd.read_excel()` 全量读取 |
| 5 | **副本系列 xlsx 的"判题结果""最终解题答案"列在很多行为空** | 报大量 `empty_annotation`（如 `multi_v1` 80 行 44 条空） | 这是**真问题**（标注同学漏标），不豁免；如果只想看公式问题，看 CSV 时过滤 `issue_type != empty_annotation` |
| 6 | **`$n$` / `$x$` 等简单原子被误判为不必要定界符** | 母项目历史问题 | 在 `_latex_utils.SIMPLE_ATOM_RE` 已豁免（≤3 字符纯字母数字）；本 repo 没暴露这条检查，留作未来扩展 |

## 5. 设计决策（为什么 X 不 Y）

| 决策 | 替代方案 | 选择理由 |
|---|---|---|
| `katex_assets/` 直接 commit | `.gitignore` 让脚本自动下载 | 数据测同学的环境可能没外网；312KB 不大；离线可用是硬需求 |
| `PROFILES` 内置 dict | 外置 `profiles.yaml` | 单文件无依赖，加 profile 改一个文件即可；不引入 pyyaml |
| 输出 CSV 而非回写 Excel | 直接在 xlsx 加问题列 | 不污染原文件；标注同学按 row_index 在 Excel 跳转更直观；CSV 易并入数据看板 |
| 列名优先（str），列号 fallback（int） | 仅列号 | xlsx 结构会变（如 3.xlsx 列号比 1/2/4.xlsx 偏移 2），列名更稳 |
| 一行多 issue 不折叠 | dedupe by row | 每个错位置都列出，方便一次性修完；如要按行聚合，pandas 透视即可 |
| `row_index` 用 Excel 实际行号（含表头第 1 行 + iterrows 从 2 开始） | 0/1-based 数据行号 | 标注同学在 Excel 里看到的行号 = `row_index`，直接定位 |

## 6. 重要不变量（改代码不要破坏）

1. **`_latex_utils.py` 不能引入除 `re` / `os` / `json` / `dataclasses` / `requests`(惰性) / `py_mini_racer`(惰性) 外的依赖** — 它要保持轻、可单独 import
2. **`KatexValidator` 必须 lazy 加载 py_mini_racer 和 requests**（在 `__init__` 里 try import），让 `--no-katex` 模式不需要这两个依赖
3. **profile 增删时不能改 `Profile` dataclass 字段名**（`col_question` / `col_answer` / `col_final`）— CSV header 和摘要文案绑定这三个 tag 顺序
4. **CSV header 固定 5 列**：`row_index, tag, issue_type, issue_detail, text_excerpt` — 下游可能写脚本 parse

## 7. 推送前自检 checklist

```bash
# 1. 静态：列定位
python3 check_annotation_quality.py --profile batch1 --limit 50 --no-katex
# 看 [cols] 输出，每个 tag 列名/列号应该和你预期一致

# 2. 真阳性：跑数学题密集的 batch3
python3 check_annotation_quality.py --profile batch3 --limit 500 --no-katex
# 应该能抓到 \text / \circ / \frac 等没包定界符的真问题

# 3. KaTeX 沙箱可用
python3 check_annotation_quality.py --profile batch3 --limit 50
# 首次会从 cdn 下 katex.min.js + mhchem.min.js（可走 https_proxy）

# 4. 误报抽查：
head -30 qc_out/*/problems.csv
# 人工看前 30 条，FP 应该 < 3 条（10%）
```

## 8. 不要做的事

- **不要把 KaTeX assets 移出 katex_assets/** — 用户离线环境会跑不动
- **不要从母项目（mathocr 工作树）import 任何模块** — 这个 repo 必须独立 clone 跑
- **不要把 `qc_out/` 加进 git** — .gitignore 已经忽略，提交前确认 `git status` 干净
- **不要为单个 profile 加学科专属逻辑** — 检查规则要通用；学科差异通过 `PROFILES` 表达
- **不要轻易扩大豁免清单** — 每加一条都有漏报真问题的风险；加之前先确认是 systematic FP 而非 isolated case

## 9. 路标 (TODO 候选)

- [ ] 把 `_latex_utils.SIMPLE_ATOM_RE`（"$x$/$n$ 不必要定界符"检查）暴露为 `--check-redundant` 开关
- [ ] CSV 加一列 `subject_guess`（根据 xlsx 文件名推断学科），帮助按学科聚合
- [ ] `--strict-bare-cmd` 模式：禁掉所有英语填空启发式，让纯数学/物理 xlsx 跑出更严的报告
- [ ] 输出可选 `summary.json`，让外部 dashboard 直接 parse
- [ ] 列定位失败时给出更友好的 error（候选最接近列名）
