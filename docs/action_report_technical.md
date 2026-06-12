# action_report.py 技术文档

## 概述

**可操作分析报告生成器** — 基于"行业拥挤度 + 市场宽度 + 行情趋势"叠加分析框架，对申万二级行业进行场景分类、置信度评估和投资建议输出。

### 核心功能

| 功能 | 说明 |
|------|------|
| 四大场景分类 | 低拥挤+高宽度 / 高拥挤+高宽度 / 高拥挤+低宽度 / 低拥挤+低宽度 |
| 趋势信号识别 | 区分"趋势"(持续N天)与"偶发"(刚进入)信号 |
| 置信度评分 | 综合场景一致性(25) + 宽度趋势(15) + 价格趋势(15) + 拥挤度趋势(5)，满分100 |
| 一级行业归集 | 将131个二级行业映射到31个一级行业板块 |
| 自动数据拉取 | 缺失数据时自动调用 `sw2_market_width_api.py` 和 `query_sw_index.py` |

---

## 数据架构

### 输入数据源 (3个)

```
width/
  └── width_YYYY-MM-DD.csv        # 市场宽度: {代码, 名称, 2026-06-01, 2026-06-02, ...}
                                      # 每格 = value20 (0~100)

congestion/
  └── congestion_YYYY-MM-DD.csv    # 行业拥挤度: {代码, 名称, 2026-06-01_换手, 2026-06-01_成交额拥挤, ...}
                                      # 每格 = turnoverRateFQuantile / amountCongestionQuantile (0~100)

quotes/
  └── sw2_index_quotes_YYYY-MM-DD.csv  # 行情快照: {代码, 名称, 日期, 收盘价, 涨跌幅(%), 3日涨跌(%), ...}
```

### 输出

```
report/
  └── action_report_YYYY-MM-DD.txt   # 纯文本分析报告 (约900行)
```

---

## 核心算法

### 1. 基准日期对齐 `align_base_date()`

**问题**: width/congestion/quotes 三者的最新交易日可能不同（如周末、节假日）

**解决**: 取三者交集的最近日期作为统一基准

```python
# 算法:
common_wc = sorted(set(dates_w) & set(dates_c), reverse=True)
base_date = common_wc[0]                    # 共同最新交易日
offset_w  = dates_w.index(base_date)         # width中该日期的位置索引
offset_c  = dates_c.index(base_date)         # congestion中该日期的位置索引
```

取值时使用对齐后的 offset：
```python
v_w = width_data[base_offset_w]    # 非硬编码 [0]
v_c = cong_data[base_offset_c]     # 非硬编码 [0]
```

**返回值结构**:

| 字段 | 类型 | 说明 |
|------|------|------|
| base_date | str | 共同基准日期 "YYYY-MM-DD" |
| offset_w | int | width数组中的位置 |
| offset_c | int | congestion数组中的位置 |
| actual_w | str | width实际使用的日期 |
| actual_c | str | congestion实际使用的日期 |
| quotes_aligned | bool | 行情是否与基准一致 |

---

### 2. 四大场景矩阵

```
                 宽度 >= 60          宽度 < 60
            ┌─────────────┬─────────────┐
拥挤度 >= 60 │ 场景2:持有减仓│ 场景3:高危见顶│
            │  (黄/控仓)    │  (红/卖出)   │
拥挤度 < 60 │ 场景1:最佳做多│ 场景4:弱势磨底│
            │  (绿/重仓)    │  (蓝/观望)   │
            └─────────────┴─────────────┘
```

**阈值常量** (可调):
- `WIDTH_THRESHOLD = 60`     — 市场宽度高/低分界
- `CONGESTION_THRESHOLD = 60` — 拥挤度高/低分界
- `HISTORY_DAYS = 7`          — 场景一致性回看天数

---

### 3. 置信度计算 `calc_confidence(row)` (0~100分)

基础分 50，按以下规则加减：

| 维度 | 权重范围 | 规则 |
|------|----------|------|
| **场景一致性** | +0 ~ +25 | 一致性越高加分越多 (`consistency * 25`) |
| **宽度趋势** | -10 ~ +15 | 场景1/2且上升 → +15/+10; 场景3/4且下降 → +15/+10 |
| **价格趋势** | -10 ~ +15 | 场景1/2且多周期向上 → +5~+10; 场景3全周期下跌 → +15 |
| **拥挤度趋势** | -5 ~ +5 | 场景2过热加速 → -5; 场景3风险释放 → +5 |

