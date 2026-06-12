# sw2_score_rank.py 技术文档

## 概述

**申万二级行业量化评分排名工具** — 基于价格趋势、市场宽度趋势、拥挤度趋势三项指标，对131个申万二级行业进行加权综合评分排名，同时计算3天前的历史得分用于对比分析。

### 核心功能

| 功能 | 说明 |
|------|------|
| 三维评分 | 价格趋势(35%) + 宽度趋势(35%) + 拥挤度趋势(30%) |
| 百分位归一化 | 将原始分数转为0~100百分位排名，消除量纲差异 |
| 3天前对比 | 计算基准日前移3天的得分，观察行业得分变化方向 |
| 自动数据拉取 | 缺失数据时自动调用 API/akshare 接口补充 |
| 日期对齐 | width/congestion/quotes 可能日期不同时自动对齐基准 |

---

## 数据架构

### 输入 (3个数据源)

```
quotes/
  └── sw2_index_quotes_YYYY-MM-DD.csv    # 行情: {代码, 名称, 3日涨跌(%), 5日涨跌(%), ...}

width/
  └── width_YYYY-MM-DD.csv               # 宽度: {代码, 名称, 2026-06-01, ..., 2026-06-10}
                                            # 每格 = value20 (0~100)

congestion/
  └── congestion_YYYY-MM-DD.csv          # 拥挤度: {代码, 名称, 2026-06-01_换手, 2026-06-01_成交额拥挤, ...}
```

### 输出

```
rank/
  └── sw2_score_rank_YYYY-MM-DD.csv      # 排名表 (见下方字段说明)
```

---

## 核心算法

### 1. 评分公式

#### 综合得分

```
total = s_price × 0.35 + s_width × 0.35 + s_cong × 0.30
范围: 0 ~ 100 (百分位归一化后)
```

#### 价格趋势分 `calc_price_score()`

```python
raw = chg_3d × 0.4 + chg_5d × 0.3 + chg_10d × 0.2 + chg_20d × 0.1
```

- **权重设计**: 近期权重高(3日40%)，远期衰减(20日10%)
- **数据来源**: `quotes/sw2_index_quotes_*.csv` 的涨跌幅列
- **含义**: 正值=上涨趋势强，负值=下跌趋势强

#### 市场宽度趋势分 `calc_width_score(w_data, code, base_offset)`

```python
raw = Σ(change_Nd × T_WEIGHT[N]) + current_value × 0.15
其中 change_Nd = value[base_offset] - value[base_offset + N]
N ∈ {3, 5, 10, 20}  (对应 T_OFFSETS)
T_WEIGHTS = [0.4, 0.3, 0.2, 0.1]
```

- **变化量**: 基准日 vs N天前的差值（正值=扩张）
- **绝对位置加成**: 当前宽度越高加分越多(+15%上限)
- **数据来源**: `width/*.csv` 的 value20 列

#### 拥挤度趋势分 `calc_congestion_score(c_data, code, base_offset)`

```python
cur_cong = (turnoverRateFQuantile + amountCongestionQuantile) / 2

if cur_cong >= 60:   # 高拥挤区 → 下降为好
    raw += (-change_Nd) × weight
elif cur_cong <= 40:  # 低拥挤区 → 上升为好
    raw += change_Nd × weight
else:                # 中性区 → 趋近50为好
    if 更接近50: raw += 0.5 × weight
    if 更远离50: raw -= 0.5 × weight
```

- **核心逻辑**: 高拥挤下降是好事（资金撤出风险降低），低拥挤上升是好事（关注度提升）
- **中性区间**: 40~60 分位数之间，越接近50（均值回归）越好

---

### 2. 百分位归一化 `percentile_normalize()`

```python
def percentile_normalize(scores):
    # 按分数排序 → 赋予 0~100 百分位
    # 最高分=100, 最低分=0, 中位数≈50
    sorted_indices = sorted(enumerate(scores), key=lambda x: x[1])
    result[orig_idx] = rank / (n-1) * 100
```

**目的**: 
- 消除三项指标的量纲差异（价格是%值、宽度是0~100整数、拥挤度也是0~100）
- 使最终综合得分可解释（70+ = 前30%, 90+ = 前10%）

**示例**:
```
原始分: [5.2, -3.1, 8.7, 1.0, -5.0]  (5个行业)
归一化: [62.5, 25.0, 100, 50.0, 0]        (百分位)
```

---

### 3. 3天前得分机制

#### 确定偏移量

