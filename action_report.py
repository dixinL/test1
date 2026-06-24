# -*- coding: utf-8 -*-
"""
可操作分析报告生成器
基于"行业拥挤度 + 市场宽度"叠加分析框架
参考: approach.txt 中的四大经典场景理论
增强: 历史趋势分析 + 行情涨跌幅趋势参考(3/5/10/20日)
"""

import json
import os
import sys
import subprocess
import pandas as pd
from datetime import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据存储目录（与 sw2_market_width_api.py 一致）
WIDTH_DIR = os.path.join(BASE_DIR, "width")
QUOTES_DIR = os.path.join(BASE_DIR, "quotes")
CONGESTION_DIR = os.path.join(BASE_DIR, "congestion")

# ============================================================
# 配置参数
# ============================================================
HISTORY_DAYS = 7  # 参考历史天数
WIDTH_THRESHOLD = 60  # 宽度阈值
CONGESTION_THRESHOLD = 60  # 拥挤度阈值
STABILITY_THRESHOLD = 0.5

# ============================================================
# 0a. 从 width/ 或 result.json 加载市场宽度数据
# ============================================================

def _find_csv_by_date(directory, prefix, target_date):
    """在目录中查找指定日期的CSV文件"""
    if not os.path.isdir(directory):
        return None
    target_name = "{}{}.csv".format(prefix, target_date)
    path = os.path.join(directory, target_name)
    return path if os.path.exists(path) else None


def _find_latest_csv(directory, prefix):
    """在目录中查找最新的CSV文件 (回退用)"""
    if not os.path.isdir(directory):
        return None
    csvs = sorted([f for f in os.listdir(directory) if f.startswith(prefix) and f.endswith(".csv")], reverse=True)
    if csvs:
        return os.path.join(directory, csvs[0])
    return None