---

### 4. 信号类型判定 `signal_type()`

| 条件 | 类型 | 标记 | 含义 |
|------|------|------|------|
| consistency >= 0.6 且 abs(trend) >= 2 | 趋势 | ★ | 过去7天一致，可靠信号 |
| consistency >= 0.4 | 偏稳 | ☆ | 有一定持续性 |
| 其他 | 偶发 | ○ | 今日刚进入，需观察确认 |

---

## 函数清单

### 数据加载层

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `_find_latest_csv(dir, prefix)` | str or None | 在目录找最新CSV |
| `load_width_data()` | dict | 加载宽度CSV → 内部dict格式，无则调API |
| `load_congestion_data()` | dict | 加载拥挤度CSV → 内部dict格式，无则调API |
| `get_latest_quotes_csv()` | DataFrame | 加载行情CSV，无则调 query_sw_index.py |
| `build_quotes_map(df)` | dict | 构建 `{名称: {chg_3d, chg_5d, ...}}` 映射 |
| `align_base_date(dw, dc, qd?)` | dict | 对齐三数据源的基准日期 |

### 分析计算层

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `get_history_width(code, days)` | list[float] | 提取近N天宽度序列 |
| `get_history_congestion(code, days)` | list[float] | 提取近N天拥挤度序列 |
| `calc_trend(history)` | float | 计算日均变化量 (线性回归斜率) |
| `calc_scene_consistency(history)` | float | 场景颜色的一致性占比 (0~1) |
| `classify_width(val)` | (str, int) | 高/低 分类 |
| `classify_congestion(val)` | (str, int) | 高/低 分类 |
| `get_scene(w, c)` | (str, str, str) | 四象限场景判断 |
| `calc_confidence(row)` | int | 置信度 0~100 |
| `signal_type(consistency, trend)` | (str, str) | 趋势/偏稳/偶发 判定 |
| `price_trend_label(chg3, chg5, chg10)` | str | 价格趋势文字标签 |
| `print_scene_table(...)` | None | 输出场景详情表格到lines |

### 申万一级映射

| 函数 | 说明 |
|------|------|
| `SW1_MAP` (常量) | 31个一级行业 → 关键词列表的映射字典 |
| `get_sw1(name)` | 根据二级行业名返回所属一级行业 |

---

## 报告输出结构

生成的 TXT 文件共9个章节：

```
============================================================
  申万二级行业可操作分析报告 (增强版 v2)
============================================================

一、市场总体状态
   - 【市场宽度】均值/状态/趋势/强势弱势行业数
   - 【市场拥挤度】均值/高拥挤/低拥挤行业数
   - 【行情趋势】5日/10日/20日平均涨跌
   - 【信号类型统计】趋势 vs 偶发
   - 【当前市场判断】综合状态描述

二、四大场景分布
   各场景总数/趋势数/置信度 + 信号含义说明

三~六、各场景详情表 (TOP 10~15)
   信号 | 一级行业 | 二级行业 | 宽度 | 拥挤 | 置信 | 3日% | 5日% | ... | 价格趋势

七、一级行业场景分布矩阵
   一级行业 | S1 | S2 | S3 | S4 | 总数 | 主导场景 | 趋势/偶发

八、综合投资建议
   ★★★ 趋势信号的做多/控仓/规避建议
   ○ 偶发信号的观察建议

九、分析框架速查表
   四象限操作指南 + 当前市场总结
```

---

## 使用方法

```bash
# 直接运行 (自动从各文件夹加载数据)
python action_report.py

# 输出位置
report/action_report_2026-06-12.txt
```

### 数据依赖链

```
action_report.py
  ├── sw2_market_width_api.py (无 width/ CSV 时自动调)
  │       ├── width/width_*.csv
  │       └── congestion/congestion_*.csv
  └── query_sw_index.py (无 quotes/ CSV 时自动调)
          └── quotes/sw2_index_quotes_*.csv
```

---

## 注意事项

1. **日期对齐**: 当 width/congestion/quotes 的数据日期不一致时，系统会自动找到共同可用日期并给出警告
2. **空数据处理**: 行情匹配失败时该行业的涨跌幅显示为0.00%，报告中会打印 `[WARNING]`
3. **非交易日**: 周末运行时会使用最近的交易日数据，报告日期仍为当天