```python
# 从 width CSV 表头取"倒数第4个日期"作为"3天前"
dates_w = w_data["dates"]           # ["2026-05-07", ..., "2026-06-08", "2026-06-09", "2026-06-10"]
offset_3d_ago_w = len(dates_w) - 4  # 指向 "2026-06-05"

# congestion 同理
offset_3d_ago_c = base_offset_c - 3
```

#### 行情数据获取 (可能需要调接口)

```python
load_or_generate_quotes_for_date("2026-06-05")
  │
  ├─ quotes/sw2_index_quotes_2026-06-05.csv 存在? → 直接加载
  │
  └─ 不存在? → 调 akshare.index_hist_sw(symbol) 拉全量K线
                → 在历史中定位到 <= 目标日期的最近交易日
                → 以该位置为基准算 3/5/10/20日涨跌幅
                → 存入 quotes/ 目录复用
                → 返回 name_map
```

#### 最终输出列

| 列名 | 当日来源 | 3天前来源 |
|------|----------|-----------|
| 综合得分 | base_offset=0 的三项归一化加权 | offset_3d 的三项归一化加权 |
| 价格趋势(35%) | 今日 quotes CSV | 3天前 quotes (可能新生成) |
| 宽度趋势(35%) | width values[base_offset_w] | width values[offset_3d_ago_w] |
| 拥挤度趋势(30%) | congestion values[base_offset_c] | congestion values[offset_3d_ago_c] |

---

### 4. 基准日期对齐

**问题**: width 最新到06-11, congestion 最新到06-10, quotes 是06-11 — 三个数据源日期不一致

**解决**:
```python
# 取 width ∩ congestion 的共同最新日期
common_wc = sorted(set(dates_w) & set(dates_c))
base_date = common_wc[-1]              # 如 "2026-06-10"

# 各自在该日的索引
base_offset_w = dates_w.index(base_date)   # 如 29 (倒数第2个)
base_offset_c = dates_c.index(base_date)   # 如 28 (最后1个)

# 行情检查
if quotes_date != base_date and quotes_date != actual_w and quotes_date != actual_c:
    print("[WARN] 行情日期与基准不一致")
```

---

## 函数清单

### 数据加载层

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `_find_latest_csv(dir, prefix)` | str or None | 查找目录下最新CSV |
| `load_quotes_csv()` | DataFrame | 加载行情CSV，无则调 query_sw_index.py |
| `build_name_map(df)` | dict | `{名称: {chg_3d, chg_5d, chg_10d, chg_20d}}` |
| `load_or_generate_quotes_for_date(date)` | dict or None | 加载/生成指定日期行情快照 |
| `_calc_n_chg(closes, n)` | float or None | 计算N日涨跌幅 |
| `load_width_data()` | dict | 加载宽度CSV→内部格式，无则调API |
| `load_congestion_data()` | dict | 加载拥挤度CSV→内部格式，无则调API |

### 评分计算层

| 函数 | 返回值 | 参数说明 |
|------|--------|----------|
| `get_value_at_offset(values, offset)` | float or None | 安全取第offset个元素 |
| `get_width_change(w, code, offset, base_off)` | float | 宽度N日变化量 |
| `get_congestion_change(c, code, offset, base_off)` | float | 拥挤度N日变化量(换手+成交额均值) |
| `calc_price_score(name_map, name)` | float | 价格趋势原始分 |
| `calc_width_score(w, code, base_off)` | float | 宽度趋势原始分 |
| `calc_congestion_score(c, code, base_off)` | float | 拥挤度趋势原始分 |
| `percentile_normalize(scores)` | list[float] | 百分位归一化(0~100) |

### 主流程

| 函数 | 说明 |
|------|------|
| `main()` | 全流程入口: 加载→对齐→评分→归一化→排序→输出 |

---

## 输出文件格式

`rank/sw2_score_rank_2026-06-12.csv`:

| 列名 | 类型 | 说明 |
|------|------|------|
| 排名 | int | 1~N (按综合得分降序) |
| 代码 | str | 申万二级行业代码 |
| 名称 | str | 申万二级行业名称 |
| **综合得分** | float (×1) | **主排序列: 三项加权 0~100** |
| 价格趋势(35%) | float (×1) | 归一化后的价格趋势分 |
| 宽度趋势(35%) | float (×1) | 归一化后的宽度趋势分 |
| 拥挤度趋势(30%) | float (×1) | 归一化后的拥挤度趋势分 |
| 3天前得分 | float / NaN | 3天前同规则计算的得分 (无数据显示空) |

### 示例数据