def _try_load_or_fetch(directory, prefix, api_type, parse_fn, fetch_args=None):
    """
    通用加载逻辑: 当天 → 调API → 再试当天(可能非交易日) → 回退最新

    参数:
        directory:   数据目录
        prefix:     文件名前缀 (如 "width_")
        api_type:    传给 sw2_market_width_api.py 的参数 ("width"/"congestion") 或 None表示不调API
        parse_fn:     (df, path) -> data_dict 的解析函数
        fetch_args:   调 query_sw_index.py 时的额外参数列表 (可选)

    返回: data_dict
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 先找当天
    path = _find_csv_by_date(directory, prefix, today_str)
    if path:
        df = pd.read_csv(path, encoding="utf-8-sig")
        print("[命中] {} ({})".format(os.path.basename(path), directory))
        return parse_fn(df, path)

    # 2) 无当天数据 → 调接口拉取
    print("[INFO] {}/ 下无今日({})数据，正在拉取...".format(directory, today_str))

    # 区分两种 API 来源
    if api_type:
        import subprocess as _sp
        _sp.run([sys.executable, os.path.join(BASE_DIR, "sw2_market_width_api.py"), api_type],
               cwd=BASE_DIR)
    elif fetch_args:
        import subprocess as _sp
        result = _sp.run(fetch_args, cwd=BASE_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            print("[ERROR] 拉取失败: {}".format(result.stderr[-200:]))

    # 3) 再试当天 (API返回的可能不是今天，而是最近交易日)
    path = _find_csv_by_date(directory, prefix, today_str)
    if path:
        df = pd.read_csv(path, encoding="utf-8-sig")
        print("[已获取] {} ({})".format(os.path.basename(path), directory))
        return parse_fn(df, path)

    # 4) 回退到最新的
    path = _find_latest_csv(directory, prefix)
    if path:
        df = pd.read_csv(path, encoding="utf-8-sig")
        print("[回退] {} (使用历史最新)".format(os.path.basename(path)))
        return parse_fn(df, path)

    print("[ERROR] {}/ 下无任何数据!".format(directory))
    sys.exit(1)


# ---- width 解析器 ----
def _parse_width_df(df, path):
    """解析宽度CSV为内部dict格式"""
    data = {"dates": [], "swCodeNames": [], "maMarketWidth": {}}
    raw_date_cols = [c for c in df.columns if c not in ("代码", "名称") and not c.startswith(("换手", "成交"))]
    data["dates"] = raw_date_cols
    for _, row in df.iterrows():
        code = str(row.get("code", row.get("代码", "")))
        name = row.get("名称", "")
        if code and name:
            data["swCodeNames"].append({"indexCode": code, "indexName": name})
            values = [{"value20": int(row.get(d, 0)) if isinstance(row.get(d, 0), (int, float)) else 0} for d in raw_date_cols]
            data["maMarketWidth"][code] = values
    print("  转换: {} 行业, {} 天".format(len(data["swCodeNames"]), len(raw_date_cols)))
    return data


# ---- congestion 解析器 ----
def _parse_congestion_df(df, path):
    """解析拥挤度CSV为内部dict格式"""
    data = {"dates": [], "swCodeNames": [], "congestions": {}}
    all_cols = [c for c in df.columns if c not in ("代码", "名称")]
    date_set = set()
    for c in all_cols:
        base = c.rsplit("_", 1)[0]
        date_set.add(base)
    dates = sorted(date_set)
    data["dates"] = dates
    for _, row in df.iterrows():
        code = str(row.get("code", row.get("代码", "")))
        name = row.get("名称", "")
        if code and name:
            data["swCodeNames"].append({"indexCode": code, "indexName": name})
            values = []
            for d in dates:
                t_col = "{}_换手".format(d); a_col = "{}_成交额拥挤".format(d)
                t = row.get(t_col, 0); a = row.get(a_col, 0)
                values.append({
                    "turnoverRateFQuantile": float(t) if str(t) not in ("", "-", "nan") else 0,
                    "amountCongestionQuantile": float(a) if str(a) not in ("", "-", "nan") else 0,
                })
            data["congestions"][code] = values
    print("  转换: {} 行业, {} 天".format(len(data["swCodeNames"]), len(dates)))
    return data


def load_width_data():
    """加载市场宽度数据: 优先今天 -> 调API -> 回退最新"""
    return _try_load_or_fetch(WIDTH_DIR, "width_", "width", _parse_width_df)


def load_congestion_data():
    """加载拥挤度数据: 优先今天 -> 调API -> 回退最新"""
    return _try_load_or_fetch(CONGESTION_DIR, "congestion_", "congestion", _parse_congestion_df)


def get_latest_quotes_csv():
    """查找最新的行情CSV，没有则调接口生成"""
    quotes_dir = os.path.join(BASE_DIR, "quotes")
    os.makedirs(quotes_dir, exist_ok=True)

    # 查找已有的CSV文件
    csv_files = []
    if os.path.isdir(quotes_dir):
        for f in os.listdir(quotes_dir):
            if f.startswith("sw2_index_quotes_") and f.endswith(".csv"):
                csv_files.append(f)

    if csv_files:
        csv_files.sort(reverse=True)
        csv_path = os.path.join(quotes_dir, csv_files[0])
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        csv_date = str(df.iloc[0]["日期"])[:10] if not df.empty else "unknown"
        print("加载行情数据: {} (日期: {})".format(csv_files[0], csv_date))
        return df

    # 没有CSV，自动调接口生成
    print("未找到行情CSV，自动调用 query_sw_index.py 生成...")
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "query_sw_index.py"), "--summary"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        print("[ERROR] 行情查询失败:", result.stderr[-300:])
        return None

    # 重新查找
    csv_files = [f for f in os.listdir(quotes_dir)
                 if f.startswith("sw2_index_quotes_") and f.endswith(".csv")]
    if csv_files:
        csv_files.sort(reverse=True)
        csv_path = os.path.join(quotes_dir, csv_files[0])
        return pd.read_csv(csv_path, encoding="utf-8-sig")
    return None


def build_quotes_map(quotes_df):
    """构建 名称->行情数据 的映射字典"""
    if quotes_df is None or quotes_df.empty:
        print("[WARN] 行情DataFrame为空!")
        return {}
    # 标准化列名: 去掉可能的BOM前缀和空白
    clean_cols = {}
    for c in quotes_df.columns:
        clean_cols[c.strip().lstrip('\ufeff')] = c
    name_map = {}
    matched = 0
    for _, row in quotes_df.iterrows():
        # 兼容不同列名
        raw_name = row.get(clean_cols.get("名称", "名称"), "")
        name = str(raw_name).strip() if raw_name is not None and str(raw_name) != "nan" else ""
        if not name:
            continue
        def _get(col_key):
            actual_col = clean_cols.get(col_key, col_key)
            v = row.get(actual_col)
            try:
                return float(v) if v is not None and str(v).strip() not in ("-", "", "nan") else 0.0
            except (ValueError, TypeError):
                return 0.0

        entry = {
            "chg_1d": _get("涨跌幅(%)"),
            "chg_3d": _get("3日涨跌(%)"),
            "chg_5d": _get("5日涨跌(%)"),
            "chg_10d": _get("10日涨跌(%)"),
            "chg_20d": _get("20日涨跌(%)"),
            "close": _get("收盘价"),
        }
        name_map[name] = entry
        matched += 1
    print("  行情映射: {} 个行业".format(matched))
    # 调试: 显示第一个条目确认数据正常
    if name_map:
        sample = next(iter(name_map.items()))
        print("  样例: {} -> 3日={:+.2f}% 5日={:+.2f}%".format(sample[0], sample[1]["chg_3d"], sample[1]["chg_5d"]))
    return name_map


def _safe_float(row, col):
    """安全取浮点数，- 之类的转0"""
    try:
        v = row.get(col, 0)
        return float(v) if v != "-" and v is not None else 0.0
    except (ValueError, TypeError):
        return 0.0



# ============================================================
# 1. 加载API数据 (优先CSV，回退JSON)
# ============================================================
print("加载数据...")
w = load_width_data()
c = load_congestion_data()

# ---- 统一基准日期: 三个数据源对齐 ----
# width/congestion/quotes 的最新交易日可能不同, 必须用共同可用的最新日期作为基准
def align_base_date(dates_w, dates_c, quotes_date=None):
    """
    对齐多个数据源的基准日期。
    
    参数:
        dates_w:     width CSV 中的日期列列表
        dates_c:     congestion CSV 中的日期列列表  
        quotes_date: quotes CSV 中的数据日期 (字符串, 如 "2026-06-10"), 可选
    
    返回:
        dict {
            "base_date":       共同最新的基准日期 (str),
            "offset_w":        width 数据中 base_date 对应的索引 (int),
            "offset_c":        congestion 数据中 base_date 对应的索引 (int),
            "quotes_aligned":  bool,   行情日期是否与基准一致,
            "dates_info":      str,    用于打印的说明信息
        }
    """
    set_w, set_c = set(dates_w), set(dates_c)
    common_wc = sorted(set_w & set_c, reverse=True)  # width ∩ congestion
    
    if common_wc:
        base = common_wc[0]
    else:
        # 无交集, 取较晚的那个
        latest_w = dates_w[-1] if dates_w else ""
        latest_c = dates_c[-1] if dates_c else ""
        base = max(latest_w, latest_c)
    
    # 各自在base_date的offset (如果base不在该数据源中, 取最近的前一个日期)
    off_w = dates_w.index(base) if base in set_w else (len(dates_w) - 1)
    off_c = dates_c.index(base) if base in set_c else (len(dates_c) - 1)
    
    # 实际使用的日期(可能因为无交集而回退)
    actual_w = dates_w[off_w]
    actual_c = dates_c[off_c]
    
    # 行情是否对齐
    q_aligned = True
    q_info = ""
    if quotes_date:
        q_aligned = (quotes_date == base or quotes_date == actual_w or quotes_date == actual_c)
        q_info = ", quotes={}".format(quotes_date)
    
    info = "基准={} | width[{}]={} | cong[{}]={}{}".format(
        base, off_w, actual_w, off_c, actual_c, q_info)
    
    return {
        "base_date": base,
        "offset_w": off_w,
        "offset_c": off_c,
        "actual_w": actual_w,
        "actual_c": actual_c,
        "quotes_aligned": q_aligned,
        "info": info,
    }


# 行业映射
code_to_name = {}
for item in w["swCodeNames"]:
    code_to_name[item["indexCode"]] = item["indexName"]
for item in c["swCodeNames"]:
    if item["indexCode"] not in code_to_name:
        code_to_name[item["indexCode"]] = item["indexName"]

# 加载行情CSV
quotes_df = get_latest_quotes_csv()
name_to_quotes = build_quotes_map(quotes_df)

# ---- 执行日期对齐 (三个数据源) ----
def align_base_date(dates_w, dates_c, quotes_date=None):
    """对齐多个数据源的基准日期, 返回各数据源的offset和实际使用日期"""
    set_w, set_c = set(dates_w), set(dates_c)
    common_wc = sorted(set_w & set_c, reverse=True)
    
    if common_wc:
        base = common_wc[0]
    else:
        latest_w = dates_w[-1] if dates_w else ""
        latest_c = dates_c[-1] if dates_c else ""
        base = max(latest_w, latest_c)
    
    off_w = dates_w.index(base) if base in set_w else (len(dates_w) - 1)
    off_c = dates_c.index(base) if base in set_c else (len(dates_c) - 1)
    
    actual_w = dates_w[off_w]
    actual_c = dates_c[off_c]
    
    q_aligned = True
    q_info = ""
    if quotes_date:
        q_aligned = (quotes_date == base or quotes_date == actual_w or quotes_date == actual_c)
        q_info = ", quotes={}".format(quotes_date)
    
    info = "基准={} | width[{}]={} | cong[{}]={}{}".format(
        base, off_w, actual_w, off_c, actual_c, q_info)
    
    return {
        "base_date": base,
        "offset_w": off_w,
        "offset_c": off_c,
        "actual_w": actual_w,
        "actual_c": actual_c,
        "quotes_aligned": q_aligned,
        "info": info,
    }


quotes_date_str = None
if quotes_df is not None and not quotes_df.empty:
    quotes_date_str = str(quotes_df.iloc[0]["日期"])[:10]

aligned = align_base_date(w["dates"], c["dates"], quotes_date=quotes_date_str)
latest_date = aligned["base_date"]
base_offset_w = aligned["offset_w"]
base_offset_c = aligned["offset_c"]

print("[日期对齐] {}".format(aligned["info"]))
if not aligned["quotes_aligned"]:
    print("  [WARN] 行情日期与基准不一致, 分析时请注意")

# ============================================================
# 2. 历史数据提取函数
# ============================================================

def get_history_width(code, days=HISTORY_DAYS):
    if code not in w["maMarketWidth"]:
        return []
    values = w["maMarketWidth"][code]
    history = []
    for i in range(min(days, len(values))):
        v = values[i]
        if isinstance(v, dict):
            history.append(v.get("value20", 0))
        else:
            history.append(0)
    return history


def get_history_congestion(code, days=HISTORY_DAYS):
    """获取历史拥挤度 (只用 turnoverRateFQuantile 换手率分位数)"""
    if code not in c["congestions"]:
        return []
    values = c["congestions"][code]
    history = []
    for i in range(min(days, len(values))):
        v = values[i]
        if isinstance(v, dict):
            t = v.get("turnoverRateFQuantile", 0) or 0
            history.append(t)
        else:
            history.append(0)
    return history


def calc_trend(history):
    if len(history) < 2:
        return 0
    diffs = [history[i + 1] - history[i] for i in range(len(history) - 1)]
    return sum(diffs) / len(diffs) if diffs else 0


def calc_scene_consistency(scene_history):
    if not scene_history:
        return 0
    from collections import Counter
    counts = Counter(scene_history)
    if not counts:
        return 0
    return counts.most_common(1)[0][1] / len(scene_history)


# ============================================================
# 3. 场景判断规则
# ============================================================

# 三分法阈值
W_LOW, W_MID, W_HIGH = 0, 50, 80   # 宽度: 低(<50) / 中(50~80) / 高(>=80)
C_LOW, C_MID, C_HIGH = 0, 50, 80   # 拥挤度: 低(<50) / 中(50~80) / 高(>=80)


def classify_width(w_val):
    """三分法宽度分类: 返回 (等级名, 数字编码)"""
    if w_val >= W_HIGH:
        return ("高", 2)
    elif w_val >= W_MID:
        return ("中", 1)
    else:
        return ("低", 0)


def classify_congestion(c_val):
    """三分法拥挤度分类: 返回 (等级名, 数字编码)"""
    if c_val >= C_HIGH:
        return ("高", 2)
    elif c_val >= C_MID:
        return ("中", 1)
    else:
        return ("低", 0)


# ============================================================
# 3x3 场景矩阵 (9个场景)
# 行: 宽度(低/中/高), 列: 拥挤度(低/中/高)
#
#           拥挤<50     拥挤50~80   拥挤>=80
#         ┌─────────┬──────────┬─────────┐
# 宽度>=80│ S1 强势  │ S2 加速   │ S3 极端  │
# 宽度50~80│ S4 回暖  │ S5 盘整   │ S6 警惕  │
# 宽度<50 │ S7 冰封  │ S8 探底   │ S9 出逃  │
# ============================================================
SCENE_MATRIX = {
    # w=高(2), c=低(0)
    (2, 0): ("S1:强势启动", "#22c55e", "积极做多"),      # 绿色-强
    # w=高(2), c=中(1)
    (2, 1): ("S2:加速上行", "#84cc16", "追多控仓"),       # 浅绿
    # w=高(2), c=高(2)
    (2, 2): ("S3:极端过热", "#eab308", "逢高减仓"),       # 黄色-警告

    # w=中(1), c=低(0)
    (1, 0): ("S4:回暖蓄势", "#06b6d4", "逢低吸纳"),       # 青
    # w=中(1), c=中(1)
    (1, 1): ("S5:盘整选择", "#8b5cf6", "耐心等待"),       # 紫
    # w=中(1), c=高(2)
    (1, 2): ("S6:高位滞涨", "#f97316", "逐步撤离"),       # 橙-警示

    # w=低(0), c=低(0)
    (0, 0): ("S7:冰封观望", "#3b82f6", "远离观望"),       # 蓝
    # w=低(0), c=中(1)
    (0, 1): ("S8:探底企稳", "#6366f1", "密切跟踪"),       # 靛蓝
    # w=低(0), c=高(2)
    (0, 2): ("S9:恐慌出逃", "#ef4444", "坚决规避"),       # 红-危险
}


def get_scene(w, c_val):
    """三分法9场景判断"""
    if c_val is None:
        return "数据缺失(拥挤度)", "gray", ""
    _, w_num = classify_width(w)
    _, c_num = classify_congestion(c_val)

    key = (w_num, c_num)
    if key in SCENE_MATRIX:
        name, color, action = SCENE_MATRIX[key]
        return name, color, action
    else:
        return "未知场景", "gray", ""


def calc_width_confidence(row):
    """
    宽度维度置信度 (0-100) - 三分法9场景版

    评估项:
      - 场景一致性 (颜色稳定性): 0~25分
      - 宽度趋势方向与场景是否匹配: 0~35分
      - 价格趋势(行情数据)交叉验证: 0~40分
    """
    conf = 30  # 基础分
    scene = row.get("scene", "")
    sc = row.get("scene_consistency", 0.5)
    wt = row.get("width_trend", 0)

    # ---- 1. 场景一致性 (0~25) ----
    conf += sc * 25

    # ---- 2. 宽度趋势方向匹配 (0~35 / -10~-20) ----
    # S1/S2/S3 (高宽度): 应该向上
    if "S1:" in scene or "S2:" in scene or "S3:" in scene:
        if wt >= 4:
            conf += 35
        elif wt >= 2:
            conf += 28
        elif wt > 0:
            conf += 18
        elif wt <= -4:
            conf -= 20
        elif wt <= -2:
            conf -= 12
        elif wt < 0:
            conf -= 6
    # S7/S8/S9 (低宽度): 应该向下或持平
    elif "S7:" in scene or "S9:" in scene:
        if wt <= -4:
            conf += 35
        elif wt <= -2:
            conf += 28
        elif wt < 0:
            conf += 18
        elif wt >= 4:
            conf -= 20       # 弱势中反而扩张=异常
        elif wt >= 2:
            conf -= 12
        elif wt > 0:
            conf -= 6
    # S4/S5/S6 (中宽度): 允许小幅波动
    elif "S4:" in scene:
        # 回暖蓄势: 向上为正
        if wt >= 2:
            conf += 28
        elif wt > 0:
            conf += 18
        elif wt <= -2:
            conf -= 10
    elif "S5:" in scene or "S6:" in scene:
        # 盘整/滞涨: 向下略好(回归合理), 向上需谨慎
        if wt >= 4:
            conf -= 15
        elif wt >= 2:
            conf -= 8
        elif wt <= -2:
            conf += 10
        elif wt <= 0:
            conf += 5

    # ---- 3. 价格趋势验证 (0~40) ----
    chg_3d = row.get("chg_3d", 0)
    chg_5d = row.get("chg_5d", 0)
    chg_10d = row.get("chg_10d", 0)
    chg_20d = row.get("chg_20d", 0)

    # S1/S2 (强势/做多区间): 价格应向上
    if "S1:" in scene or "S2:" in scene:
        if chg_3d > 0 and chg_5d > 0 and chg_10d > 0:
            conf += 40
        elif chg_5d > 0 and chg_10d > 0:
            conf += 30
        elif chg_10d > 0 and chg_20d > 0:
            conf += 18
        elif chg_3d < -5 and chg_5d < -3:
            conf -= 15
        elif chg_5d < 0:
            conf -= 8
    # S3/S6 (过热/滞涨): 价格走弱=确认见顶
    elif "S3:" in scene or "S6:" in scene:
        if chg_5d < 0 and chg_10d < 0 and chg_20d < 0:
            conf += 40
        elif chg_5d < 0 and chg_10d < 0:
            conf += 30
        elif chg_3d > 0:
            conf -= 15        # 短期反弹=诱多
        elif chg_5d > 0:
            conf -= 8
    # S4/S5/S8 (回暖/盘整/探底): 价格企稳=正面信号
    elif "S4:" in scene:
        if chg_5d > 0 and chg_10d > 0:
            conf += 25        # 确认回升
        elif chg_5d > 0 and chg_10d < 0:
            conf += 18        # 短期企稳
        elif chg_20d < -15:
            conf += 12        # 超跌反弹空间
    elif "S8:" in scene:
        if chg_5d > 0 and chg_10d > 0:
            conf += 25        # 探底成功
        elif chg_5d > 0:
            conf += 15        # 反弹试探
        elif chg_5d < -3:
            conf -= 10        # 继续探底
    # S7/S9 (冰封/出逃): 极端弱势
    elif "S7:" in scene:
        if chg_20d < -20:
            conf += 15        # 超跌极端
        elif chg_5d > 0:
            conf += 10        # 死猫跳
    elif "S9:" in scene:
        if chg_5d < 0 and chg_10d < 0 and chg_20d < 0:
            conf += 30        # 确认恐慌
        elif chg_5d < 0:
            conf += 18
        elif chg_3d > 0:
            conf -= 12        # 反弹可能是陷阱

    return max(0, min(100, int(conf)))


def calc_congestion_confidence(row):
    """
    拥挤度维度置信度 (0-100) - 三分法9场景版

    评估项:
      - 场景一致性 (颜色稳定性): 0~35分
      - 拥挤度趋势方向与场景是否匹配: 0~35分
      - 拥挤度绝对水平合理性: 0~30分
    """
    conf = 30  # 基础分
    scene = row.get("scene", "")
    sc = row.get("scene_consistency", 0.5)
    ct = row.get("cong_trend", 0)
    avg_cong = row.get("avg_cong", 0)

    # ---- 1. 场景一致性 (0~35) ----
    conf += sc * 35

    # ---- 2. 拥挤度趋势方向匹配 (0~35 / -10~-20) ----
    # S1/S4/S7 (低拥挤区 c<50): 不升温是好的
    if "S1:" in scene or "S4:" in scene or "S7:" in scene:
        if ct <= -2:
            conf += 35
        elif ct <= 0:
            conf += 22
        elif ct >= 4:
            conf -= 20        # 低拥挤急升=异常
        elif ct >= 2:
            conf -= 12
        else:
            conf -= 6
    # S2/S5 (中拥挤区 c:50~80): 温和可控
    elif "S2:" in scene or "S5:" in scene:
        if ct <= 0:
            conf += 30        # 未升温=健康
        elif ct <= 2:
            conf += 15
        elif ct >= 4:
            conf -= 20
        else:
            conf -= 8
    # S3/S6/S9 (高拥挤区 c>=80): 应降温
    elif "S3:" in scene or "S6:" in scene or "S9:" in scene:
        if ct <= -3:
            conf += 35        # 明显降温=风险释放
        elif ct <= -1:
            conf += 22
        elif ct >= 2:
            conf -= 15        # 还在升温=风险加剧
        else:
            conf -= 6

    # ---- 3. 拥挤度绝对水平合理性 (0~30) ----
    # S1/S4/S7 (预期低拥挤)
    if "S1:" in scene or "S4:" in scene or "S7:" in scene:
        if avg_cong < 30:
            conf += 30
        elif avg_cong < 45:
            conf += 20
        elif avg_cong < 55:
            conf += 8
        else:
            conf -= 10        # 应低却高=矛盾
    # S2/S5 (预期中拥挤)
    elif "S2:" in scene or "S5:" in scene:
        if 40 <= avg_cong <= 70:
            conf += 20        # 刚好在中间带
        elif avg_cong > 80:
            conf -= 8         # 偏高了
        elif avg_cong < 30:
            conf -= 5         # 偏低了
    # S3/S6/S9 (预期高拥挤)
    elif "S3:" in scene or "S6:" in scene or "S9:" in scene:
        if avg_cong > 85:
            conf -= 10        # 极端拥挤
        elif avg_cong > 70:
            conf += 5
        elif avg_cong < 50:
            conf -= 8         # 标记高拥挤但实际偏低

    return max(0, min(100, int(conf)))


def calc_confidence(row):
    """
    综合置信度 (0-100)

    加权合并两个维度:
      - 宽度权重 60% (主趋势指标)
      - 拥挤度权重 40% (辅助/修正指标)

    当两维度方向一致时给予额外加分(共振效应);
    方向矛盾时给予惩罚(冲突降权).
    """
    wc = calc_width_confidence(row)
    cc = calc_congestion_confidence(row)
    scene = row.get("scene", "")

    # 基础加权: 60%宽度 + 40%拥挤度
    combined = wc * 0.6 + cc * 0.4

    # 共振/冲突修正 (±10)
    # 两边都高(>=70)或都低(<40)=共振; 一高一低=冲突
    if wc >= 70 and cc >= 70:
        combined += 10       # 双重确认，强信号
    elif wc < 40 and cc < 40:
        combined -= 5        # 双弱，整体不可靠
    elif abs(wc - cc) > 35:
        combined -= 8        # 维度间矛盾较大

    return max(0, min(100, int(round(combined))))


def signal_type(consistency, trend, scene=None, width_val=0):
    """
    三级信号分类，偏细时根据S1-S9场景给出方向性描述

    参数:
      consistency: 场景颜色一致性 (0~1)
      trend:       宽度日均变化量
      scene:       当前Sx场景名
      width_val:   当前市场宽度值

    返回: (类型标签, 图标标记)
    """
    # ---- 趋势: 稳定且动能明确 ----
    if consistency >= 0.6 and abs(trend) >= 2:
        return "趋势", "★"

    # ---- 偶发: 频繁跳变，不可靠 ----
    elif consistency < 0.4:
        return "偶发", "○"

    # ---- 偏稳: 有持续性但动能不足，按S1-S9细分 ----
    if not scene:
        return "偏稳", "☆"

    # S1/S2/S3 区间 (高宽度 - 做多/追多/减仓)
    if "S1:" in scene or "S2:" in scene or "S3:" in scene:
        if abs(trend) >= 3:
            return "偏稳(加速)", "☆"
        elif abs(trend) >= 1:
            return "偏稳(延续)", "☆"
        elif trend > 0:
            return "偏稳(缓升)", "☆"
        elif trend > -1:
            return "偏稳(横盘)", "☆"
        else:
            return "偏稳(回落)", "☆"

    # S4 区间 (中宽低拥 - 回暖蓄势)
    elif "S4:" in scene:
        if width_val >= 65:
            return "偏稳(接近强势)", "☆"
        elif trend > 0:
            return "偏稳(稳步回升)", "☆"
        elif trend > -1:
            return "偏稳(平台整理)", "☆"
        else:
            return "偏稳(回调确认)", "☆"

    # S5 区间 (中宽中拥 - 方向不明最纠结的位置)
    elif "S5:" in scene:
        if trend > 0:
            return "偏稳(偏多震荡)", "☆"
        elif trend < 0:
            return "偏稳(偏弱震荡)", "☆"
        else:
            return "偏稳(胶着)", "☆"

    # S6 区间 (中宽高拥 - 高位警惕)
    elif "S6:" in scene:
        if width_val >= 70:
            return "偏稳(高位风险)", "☆"
        elif trend < -1:
            return "偏稳(破位边缘)", "☆"
        else:
            return "偏稳(诱多嫌疑)", "☆"

    # S7 区间 (低宽低拥 - 冰封观望)
    elif "S7:" in scene:
        if width_val >= 40:
            return "偏稳(底部徘徊)", "☆"
        elif trend > -3:
            return "偏稳(阴跌放缓)", "☆"
        else:
            return "偏稳(自由落体)", "☆"

    # S8 区间 (低宽中拥 - 探底企稳)
    elif "S8:" in scene:
        if trend > 1:
            return "偏稳(反弹试探)", "☆"
        elif trend > -1:
            return "偏稳(磨底)", "☆"
        else:
            return "偏稳(再探前低)", "☆"

    # S9 区间 (低宽高拥 - 恐慌出逃)
    elif "S9:" in scene:
        if trend < -3:
            return "偏稳(崩盘)", "☆"
        elif trend < 0:
            return "偏稳(恐慌蔓延)", "☆"
        else:
            return "偏稳(超卖反弹)", "☆"

    return "偏稳", "☆"








def price_trend_label(chg_3d, chg_5d, chg_10d, chg_20d):
    """
    价格趋势标签 (综合 4 个时间维度)

    分层判断:
      第一层: 短期动能 (3日 + 5日) → 方向
      第二层: 中期趋势 (10日 + 20日) → 确认/背离
      组合出 9 级标签

    返回: 趋势描述字符串
    """
    # ---- 短期方向 (3+5日) ----
    if chg_5d > 2 and chg_3d > 1:
        short = "强攻"      # 短期强劲上攻
    elif chg_5d > 0 and chg_3d > 0:
        short = "上行"      # 短期一致向上
    elif chg_5d > 0 and chg_3d <= 0:
        short = "反弹"      # 5日正但3日转弱，反弹乏力或回调中
    elif chg_5d > -2 and chg_5d <= 0:
        short = "整理"      # 小幅波动
    else:
        short = "下挫"      # 短期明显下跌

    # ---- 中期方向 (10+20日) ----
    if chg_10d > 0 and chg_20d > 0:
        mid = "多"          # 中期多头排列
    elif chg_10d > 0 and chg_20d <= 0:
        mid = "转暖"        # 中期刚转正，底部回升中
    elif chg_10d <= 0 and chg_20d > 0:
        mid = "回落"        # 中期从高位回落
    else:
        mid = "空"          # 中期空头排列

    # ---- 组合标签 ----
    combo_map = {
        ("强攻", "多"):   "↑↑↑ 强势突破",
        ("强攻", "转暖"): "↑↑ 强势反转",
        ("强攻", "回落"): "↑↑ 反弹受阻",
        ("强攻", "空"):   "↑ 超跌反弹",

        ("上行", "多"):   "↑↑ 稳步上行",
        ("上行", "转暖"): "↑ 偏强回暖",
        ("上行", "回落"): "→ 高位震荡",
        ("上行", "空"):   "↑ 弱势反抽",

        ("反弹", "多"):   "↑ 震荡偏强",
        ("反弹", "转暖"): "→ 底部企稳",
        ("反弹", "回落"): "↓ 弱势震荡",
        ("反弹", "空"):   "↓ 阴跌不止",

        ("整理", "多"):   "→ 横盘蓄势",
        ("整理", "转暖"): "→ 磨底待变",
        ("整理", "回落"): "↓ 高位滞涨",
        ("整理", "空"):   "↓ 阴跌整理",

        ("下挫", "多"):   "↓ 急跌反弹",
        ("下挫", "转暖"): "↓ 探底过程中",
        ("下挫", "回落"): "↓↓ 加速下跌",
        ("下挫", "空"):   "↓↓↓ 深度弱势",
    }

    return combo_map.get((short, mid), "→ 震荡")


# ============================================================
# 4. 申万一级分类
# ============================================================
SW1_MAP = {
    "农林牧渔": ["农业", "林业", "渔业", "畜牧", "饲料", "动物保健", "农产品加工", "种植业", "农业综合"],
    "采掘": ["煤炭", "石油", "采矿", "油气"],
    "化工": ["化学", "塑料", "橡胶", "农化", "纤维"],
    "钢铁": ["普钢", "特钢", "冶钢"],
    "有色金属": ["工业金属", "贵金属", "小金属", "金属新材料"],
    "电子": ["半导体", "元件", "光学", "消费电子", "电子化学品", "电子"],
    "汽车": ["乘用车", "商用车", "汽车", "摩托车"],
    "家用电器": ["家电", "白色家电", "黑色家电", "小家电", "厨卫", "照明", "零部件"],
    "食品饮料": ["白酒", "饮料", "乳品", "调味", "休闲食品", "食品加工", "食品"],
    "纺织服装": ["服装", "家纺", "纺织", "饰品"],
    "轻工制造": ["造纸", "包装", "文娱", "家居"],
    "医药生物": ["化学制药", "中药", "生物制品", "医药", "医疗", "器械"],
    "公用事业": ["电力", "燃气", "水务", "环保"],
    "交通运输": ["航空", "机场", "航运", "港口", "物流", "铁路", "公路"],
    "房地产": ["房地产", "物业"],
    "商业贸易": ["零售", "连锁", "百货", "贸易", "商业"],
    "休闲服务": ["酒店", "餐饮", "旅游", "景区", "体育", "娱乐", "休闲"],
    "银行": ["银行"],
    "非银金融": ["证券", "保险", "多元金融"],
    "建筑材料": ["水泥", "玻璃", "建材", "非金属"],
    "建筑装饰": ["房屋建设", "装修", "园林", "基础建设", "建筑装饰"],
    "电气设备": ["电气", "电机", "电源", "自动化设备", "自动化"],
    "国防军工": ["航空装备", "航天", "军工", "船舶", "国防"],
    "计算机": ["计算机", "软件", "IT服务", "互联网"],
    "传媒": ["出版", "影视", "数字媒体", "广告", "电视广播", "传媒"],
    "通信": ["通信"],
    "机械设备": ["通用设备", "专用设备", "工程机械", "仪器仪表", "机械"],
}


def get_sw1(name):
    for sw1, keywords in SW1_MAP.items():
        for kw in keywords:
            if kw in name or name.startswith(kw):
                return sw1
    return "其他"


# ============================================================
# 5. 数据整理（带历史和行情趋势）
# ============================================================
print("分析历史趋势...")
industries = []

all_codes = set(w["maMarketWidth"].keys()) | set(c["congestions"].keys())

for code in all_codes:
    name = code_to_name.get(code, code)
    width_data = w["maMarketWidth"].get(code, [])
    cong_data = c["congestions"].get(code, [])

    if not width_data or not cong_data:
        continue

    # 用对齐后的offset取值, 确保三个数据源时间基准一致
    v_w = width_data[base_offset_w] if base_offset_w < len(width_data) else width_data[-1]
    latest_width = v_w.get("value20", 0) if isinstance(v_w, dict) else 0

    v_c = cong_data[base_offset_c] if base_offset_c < len(cong_data) else cong_data[-1]
    if isinstance(v_c, dict):
        turnover = v_c.get("turnoverRateFQuantile", 0) or 0
        amount = v_c.get("amountCongestionQuantile", 0) or 0
    else:
        turnover, amount = 0, 0

    # 场景判断只用换手率分位数 (turnoverRateFQuantile)
    # 成交额拥挤度 (amountCongestionQuantile) 作为辅助参考
    cong_for_scene = turnover
    avg_cong = (turnover + amount) / 2 if turnover else 0  # 综合值仅用于展示

    # 历史宽度/拥挤度
    hist_width = get_history_width(code, HISTORY_DAYS)
    hist_cong = get_history_congestion(code, HISTORY_DAYS)
    width_trend = calc_trend(hist_width)
    cong_trend = calc_trend(hist_cong)

    # 场景历史
    scene_history = []
    for i in range(min(len(hist_width), len(hist_cong))):
        _, color, _ = get_scene(hist_width[i], hist_cong[i])
        scene_history.append(color)
    scene_consistency = calc_scene_consistency(scene_history)

    # 当前场景: 拥挤度只用 turnoverRateFQuantile 判断
    scene, color, action = get_scene(latest_width, cong_for_scene)
    sig_type, sig_mark = signal_type(scene_consistency, width_trend,
                                     scene=scene, width_val=latest_width)

    # 行情趋势数据（从CSV匹配）
    quotes = name_to_quotes.get(name, {})
    chg_3d = quotes.get("chg_3d", 0)
    chg_5d = quotes.get("chg_5d", 0)
    chg_10d = quotes.get("chg_10d", 0)
    chg_20d = quotes.get("chg_20d", 0)

    row = {
        "code": code,
        "name": name,
        "sw1": get_sw1(name),
        "width": latest_width,
        "turnover": turnover,
        "amount": amount,
        "avg_cong": avg_cong,
        "scene": scene,
        "color": color,
        "action": action,
        "width_trend": width_trend,
        "cong_trend": cong_trend,
        "scene_consistency": scene_consistency,
        "signal_type": sig_type,
        "signal_mark": sig_mark,
        "chg_3d": chg_3d,
        "chg_5d": chg_5d,
        "chg_10d": chg_10d,
        "chg_20d": chg_20d,
        "price_trend": price_trend_label(chg_3d, chg_5d, chg_10d, chg_20d),
    }
    row["width_conf"] = calc_width_confidence(row)
    row["cong_conf"] = calc_congestion_confidence(row)
    row["confidence"] = calc_confidence(row)
    industries.append(row)

# 行情数据匹配统计
quotes_matched = sum(1 for i in industries if i.get("chg_3d", 0) != 0 or i.get("chg_5d", 0) != 0)
print("\n  行情匹配: {}/{} 个行业有涨跌幅数据".format(quotes_matched, len(industries)))
if quotes_matched == 0 and industries:
    print("  [WARNING] 所有行业行情数据为空! 检查 quotes/ CSV 列名是否正确")

# ============================================================
# 6. 按S1-S9场景分类
# ============================================================
SCENE_KEYS = ["S1:", "S2:", "S3:", "S4:", "S5:", "S6:", "S7:", "S8:", "S9:"]
scene_groups = {}
for sk in SCENE_KEYS:
    scene_groups[sk] = [i for i in industries if sk in i["scene"]]
scene_missing = [i for i in industries if "数据缺失" in i.get("scene", "")]

for s in scene_groups.values():
    s.sort(key=lambda x: (x["confidence"], x["width"]), reverse=True)

trend_groups = {}
stable_groups = {}
for sk in SCENE_KEYS:
    trend_groups[sk] = [i for i in scene_groups[sk] if i["signal_type"] == "趋势"]
    stable_groups[sk] = [i for i in scene_groups[sk] if "偏稳" in i["signal_type"]]

# ============================================================
# 7. 一级行业场景分布
# ============================================================
sw1_scene_count = defaultdict(lambda: {sk: 0 for sk in SCENE_KEYS})
sw1_trend_count = defaultdict(lambda: {"trend": 0, "sporadic": 0})

for ind in industries:
    sw1 = ind["sw1"]
    matched = False
    for sk in SCENE_KEYS:
        if sk in ind["scene"]:
            sw1_scene_count[sw1][sk] += 1
            matched = True
            break
    if not matched:
        sw1_scene_count[sw1]["数据缺失"] = sw1_scene_count[sw1].get("数据缺失", 0) + 1

    if ind["signal_type"] == "趋势":
        sw1_trend_count[sw1]["trend"] += 1
    else:
        sw1_trend_count[sw1]["sporadic"] += 1

# ============================================================
# 8. 生成报告 (Markdown 格式)
# ============================================================
md = []  # markdown lines

# ---- 标题 ----
md.append("# 申万二级行业可操作分析报告 (增强版 v2)")
md.append("")
md.append("> 基于: 行业拥挤度 + 市场宽度 + 行情趋势 叠加分析  ")
md.append("> 参考历史{}日 + 3/5/10/20日涨跌幅趋势".format(HISTORY_DAYS))
md.append("")
md.append("报告日期: {} | 数据截止: {} | 参考历史: {}天".format(
    datetime.now().strftime("%Y-%m-%d"), latest_date, HISTORY_DAYS))
md.append("")
md.append("---")
md.append("")

# ---- 一、市场总体状态 ----
md.append("## 一、市场总体状态")
md.append("")
valid = industries
total = len(valid)

avg_w = sum(i["width"] for i in valid) / total if total else 0
avg_c_list = [i["avg_cong"] for i in valid if i["avg_cong"] > 0]
avg_c = sum(avg_c_list) / len(avg_c_list) if avg_c_list else 0
avg_w_trend = sum(i["width_trend"] for i in valid) / total if total else 0
avg_c_trend = sum(i["cong_trend"] for i in valid) / total if total else 0

market_status = "极弱" if avg_w < 30 else "弱势" if avg_w < 50 else "中性" if avg_w < 70 else "强势"
trend_status = "上升" if avg_w_trend > 2 else "下降" if avg_w_trend < -2 else "震荡"
cong_status = "高热" if avg_c >= C_HIGH else "适中" if avg_c >= C_MID else "清淡"
cong_trend_status = "升温" if avg_c_trend > 1 else "降温" if avg_c_trend < -1 else "平稳"

md.append("### 市场宽度")
md.append("")
md.append("- 均值: {:.1f} | 状态: **{}** | 趋势: {} ({:+.1f}/天)".format(avg_w, market_status, trend_status, avg_w_trend))
md.append("- 高(>=80): {} 个 | 中(50~80): {} 个 | 低(<50): {} 个".format(
    len([i for i in valid if i["width"] >= W_HIGH]),
    len([i for i in valid if W_MID <= i["width"] < W_HIGH]),
    len([i for i in valid if i["width"] < W_MID])))
md.append("")

md.append("### 市场拥挤度")
md.append("")
md.append("- 拥挤度均值: {:.1f} | 状态: **{}** | 趋势: {} ({:+.2f}/天)".format(avg_c, cong_status, cong_trend_status, avg_c_trend))
md.append("- 高(>=80): {} 个 | 中(50~80): {} 个 | 低(<50): {} 个".format(
    len([i for i in valid if i["avg_cong"] >= C_HIGH]),
    len([i for i in valid if C_MID <= i["avg_cong"] < C_HIGH]),
    len([i for i in valid if i["avg_cong"] < C_MID])))
md.append("")

md.append("### 行情趋势 (来自行情CSV)")
md.append("")
avg_chg5 = sum(i["chg_5d"] for i in valid) / total if total else 0
avg_chg10 = sum(i["chg_10d"] for i in valid) / total if total else 0
avg_chg20 = sum(i["chg_20d"] for i in valid) / total if total else 0
md.append("- 5日平均: {:+.2f}% | 10日平均: {:+.2f}% | 20日平均: {:+.2f}%".format(avg_chg5, avg_chg10, avg_chg20))
price_up5 = len([i for i in valid if i["chg_5d"] > 0])
price_up10 = len([i for i in valid if i["chg_10d"] > 0])
md.append("- 5日上涨: {} 个 | 10日上涨: {} 个".format(price_up5, price_up10))
md.append("")

md.append("### 信号类型统计")
md.append("")
trend_cnt = len([i for i in valid if i["signal_type"] == "趋势"])
stable_cnt = len([i for i in valid if "偏稳" in i.get("signal_type", "")])
sporadic_cnt = len([i for i in valid if i["signal_type"] == "偶发"])
md.append("- ★趋势: {} 个 | ☆偏稳: {} 个 | ○偶发: {} 个 (合计: {} 个)".format(
    trend_cnt, stable_cnt, sporadic_cnt, trend_cnt + stable_cnt + sporadic_cnt))

# 偏稳子类型细分
if stable_cnt > 0:
    stable_types = {}
    for i in valid:
        st = i.get("signal_type", "")
        if "偏稳" in st:
            stable_types[st] = stable_types.get(st, 0) + 1
    if stable_types:
        sorted_stable = sorted(stable_types.items(), key=lambda x: -x[1])
        detail = "  ".join(["{}:{}".format(k, v) for k, v in sorted_stable])
        md.append("- ☆偏稳细分: {}".format(detail))
md.append("")

md.append("### 当前市场判断")
md.append("")
md.append("- {} + {} = **{}**".format(
    "宽度极弱" if avg_w < 20 else "宽度弱势" if avg_w < 40 else "宽度中性" if avg_w < 60 else "宽度强势",
    "高拥挤" if avg_c >= 60 else "低拥挤",
    "弱势磨底" if avg_w < 40 and avg_c < 60 else
    "高位见顶风险" if avg_w < 40 and avg_c >= 60 else
    "主升行情" if avg_w >= 40 and avg_c >= 60 else
    "启动初期"))

md.append("")
md.append("---")
md.append("")
md.append("## 二、九场景矩阵 (共 {} 个有效行业)".format(len(valid)))
md.append("")

md.append("| 场景 | 总数 | ★趋势 | ☆偏稳 | 置信度 | ☆偏稳细分 |")
md.append("|------|------|-------|-------|--------|-----------|")

# S1-S9 按行输出
for sk in SCENE_KEYS:
    slist = scene_groups.get(sk, [])
    tlist = trend_groups.get(sk, [])
    stable_list = stable_groups.get(sk, [])
    avg_conf = sum(i["confidence"] for i in slist) / len(slist) if slist else 0

    # 偏稳子类型统计
    stable_detail = ""
    if stable_list:
        sub_counts = {}
        for i in stable_list:
            st = i.get("signal_type", "")
            sub_counts[st] = sub_counts.get(st, 0) + 1
        sorted_sub = sorted(sub_counts.items(), key=lambda x: -x[1])
        detail_parts = ["{}{}".format(k, v) for k, v in sorted_sub]
        stable_detail = " ".join(detail_parts)

    scene_name = slist[0]["scene"] if slist else sk.replace(":", "")
    md.append("| {} | {} | {} | {} | {:.0f} | {} |".format(
        scene_name, len(slist), len(tlist), len(stable_list), avg_conf, stable_detail))

# 数据缺失行
if scene_missing:
    md.append("| ⚠ 数据缺失 | {} | - | - | - | - |".format(len(scene_missing)))
    missing_w0 = sum(1 for i in scene_missing if i.get("width", 0) == 0)
    missing_c_none = sum(1 for i in scene_missing if i.get("avg_cong") is None or i.get("avg_cong") == 0)
    reasons = []
    if missing_w0 > 0:
        reasons.append("宽度=0:{}".format(missing_w0))
    if missing_c_none > 0:
        reasons.append("拥挤度空:{}".format(missing_c_none))
    if reasons:
        md.append("| | | | | | {} |".format(" | ".join(reasons)))

# 合计行
scene_total = sum(len(scene_groups.get(sk, [])) for sk in SCENE_KEYS) + len(scene_missing)
md.append("| **合计** | **{}** | | | | |".format(scene_total))
if scene_total != total:
    md.append("")
    md.append("> ⚠ 校验: 场景{} + 缺失{} = {} vs 总数{}".format(
        scene_total - len(scene_missing), len(scene_missing), scene_total, total))

md.append("")
md.append("### 9场景矩阵速查")
md.append("")
md.append("```")
md.append("          拥挤<50        拥挤50~80      拥挤>=80")
md.append("        ┌─────────┬──────────┬─────────┐")
md.append(" 宽度>=80│ S1 强势   │ S2 加速   │ S3 极端  │  积极→追多→逢高减仓")
md.append(" 宽度50~80│ S4 回暖   │ S5 盘整   │ S6 警惕  │  吸纳→等待→逐步撤离")
md.append(" 宽度<50 │ S7 冰封   │ S8 探底   │ S9 出逃  │  观望→跟踪→坚决规避")
md.append("```")
md.append("")
md.append("### 信号含义")
md.append("")
md.append("- **★趋势**: 场景稳定+动能明确，信号可靠")
md.append("- **☆偏稳**: 有持续性但动能不足 (按场景细分)")
md.append("- **○偶发**: 今日刚进入该场景，需观察确认")
md.append("")


def print_scene_table(title, scene_list, trend_list, stable_list=None, max_rows=10, action_desc=""):
    """打印场景详情 Markdown 表格（含行情趋势列）"""
    if stable_list is None:
        stable_list = []
    sporadic_count = len(scene_list) - len(trend_list) - len(stable_list)

    md.append("")
    md.append("---")
    md.append("")
    md.append("## {} ({})".format(title, len(scene_list)))
    md.append("")
    md.append("- ★趋势: {} 个 | ☆偏稳: {} 个 | ○偶发: {} 个".format(
        len(trend_list), len(stable_list), sporadic_count))
    if stable_list:
        sub_counts = {}
        for i in stable_list:
            st = i.get("signal_type", "")
            sub_counts[st] = sub_counts.get(st, 0) + 1
        sorted_sub = sorted(sub_counts.items(), key=lambda x: -x[1])
        detail_parts = ["{}{}".format(k, v) for k, v in sorted_sub]
        md.append("- ☆偏稳细分: {}".format("  ".join(detail_parts)))
    md.append("- 操作: **{}**".format(action_desc))
    md.append("")
    if not scene_list:
        md.append("*(当前无)*")
        md.append("")
        return

    md.append("| 信号 | 一级行业 | 二级行业 | 宽度 | 拥挤 | 宽信 | 拥信 | 3日% | 5日% | 10日% | 20日% | 价格趋势 |")
    md.append("|------|----------|----------|------|------|------|------|------|------|-------|-------|----------|")
    for i in scene_list[:max_rows]:
        sig_display = "{}{}".format(i["signal_mark"], i.get("signal_type", ""))
        md.append("| {} | {} | {} | {:.0f} | {:.0f} | {:.0f} | {:.0f} | {:+.1f} | {:+.1f} | {:+.1f} | {:+.1f} | {} |".format(
            sig_display, i["sw1"][:11], i["name"][:8],
            i["width"], i["avg_cong"],
            i.get("width_conf", 0), i.get("cong_conf", 0),
            i["chg_3d"], i["chg_5d"], i["chg_10d"], i["chg_20d"],
            i["price_trend"]))
    md.append("")


SCENE_TITLES = {
    "S1:": ("三", "S1: 强势启动 (高宽低拥) - 积极做多"),
    "S2:": ("四", "S2: 加速上行 (高宽中拥) - 追多控仓"),
    "S3:": ("五", "S3: 极端过热 (高宽高拥) - 逢高减仓"),
    "S4:": ("六", "S4: 回暖蓄势 (中宽低拥) - 逢低吸纳"),
    "S5:": ("七", "S5: 盘整选择 (中宽中拥) - 耐心等待"),
    "S6:": ("八", "S6: 高位滞涨 (中宽高拥) - 逐步撤离"),
    "S7:": ("九", "S7: 冰封观望 (低宽低拥) - 远离观望"),
    "S8:": ("十", "S8: 探底企稳 (低宽中拥) - 密切跟踪"),
    "S9:": ("十一", "S9: 恐慌出逃 (低宽高拥) - 坚决规避"),
}
SCENE_ACTION_DESC = {
    "S1:": "积极做多 -- 趋势刚启动,顺势而为",
    "S2:": "追多控仓 -- 趋势延续中,注意过热风险",
    "S3:": "逢高减仓 -- 极端过热,逐步撤离",
    "S4:": "逢低吸纳 -- 回暖蓄势,逢低布局",
    "S5:": "耐心等待 -- 方向不明,静观其变",
    "S6:": "逐步撤离 -- 高位滞涨,控制风险",
    "S7:": "远离观望 -- 极端弱势,远离市场",
    "S8:": "密切跟踪 -- 探底企稳,密切关注",
    "S9:": "坚决规避 -- 恐慌出逃,不可触碰",
}

for sk in SCENE_KEYS:
    num, title = SCENE_TITLES.get(sk, ("?", sk))
    print_scene_table("{}、{}".format(num, title), scene_groups.get(sk, []),
                      trend_groups.get(sk, []),
                      stable_list=stable_groups.get(sk, []),
                      max_rows=len(scene_groups.get(sk, [])),
                      action_desc=SCENE_ACTION_DESC.get(sk, ""))

md.append("")
md.append("---")
md.append("")
md.append("## 七、一级行业场景分布矩阵 (含趋势统计)")
md.append("")

md.append("| 一级行业 | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 | S9 | 总数 | 主导 | 趋势/偶发 |")
md.append("|----------|----|----|----|----|----|----|----|----|----|------|------|-----------|")
for sw1 in sorted(sw1_scene_count.keys(), key=lambda x: -sum(sw1_scene_count[x].values())):
    cnt = sw1_scene_count[sw1]
    total_sw1 = sum(cnt.values())
    # 一级行业: 显示主导Sx场景和各Sx计数（固定S1-S9顺序）
    dominant = max(cnt.items(), key=lambda x: x[1])
    dom_key = dominant[0] if dominant else "?"
    dom_label = dom_key.replace(":", "") if dom_key else "-"
    t = sw1_trend_count[sw1]
    # 固定 S1-S9 顺序输出各场景计数
    s_vals = [cnt.get(sk, 0) for sk in SCENE_KEYS]
    cols = "|".join([" {:>2}".format(v) for v in s_vals])
    md.append("| {} |{} | {:>4} | {:>4} | {}/{} |".format(
        sw1[:12], cols, total_sw1, dom_label, t["trend"], t["sporadic"]))

# ---- 八、综合投资建议 ----
md.append("")
md.append("---")
md.append("")
md.append("## 八、综合投资建议 (趋势信号优先 + 价格趋势验证)")
md.append("")

# 做多方向: S1 + S4 的趋势信号
buy_trends = trend_groups.get("S1:", []) + trend_groups.get("S4:", [])
if buy_trends:
    md.append("### 做多方向 - 趋势信号 (★★★ 可靠)")
    md.append("")
    for i in buy_trends[:5]:
        md.append("- **[{}]** {}({}) 宽度{:.0f} 拥挤{:.0f} 5日{:+.1f}% 10日{:+.1f}%".format(
            i["scene"].split(":")[0], i["name"], i["sw1"],
            i["width"], i["avg_cong"], i["chg_5d"], i["chg_10d"]))
    md.append("")
    # S1/S4 偏稳
    buy_stable = stable_groups.get("S1:", []) + stable_groups.get("S4:", [])
    if buy_stable:
        md.append("### 做多/回暖 - 偏稳信号 (观察确认)")
        md.append("")
        for i in buy_stable[:3]:
            md.append("- **[{}]** {}({}) 宽度{:.0f} 拥挤{:.0f} -- 观察确认".format(
                i["scene"].split(":")[0], i["name"], i["sw1"], i["width"], i["avg_cong"]))
        md.append("")

# 持有/追多: S2 的趋势信号
hold_trends = trend_groups.get("S2:", [])
if hold_trends:
    md.append("### 追多/持有 - 趋势信号 (★★ 控仓)")
    md.append("")
    for i in hold_trends[:3]:
        alert = " ⚠价格5日转负" if i["chg_5d"] < 0 else ""
        md.append("- **[{}]** {}({}) 宽度{:.0f} 拥挤{:.0f} 5日{:+.1f}%{}".format(
            i["scene"].split(":")[0], i["name"], i["sw1"],
            i["width"], i["avg_cong"], i["chg_5d"], alert))
    md.append("")

# 减仓/规避: S3/S6/S9 的趋势信号
risk_trends = trend_groups.get("S3:", []) + trend_groups.get("S6:", []) + trend_groups.get("S9:", [])
if risk_trends:
    md.append("### 减仓/规避 - 趋势信号 (★★★ 紧急)")
    md.append("")
    for i in risk_trends[:8]:
        confirm = "[全周期跌]" if (i["chg_5d"] < 0 and i["chg_10d"] < 0 and i["chg_20d"] < 0) else ""
        md.append("- **[{}]** {}({}) 宽度{:.0f} 拥挤{:.0f} 5日{:+.1f}% {}".format(
            i["scene"].split(":")[0], i["name"], i["sw1"],
            i["width"], i["avg_cong"], i["chg_5d"], confirm))
    md.append("")

# 观望: S7/S8 的关注项
watch_items = trend_groups.get("S8:", [])[:3] if not trend_groups.get("S7:", []) else []
if watch_items or trend_groups.get("S7:", []):
    watch_items = trend_groups.get("S7:", [])[:2] + trend_groups.get("S8:", [])[:3]
    if watch_items:
        md.append("### 观望区关注")
        md.append("")
        for i in watch_items:
            md.append("- **[{}]** {}({}) 宽度{:.0f} 拥挤{:.0f} 5日{:+.1f}% -- 密切跟踪".format(
                i["scene"].split(":")[0], i["name"], i["sw1"],
                i["width"], i["avg_cong"], i["chg_5d"]))
        md.append("")

# ---- 九、总结 ----
md.append("---")
md.append("")
md.append("## 九、9场景矩阵速查")
md.append("")
md.append("```")
md.append("          拥挤<50       拥挤50~80     拥挤>=80")
md.append("        ┌─────────┬──────────┬─────────┐")
md.append(" 宽度>=80│ S1 积极    │ S2 追多   │ S3 减仓  │")
md.append(" 宽度50~80│ S4 吸纳    │ S5 等待   │ S6 撤离  │")
md.append(" 宽度<50 │ S7 观望    │ S8 跟踪   │ S9 规避  │")
md.append("```")
md.append("")
md.append("- ★趋势: 场景稳定+动能明确 | ☆偏稳: 有持续性但动能不足 | ○偶发: 刚进入需观察")
md.append("")
md.append("- 当前市场状态: **{}**".format(
    "强势(宽>50)" if avg_w >= 50 else "中性(宽30~50)" if avg_w >= 30 else "弱势(宽<30)"))
md.append("- 拥挤度状态: **{}**".format(
    "高热(拥>80)" if avg_c >= C_HIGH else "适中(拥50~80)" if avg_c >= C_MID else "清淡(拥<50)"))
md.append("")
md.append("---")
md.append("")
md.append("*报告生成完毕*")

report_text = "\n".join(md)

# 保存 -> report/ 文件夹，日期命名 (Markdown)
REPORT_DIR = os.path.join(BASE_DIR, "report")
os.makedirs(REPORT_DIR, exist_ok=True)
today_str = datetime.now().strftime("%Y-%m-%d")
output_path = os.path.join(REPORT_DIR, "action_report_{}.md".format(today_str))
with open(output_path, "w", encoding="utf-8") as f:
    f.write(report_text)

print("\n[已保存] {}".format(output_path))
print("  共分析 {} 个行业, 4个场景分布".format(len(industries)))

