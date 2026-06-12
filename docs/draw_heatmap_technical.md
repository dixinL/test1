# draw_heatmap.py 技术文档

## 概述

**热力图生成器** — 从本地 CSV 数据读取市场宽度/行业拥挤度矩阵，生成 PNG 静态图片或 HTML 交互式热力图页面。

### 核心功能

| 功能 | 说明 |
|------|------|
| CSV 数据加载 | 优先读本地文件，无数据自动调 `sw2_market_width_api.py` |
| 矩阵提取 | 调用 `sw2_market_width_api.extract_matrix_from_csv()` 解析 CSV |
| 双格式输出 | PNG (Pillow) + HTML (纯手写，无JS依赖) |
| 日期对齐 | 输出文件名使用实际数据的日期（而非当天） |
| 批量处理 | 默认同时生成 width + congestion 两种热力图 |

---

## 数据架构

### 输入

```
width/
  └── width_2026-06-10.csv           # 市场宽度原始数据
        列: 代码, 名称, 2026-05-07, 2026-05-08, ... (30个日期列)

congestion/
  └── congestion_2026-06-10.csv      # 行业拥挤度原始数据
        列: 代码, 名称, 2026-05-07_换手, 2026-05-07_成交额拥挤, ...
```

### 输出 (与数据同目录)

```
width/
  ├── width_2026-06-10.csv           # 数据
  ├── width_2026-06-10_heatmap.png    # 热力图图片
  └── width_2026-06-10_heatmap.html   # 热力图网页

congestion/
  ├── congestion_2026-06-10.csv       # 数据
  ├── congestion_2026-06-10_heatmap.png
  └── congestion_2026-06-10_heatmap.html
```

---

## 核心算法

### 1. 颜色方案 `_seq_color(val, vmax=100)`

顺序型 蓝→黄 配色方案：

```
值(vmax)     颜色          RGB              含义
─────────   ────────────   ──────────────    ──────
100         深蓝          (0, 0, 255)      偏高
75          蓝紫过渡                        ...
50          黄            (255, 255, 0)    中等
25          橙黄过渡                        ...
0           浅灰          (245,245,245)    空值/无数据
```

**分段函数**:
```python
ratio = val / vmax
if ratio >= 0.5:      # 50~100%: 黄 → 蓝
    r = 255 * (1 - t), g = 255 * (1 - t), b = 255 * t
else:                 # 0~50%:  橙 → 黄
    r = 255, g = 128+127*t, b = 0
```