```
排名,代码,名称,综合得分,价格趋势(35%),宽度趋势(35%),拥挤度趋势(30%),3天前得分
1,851222.SI,种植业,78.5,82.3,75.1,77.8,65.2
2,851223.SI,渔业,76.2,79.8,72.5,75.9,70.1
...
131,851010.SI,保险,18.3,15.2,20.1,19.5,22.8
```

---

## 使用方法

```bash
# 直接运行
python sw2_score_rank.py

# 输出示例
============================================================
  申万二级行业量化评分排名
  价格趋势(35%) + 宽度趋势(35%) + 拥挤度趋势(30%)
============================================================

[1/3] 加载行情数据 ...
加载行情: sw2_index_quotes_2026-06-11.csv (131, 14)

[2/3] 加载市场宽度数据 ...
加载宽度数据: width_2026-06-11.csv (131, 31)

[3/3] 加载拥挤度数据 ...
加载拥挤度数据: congestion_2026-06-11.csv (131, 31)

[INFO] 基准日期对齐: 基准=2026-06-10 | width[29]=2026-06-10 | cong[28]=2026-06-10 | quotes=2026-06-11
  [WARN] 行情日期(2026-06-11)与基准日期(2026-06-10)不一致

[INFO] 3天前: 2026-06-05 (width_off=26, cong_off=25)

[INFO] 获取3天前(2026-06-05)行情数据...
  [生成] 正在获取 2026-06-05 的行情快照...
  [已保存] sw2_index_quotes_2026-06-05.csv (131)

有效行业: 131 个

[已保存] rank/sw2_score_rank_2026-06-12.csv

============================================================
  TOP 30 (综合得分从高到低)
============================================================
 排名         名称  综合得分  3天前得分
   1        种植业     78.5      65.2
   2          渔业     76.2      70.1
 ...

  得分变化 vs 3天前(2026-06-05): 上升45 / 持平38 / 下降48

============================================================
  BOTTOM 10
============================================================
 ...

============================================================
  得分分布
============================================================
  均值: 50.2  中位数: 51.3  最高: 88.7  最低: 12.4
  >=70: 25 个    >=60: 52 个    <30: 8 个
  三项均值: 价格=50.1  宽度=51.0  拥挤=49.3
```

---

## 配置参数

在文件顶部可调整：

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `W_PRICE` | 0.35 | 价格趋势权重 |
| `W_WIDTH` | 0.35 | 宽度趋势权重 |
| `W_CONG` | 0.30 | 拥挤度趋势权重 |
| `T_WEIGHTS` | `[0.4, 0.3, 0.2, 0.1]` | 时间衰减权重 (3/5/10/20日) |
| `T_OFFSETS` | `[3, 5, 10, 20]` | 对应的周期天数 |

**权重约束**: `W_PRICE + W_WIDTH + W_CONG ≈ 1.0`

---

## 依赖关系

### 外部依赖

| 包 | 用途 | 必需性 |
|----|------|--------|
| `pandas` | DataFrame 操作 | **必需** |
| `akshare` | 仅在缺少历史行情CSV时使用 | **按需** (自动import) |

### 内部依赖

```
sw2_score_rank.py
  ├── query_sw_index.py       (无 quotes/ CSV 时调)
  │       └── akshare (网络请求)
  ├── sw2_market_width_api.py  (无 width/congestion/ CSV 时调)
  │       └── legulegu.com HTTP API
  └── 本地 CSV 文件 (优先读取)
```

---

## 注意事项

1. **首次运行慢**: 如果 quotes/ 下没有任何CSV，会逐行业调 akshare 拉131个行业的K线数据，耗时约2~5分钟。之后有缓存秒级加载。

2. **非交易日**: width/congestion 数据来自 legulegu.com API（交易日更新），周末运行时最新数据可能是周五的。系统会自动用实际数据的日期命名输出文件。

3. **3天前行情缺失**: 如果3天前恰逢节假日且之前未跑过程序，会自动调 akshare 生成。如果仍失败（如akshare服务异常），3天前列显示为空。

4. **百分位分布特性**: 归一化后均值为50（近似），标准差约28.8（均匀分布）。>70表示前30%，>80表示前20%。

5. **与 action_report.py 区别**:
   - `action_report`: 场景分类（四象限），定性为主，关注"当前状态"
   - `sw2_score_rank`: 量化排名（连续分值），定量为主，关注"相对强弱"
   - 两者可互补使用：action_report看"该买/卖什么"，sw2_score_rank看"优先级排序"
