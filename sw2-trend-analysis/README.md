# 申万二级行业趋势分析工具 · 一期

手动触发，跑通全部申万二级行业板块（约 130 项，实测 akshare 1.18.94 返回 131 项，数量随版本浮动，代码按实际返回动态遍历）的指数点位拉取、趋势计算、
SQLite/Excel 双存储与 9-sheet Excel 导出（点位 + MA5~MA360 + 汇总）。详见 `../申万行业趋势分析工具-方案.md`。

> **专业术语与计算规则**（归一化斜率、R²、死区阈值 ε、窗口强度、综合强度分等的定义与公式）见 [`docs/指标与计算规则.md`](docs/指标与计算规则.md)。

## 安装

```bash
pip install -r requirements.txt
```

## 运行顺序

### 1. 先跑 ε 探针（首次必需，之后低频跑）

```bash
python probe_epsilon.py            # 全部板块
python probe_epsilon.py --code 801012   # 只跑单个板块
python probe_epsilon.py --no-csv        # 不写 CSV 备查
```

探针会：
- 逐板块**优先读本地 `price`**，库空才首灌全量历史（不复权），因此同时完成 `price` 首灌；
- 对每个 (板块,窗口) 取历史 `|slope_norm|` 的 40% 分位作为死区阈值 ε，写入 `epsilon` 表；
- 样本不足的 (板块,窗口) 告警跳过，不写无效 ε。

ε 是慢变量，建议每月/每季跑一次即可。

### 2. 再跑主流程

```bash
python main.py                # 全部板块
python main.py --code 801012  # 只跑单个板块
python main.py --no-excel     # 只落库，不导出 Excel
```

主流程会：
- 逐板块增量拉取点位（库非空只 append 更新的交易日），校验去 null/异常涨跌幅；
- 对 7 个窗口（5/10/30/60/90/180/360 交易日）算 MA + 归一化斜率 + R² + `price_vs_ma`，
  结合每板块每窗口独立 ε 判 `UP/DOWN/FLAT/N/A`；
- 第二层整理跨周期一致性，出 UP/DOWN 双关注榜；
- 落 `trend` 表（同一天重跑 UPSERT 覆盖，幂等），默认同时导出 Excel 快照
  `data/exports/sw_trend_YYYY-MM-DD.xlsx` 与汇总榜单 `sw_trend_summary_YYYY-MM-DD.csv`
  （逐窗口明细见 Excel 的 MA5~MA360 各 sheet，不再单独导 CSV；`--no-excel`/`--no-csv` 可分别跳过）。

> 若未先跑探针、`epsilon` 表为空，主流程会告警并把所有方向降级为 `N/A`。

## 目录结构

```
sw2-trend-analysis/
├── config.py          # 全局配置（窗口/分位数/口径/路径/重试/校验）
├── db.py              # SQLite 建表与读写（price/trend/epsilon）
├── fetch.py           # AkShare 取列表 + 拉点位 + 增量 + 校验
├── analyze.py         # 第一层方向判定 + 第二层跨周期整理 + 探针复用工具
├── export_excel.py    # 9-sheet Excel 导出（点位 + MA5~MA360 + 汇总）
├── probe_epsilon.py   # ε 探针（先行/独立运行）
├── main.py            # 主流程入口（默认同时出 Excel + 汇总 CSV）
├── export_csv.py      # 汇总榜单 CSV 导出（明细见 Excel 的 MA5~MA360 sheet）
├── docs/              # 说明文档
│   └── 指标与计算规则.md
├── requirements.txt
└── data/              # 运行后自动生成
    ├── sw.db          # SQLite（price/trend/epsilon）
    ├── epsilon_probe.csv
    └── exports/       # Excel 快照 + 汇总榜单 CSV
```

## 数据说明

- 数据源 `index_hist_sw` 无起止日期入参，一次返回全量历史；增量在本地按日期过滤实现。
- 申万行业指数为点位指数，**不复权**（无 qfq）。
- `price` 存全历史，探针与主流程共用同一份，探针一般 0 次额外网络请求。