**文字颜色** (`_text_color`):
- `val >= 60`: 白字 (#FFFFFF) — 深色背景上可见
- `val > 0 且 < 60`: 黑字 (#333333)
- `val <= 0`: 灰字 (#999999)

---

### 2. 自动数据拉取 `load_data()`

```
load_data(data_type="width")
  │
  ├─ 有CSV? ─是→ extract_matrix_from_csv() + 返回 (matrix,..., data_date)
  │
  └─ 无CSV? ─→ subprocess.call(sw2_market_width_api.py [width])
                  │
                  ├─ 成功 → 重新查找CSV → 加载返回
                  └─ 失败 → 打印错误信息 → return None
```

**关键**: 返回值新增第6元素 `data_date` — 从 CSV 文件名中提取的实际日期字符串。确保输出文件名与数据日期一致。

---

### 3. 输出路径构建 `get_output_paths()`

```python
# 文件命名规则: {类型}_{数据日期}_heatmap.{后缀}
width_2026-06-10_heatmap.png
congestion_2026-06-10_heatmap.html
```

**参数**:
- `data_type`: `"width"` 或 `"congestion"` — 决定目标目录和前缀
- `ext`: `"png"` / `"html"`
- `data_date`: 实际数据日期（非当天），默认用 `date.today()`

---

## 函数清单

### 颜色模块

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `_seq_color(val, vmax)` | (R,G,B) tuple | 数值→RGB颜色 |
| `_get_color_hex(val, vmax)` | str | RGB→CSS rgb() 字符串 |
| `_get_text_col(val, vmax)` | str | 数值→CSS文字颜色 |
| `_rgb_color(val, vmax)` | (R,G,B) | 别名 (PNG用) |
| `_text_color(val, vmax)` | (R,G,B) | 别名 (PNG用) |

### 图表生成模块

| 函数 | 参数 | 说明 |
|------|------|------|
| `generate_html(matrix, row_names, dates, col_labels, vmax, title, output_path)` | 全部必要 | 生成HTML交互热力图 |
| `generate_png(matrix, row_names, dates, col_labels, vmax, title, output_path)` | 全部必要 | 用Pillow生成PNG静态热力图 |
| `_load_font(size, fallback?)` | size: int | 加载系统字体 (msyh.ttc/simhei.ttf) |

### 数据加载模块

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `_find_latest_csv(dir, prefix)` | str or None | 查找最新CSV文件 |
| `load_data(data_type)` | tuple(6) or None | 加载数据 + 提取矩阵 + 日期提取 |
| `_extract_date_from_filename(filename)` | str | 从文件名正则提取YYYY-MM-DD |
| `get_output_paths(type, ext, date?)` | str | 构建完整输出路径 |

### 主流程 & CLI

| 函数/变量 | 说明 |
|-----------|------|
| `run(data_type, output_format)` | 主流程: 加载→生成→输出 |
| `parse_args(argv)` | 解析命令行参数 |
| `print_usage()` | 打印帮助信息 |

---

## HTML 热力图结构

生成的 HTML 是**单文件、无外部依赖**的完整页面：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <style>
    /* 关键特性:
     * 表头 sticky 定位 (滚动时固定)
     * 行首标签 sticky-left 定位
     * hover 高亮整行
     * 响应式 overflow-x auto */
    th { position: sticky; top: 0; z-index: 2 }
    .row-label { position: sticky; left: 0; z-index: 1 }
  </style>
</head>
<body>
  <div class="container">
    <h1>标题</h1>
    <div class="subtitle">日期范围 | 交易日数 | 行业数</div>
    <table>
      <!-- 行首+行尾都有行业名称 -->
      <!-- 单元格内联 style="background:rgb(...)" -->
    </table>
    <div class="legend">偏低 ← [渐变条] → 偏高</div>
  </div>
</body>
```

---

## PNG 热力图布局

```
┌──────────────────────────────────────────────────┐
│                   标题 (居中)                     │
│               副标题 (日期范围)                    │
├──────┬────┬────┬────┬────┬────┬────┬────┬────────┤
│      │ d1 │ d2 │ d3 │ ... │ d30│        │        │
├──────┼────┼────┼────┼────┼────┼────┼────┼────────┤
│ 种植业│ 42 │ 38 │ 45 │    │ 55│ ...    │ 种植业  │
│ 半导体│ 78 │ 82 │ 80 │    │ 71│ ...    │ 半导体  │
│  ... │    │    │    │    │    │        │   ...   │
├──────┴────┴────┴────┴────┴────┴────┴────┴────────┤
│  图例: 低[====渐变条===高]                       │
│  数据来源: legulegu.com | 更新时间: YYYY-MM-DD    │
└──────────────────────────────────────────────────┘
```

**尺寸计算** (以131行业×30天为例):
- 左标签宽: 115px, 右标签宽: 115px
- 单元格: 36×24 px
- 图片总宽 ≈ 1500px, 总高 ≈ 3700px

---

## 使用方法

```bash
# 默认: 宽度+拥挤度 都生成 PNG
python draw_heatmap.py

# 仅宽度 HTML
python draw_heatmap.py width html

# 仅拥挤度 两者都生成
python draw_heatmap.py congestion both

# 指定多个类型
python draw_heatmap.py width congestion html

# 帮助
python draw_heatmap.py --help / -h
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `[数据类型...]` | `width congestion` (全部) | 可指定一个或多个 |
| `[输出格式]` | `png` | png / html / both |

### 示例输出

```
============================================================
  热力图生成 | 数据: width, congestion | 输出: PNG图片
============================================================

[1/2] 加载数据...
[数据源] width_2026-06-10.csv (width)
  尺寸: 131行 x 30列 | 范围: 0~92 | 数据日期: 2026-06-10

[2/2] 生成图表...
[PNG] width/width_2026-06-10_heatmap.png (1520x3748)

[1/2] 加载数据...
[数据源] congestion_2026-06-10.csv (congestion)
  尺寸: 131行 x 30列 | 范围: 0~98 | 数据日期: 2026-06-10

[2/2] 生成图表...
[PNG] congestion/congestion_2026-06-10_heatmap.png (1520x3748)

============================================================
  全部完成!
```

---

## 依赖关系

### 外部依赖

| 包 | 用途 | 必需性 |
|----|------|--------|
| `Pillow` | PNG 生成 | **可选** (无则跳过PNG) |
| `sw2_market_width_api` | 矩阵提取函数 + 图表标题 | **必需** |

### 内部依赖

```
draw_heatmap.py
  └── sw2_market_width_api.py
        ├── extract_matrix_from_csv(csv_path, data_type)
        │       返回: (matrix, row_names, dates, col_labels, vmax)
        └── get_chart_title(data_type, dates)
                返回: "均线市场宽度 (MA20)\n2026-05-07 ~ 2026-06-10"
```

### 自动拉取链路 (无本地CSV时)

```
draw_heatmap.py
  └── subprocess → sw2_market_width_api.py [width|congestion]
        └── HTTP GET legulegu.com/api/stockdata/...
                └── _save_to_dir(width|congestion/, *.csv)
                      └── draw_heatmap 重新查找并加载
```

---

## 注意事项

1. **日期一致性**: 输出文件的日期来自底层CSV数据文件名，不是"今天"。确保图和数据对应。
2. **字体要求**: PNG生成需要系统中存在 `msyh.ttc`(微软雅黑) 或 `simhei.ttf`(黑体)，否则使用默认字体可能中文乱码。
3. **大尺寸警告**: 131个行业×30天的热力图 PNG 约 1500×3700px，浏览器打开时建议缩小查看。
4. **HTML兼容**: 生成的HTML为纯原生实现，无需任何前端框架，所有现代浏览器可直接打开。
