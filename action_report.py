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
    if code not in c["congestions"]:
        return []
    values = c["congestions"][code]
    history = []
    for i in range(min(days, len(values))):
        v = values[i]
        if isinstance(v, dict):
            t = v.get("turnoverRateFQuantile", 0) or 0
            a = v.get("amountCongestionQuantile", 0) or 0
            history.append((t + a) / 2)
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

def classify_width(w_val):
    return ("高", 1) if w_val >= WIDTH_THRESHOLD else ("低", 0)


def classify_congestion(c_val):
    return ("高", 1) if c_val >= CONGESTION_THRESHOLD else ("低", 0)


def get_scene(w, c_val):
    if w == 0 or c_val is None:
        return "数据缺失", "gray", ""
    _, w_num = classify_width(w)
    _, c_num = classify_congestion(c_val)
    if w_num == 0 and c_num == 0:
        return "场景4:弱势磨底", "blue", "观望"
    elif w_num == 1 and c_num == 0:
        return "场景1:最佳做多", "green", "做多/加仓"
    elif w_num == 1 and c_num == 1:
        return "场景2:持有减仓", "yellow", "持有/控仓"
    else:
        return "场景3:高危见顶", "red", "卖出/避险"


def calc_confidence(row):
    """
    计算信号置信度 (0-100)
    综合考虑: 场景一致性 + 宽度趋势 + 价格趋势(3/5/10/20日)
    """
    conf = 50
    scene = row.get("scene", "")

    # 1. 场景一致性 (0-25)
    conf += row.get("scene_consistency", 0.5) * 25

    # 2. 宽度趋势 (0-15)
    width_trend = row.get("width_trend", 0)
    cong_trend = row.get("cong_trend", 0)

    if "场景1" in scene and width_trend > 0:
        conf += 15
    elif "场景2" in scene and width_trend > 0:
        conf += 10
    elif "场景3" in scene and width_trend < 0:
        conf += 15
    elif "场景4" in scene and width_trend < 0:
        conf += 10
    elif width_trend < -5:
        conf -= 10

    # 3. 价格趋势加分 (0-15) —— 新增：从行情CSV判断
    chg_3d = row.get("chg_3d", 0)
    chg_5d = row.get("chg_5d", 0)
    chg_10d = row.get("chg_10d", 0)
    chg_20d = row.get("chg_20d", 0)

    # 短中长期趋势一致性判断
    if "场景1" in scene or "场景2" in scene:
        # 做多/持有场景：价格趋势向上加分
        if chg_3d > 0 and chg_5d > 0:
            conf += 10  # 短期趋势一致向上
        elif chg_5d > 0 and chg_10d > 0:
            conf += 8  # 中期趋势向上
        elif chg_10d > 0 and chg_20d > 0:
            conf += 5  # 长期趋势向上
        elif chg_3d < -5 and chg_5d < -3:
            conf -= 10  # 短期反转预警

    elif "场景3" in scene:
        # 高危场景：价格持续走弱=确认顶部
        if chg_5d < 0 and chg_10d < 0 and chg_20d < 0:
            conf += 15  # 全周期下跌，顶部确认
        elif chg_5d < 0 and chg_10d < 0:
            conf += 10
        elif chg_3d > 0:
            conf -= 8  # 短期反弹但大趋势向下，可能是诱多

    elif "场景4" in scene:
        # 磨底场景：价格企稳=底部信号
        if chg_5d > 0 and chg_10d < 0:
            conf += 8  # 短期企稳，可能反转
        elif chg_20d < -15:
            conf += 5  # 超跌可能靠近底部

    # 4. 拥挤度趋势 (0-5)
    if "场景2" in scene and cong_trend > 2:
        conf -= 5  # 拥挤度加速上升=过热风险
    elif "场景3" in scene and cong_trend < -2:
        conf += 5  # 拥挤度回落=风险释放

    return max(0, min(100, int(conf)))


def signal_type(consistency, trend):
    if consistency >= 0.6 and abs(trend) >= 2:
        return "趋势", "★"
    elif consistency >= 0.4:
        return "偏稳", "☆"
    else:
        return "偶发", "○"


def price_trend_label(chg_3d, chg_5d, chg_10d):
    """价格趋势标签"""
    if chg_5d > 2 and chg_10d > 0:
        return "↑↑ 强势"
    elif chg_5d > 0:
        return "↑ 偏强"
    elif chg_5d < -2 and chg_10d < 0:
        return "↓↓ 弱势"
    elif chg_5d < 0:
        return "↓ 偏弱"
    else:
        return "→ 震荡"


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

    avg_cong = (turnover + amount) / 2 if turnover else 0

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

    # 当前场景
    scene, color, action = get_scene(latest_width, avg_cong)
    sig_type, sig_mark = signal_type(scene_consistency, width_trend)

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
        "price_trend": price_trend_label(chg_3d, chg_5d, chg_10d),
    }
    row["confidence"] = calc_confidence(row)
    industries.append(row)

# 行情数据匹配统计
quotes_matched = sum(1 for i in industries if i.get("chg_3d", 0) != 0 or i.get("chg_5d", 0) != 0)
print("\n  行情匹配: {}/{} 个行业有涨跌幅数据".format(quotes_matched, len(industries)))
if quotes_matched == 0 and industries:
    print("  [WARNING] 所有行业行情数据为空! 检查 quotes/ CSV 列名是否正确")

# ============================================================
# 6. 按场景和置信度分类
# ============================================================
scene1 = [i for i in industries if "场景1" in i["scene"]]
scene2 = [i for i in industries if "场景2" in i["scene"]]
scene3 = [i for i in industries if "场景3" in i["scene"]]
scene4 = [i for i in industries if "场景4" in i["scene"]]

for s in [scene1, scene2, scene3, scene4]:
    s.sort(key=lambda x: (x["confidence"], x["width"]), reverse=True)

trend_scene1 = [i for i in scene1 if i["signal_type"] == "趋势"]
trend_scene2 = [i for i in scene2 if i["signal_type"] == "趋势"]
trend_scene3 = [i for i in scene3 if i["signal_type"] == "趋势"]
trend_scene4 = [i for i in scene4 if i["signal_type"] == "趋势"]

# ============================================================
# 7. 一级行业场景分布
# ============================================================
sw1_scene_count = defaultdict(lambda: {"scene1": 0, "scene2": 0, "scene3": 0, "scene4": 0})
sw1_trend_count = defaultdict(lambda: {"trend": 0, "sporadic": 0})

for ind in industries:
    sw1 = ind["sw1"]
    if "场景1" in ind["scene"]:
        sw1_scene_count[sw1]["scene1"] += 1
    elif "场景2" in ind["scene"]:
        sw1_scene_count[sw1]["scene2"] += 1
    elif "场景3" in ind["scene"]:
        sw1_scene_count[sw1]["scene3"] += 1
    else:
        sw1_scene_count[sw1]["scene4"] += 1

    if ind["signal_type"] == "趋势":
        sw1_trend_count[sw1]["trend"] += 1
    else:
        sw1_trend_count[sw1]["sporadic"] += 1

# ============================================================
# 8. 生成报告
# ============================================================
lines = []
lines.append("=" * 95)
lines.append("  申万二级行业可操作分析报告 (增强版 v2)")
lines.append("  基于: 行业拥挤度 + 市场宽度 + 行情趋势 叠加分析")
lines.append("  参考历史{}日 + 3/5/10/20日涨跌幅趋势".format(HISTORY_DAYS))
lines.append("=" * 95)
lines.append("")
lines.append("报告日期: {}    数据截止: {}    参考历史: {}天".format(
    datetime.now().strftime("%Y-%m-%d"), latest_date, HISTORY_DAYS))
lines.append("")

# ---- 一、市场总体状态 ----
lines.append("-" * 95)
lines.append("一、市场总体状态")
lines.append("-" * 95)
valid = industries
total = len(valid)

avg_w = sum(i["width"] for i in valid) / total if total else 0
avg_c_list = [i["avg_cong"] for i in valid if i["avg_cong"] > 0]
avg_c = sum(avg_c_list) / len(avg_c_list) if avg_c_list else 0
avg_w_trend = sum(i["width_trend"] for i in valid) / total if total else 0

market_status = "极弱" if avg_w < 20 else "弱势" if avg_w < 40 else "中性" if avg_w < 60 else "强势"
trend_status = "上升" if avg_w_trend > 2 else "下降" if avg_w_trend < -2 else "震荡"

lines.append("")
lines.append("  【市场宽度】")
lines.append("    均值: {:.1f}  状态: {}  趋势: {} ({:+.1f}/天)".format(avg_w, market_status, trend_status, avg_w_trend))
lines.append("    强势行业(>=60): {} 个    弱势行业(<60): {} 个".format(
    len([i for i in valid if i["width"] >= 60]),
    len([i for i in valid if 0 < i["width"] < 60])))
lines.append("")
lines.append("  【市场拥挤度】")
lines.append("    均值: {:.1f}".format(avg_c))
lines.append("    高拥挤(>=60): {} 个    低拥挤(<60): {} 个".format(
    len([i for i in valid if i["avg_cong"] >= 60]),
    len([i for i in valid if 0 < i["avg_cong"] < 60])))
lines.append("")
lines.append("  【行情趋势】(来自行情CSV)")
avg_chg5 = sum(i["chg_5d"] for i in valid) / total if total else 0
avg_chg10 = sum(i["chg_10d"] for i in valid) / total if total else 0
avg_chg20 = sum(i["chg_20d"] for i in valid) / total if total else 0
lines.append("    5日平均: {:+.2f}%    10日平均: {:+.2f}%    20日平均: {:+.2f}%".format(avg_chg5, avg_chg10, avg_chg20))
price_up5 = len([i for i in valid if i["chg_5d"] > 0])
price_up10 = len([i for i in valid if i["chg_10d"] > 0])
lines.append("    5日上涨: {} 个    10日上涨: {} 个".format(price_up5, price_up10))
lines.append("")
lines.append("  【信号类型统计】")
trend_cnt = len([i for i in valid if i["signal_type"] == "趋势"])
sporadic_cnt = len([i for i in valid if i["signal_type"] == "偶发"])
lines.append("    趋势信号: {} 个    偶发信号: {} 个".format(trend_cnt, sporadic_cnt))
lines.append("")
lines.append("  【当前市场判断】")
lines.append("    {} + {} = {}".format(
    "宽度极弱" if avg_w < 20 else "宽度弱势" if avg_w < 40 else "宽度中性" if avg_w < 60 else "宽度强势",
    "高拥挤" if avg_c >= 60 else "低拥挤",
    "弱势磨底" if avg_w < 40 and avg_c < 60 else
    "高位见顶风险" if avg_w < 40 and avg_c >= 60 else
    "主升行情" if avg_w >= 40 and avg_c >= 60 else
    "启动初期"))

# ---- 二、四大场景分布 ----
lines.append("")
lines.append("-" * 95)
lines.append("二、四大场景分布 (共 {} 个有效行业)".format(len(valid)))
lines.append("-" * 95)
lines.append("")
lines.append("  {:^20} {:>6} {:>6} {:>8}  {}".format("场景", "总数", "趋势", "置信度", "说明"))
lines.append("  " + "-" * 60)
for sname, slist, tlist in [
    ("场景1:最佳做多", scene1, trend_scene1),
    ("场景2:持有减仓", scene2, trend_scene2),
    ("场景3:高危见顶", scene3, trend_scene3),
    ("场景4:弱势磨底", scene4, trend_scene4),
]:
    avg_conf = sum(i["confidence"] for i in slist) / len(slist) if slist else 0
    lines.append("  {:^20} {:>6} {:>6} {:>8.0f}  {}".format(
        sname, len(slist), len(tlist), avg_conf, ""))
lines.append("")
lines.append("  [信号含义]")
lines.append("  ★趋势: 过去{}天持续在该场景，信号可靠".format(HISTORY_DAYS))
lines.append("  ○偶发: 今日刚进入该场景，需观察确认")
lines.append("  场景1: 资金刚进场,趋势启动,重仓持有")
lines.append("  场景2: 趋势延续但控仓,宽度收窄即离场")
lines.append("  场景3: 抱团瓦解预警,全面减仓!")
lines.append("  场景4: 磨底观望,不左侧抄底")


def print_scene_table(title, scene_list, trend_list, max_rows, action_desc):
    """打印场景详情表格（含行情趋势列）"""
    lines.append("")
    lines.append("=" * 95)
    lines.append("{} --> {} 个".format(title, len(scene_list)))
    lines.append("    趋势信号: {} 个 | 偶发信号: {} 个".format(len(trend_list), len(scene_list) - len(trend_list)))
    lines.append("=" * 95)
    if not scene_list:
        lines.append("  (当前无)")
        return
    lines.append("")
    lines.append("  操作: {}".format(action_desc))
    lines.append("")
    lines.append("  {:<4} {:<12} {:<9} {:>6} {:>6} {:>6} {:>7} {:>7} {:>7} {:>7} {}".format(
        "信号", "一级行业", "二级行业", "宽度", "拥挤", "置信", "3日%", "5日%", "10日%", "20日%", "价格趋势"))
    lines.append("  " + "-" * 93)
    for i in scene_list[:max_rows]:
        lines.append("  {}  {:<12} {:<9} {:>6.0f} {:>6.0f} {:>6.0f} {:>+7.1f} {:>+7.1f} {:>+7.1f} {:>+7.1f} {}".format(
            i["signal_mark"], i["sw1"][:12], i["name"][:9],
            i["width"], i["avg_cong"], i["confidence"],
            i["chg_3d"], i["chg_5d"], i["chg_10d"], i["chg_20d"],
            i["price_trend"]))


print_scene_table("三、场景1: 最佳做多区间 (低拥挤+高宽度)", scene1, trend_scene1, 10,
                  "做多/加仓 -- 趋势刚启动,顺势而为")
print_scene_table("四、场景2: 持有减仓区间 (高拥挤+高宽度)", scene2, trend_scene2, 10,
                  "持有/控仓 -- 趋势延续中但开始控风险,宽度收窄即离场")
print_scene_table("五、场景3: 高危见顶区间 (高拥挤+低宽度)", scene3, trend_scene3, 15,
                  "卖出/避险 -- 抱团瓦解预警! 龙头撑指数,多数个股跌")
print_scene_table("六、场景4: 弱势磨底区间 (低拥挤+低宽度)", scene4, trend_scene4, 10,
                  "观望/不抄底 -- 磨底期,等待宽度率先回升再布局")

# ---- 七、一级行业全景 ----
lines.append("")
lines.append("=" * 95)
lines.append("七、一级行业场景分布矩阵 (含趋势统计)")
lines.append("=" * 95)
lines.append("")
lines.append("  {:<12} {:>4} {:>4} {:>4} {:>4} {:>6}  {}  {}".format(
    "一级行业", "S1", "S2", "S3", "S4", "总数", "主导场景", "趋势/偶发"))
lines.append("  " + "-" * 70)
for sw1 in sorted(sw1_scene_count.keys(), key=lambda x: -sum(sw1_scene_count[x].values())):
    cnt = sw1_scene_count[sw1]
    total_sw1 = sum(cnt.values())
    dominant = max(cnt, key=cnt.get)
    dom_cn = {"scene1": "场景1", "scene2": "场景2", "scene3": "场景3", "scene4": "场景4"}[dominant] if cnt[dominant] > 0 else "-"
    t = sw1_trend_count[sw1]
    lines.append("  {:<12} {:>4} {:>4} {:>4} {:>4} {:>6}  {}  {}/{}".format(
        sw1[:12], cnt["scene1"], cnt["scene2"], cnt["scene3"], cnt["scene4"], total_sw1, dom_cn, t["trend"], t["sporadic"]))

# ---- 八、综合投资建议 ----
lines.append("")
lines.append("=" * 95)
lines.append("八、综合投资建议 (趋势信号优先 + 价格趋势验证)")
lines.append("=" * 95)
lines.append("")

if trend_scene1:
    lines.append("  【做多方向 - 趋势信号】(★★★ 可靠)")
    for i in trend_scene1[:3]:
        lines.append("    * {}({}) 宽度{:.0f},拥挤{:.0f},价格5日{:+5.1f}%,10日{:+5.1f}%".format(
            i["name"], i["sw1"], i["width"], i["avg_cong"], i["chg_5d"], i["chg_10d"]))
    lines.append("")

if scene1:
    other = [i for i in scene1 if i["signal_type"] != "趋势"]
    if other:
        lines.append("  【做多方向 - 偶发信号】(需观察,确认价格趋势) ")
        for i in other[:2]:
            lines.append("    * {}({}) 宽度{:.0f},拥挤{:.0f},价格5日{:+5.1f}% -- 观察确认".format(
                i["name"], i["sw1"], i["width"], i["avg_cong"], i["chg_5d"]))
        lines.append("")

if trend_scene2:
    lines.append("  【控仓持有 - 趋势信号】(★★ 谨慎,关注价格是否转弱)")
    for i in trend_scene2[:3]:
        alert = " -- 价格5日转负,警惕!" if i["chg_5d"] < 0 else ""
        lines.append("    * {}({}) 宽度{:.0f},拥挤{:.0f},价格5日{:+5.1f}%{}".format(
            i["name"], i["sw1"], i["width"], i["avg_cong"], i["chg_5d"], alert))
    lines.append("")

if trend_scene3:
    lines.append("  【规避减仓 - 趋势信号】(★★★ 紧急,价格趋势确认)")
    for i in trend_scene3[:5]:
        confirm = "[确认]全周期下跌" if (i["chg_5d"] < 0 and i["chg_10d"] < 0 and i["chg_20d"] < 0) else "需关注"
        lines.append("    * {}({}) 宽度{:.0f},拥挤{:.0f},价格5日{:+5.1f}%,10日{:+5.1f}% -- {}!".format(
            i["name"], i["sw1"], i["width"], i["avg_cong"], i["chg_5d"], i["chg_10d"], confirm))
    lines.append("")

if scene3:
    other = [i for i in scene3 if i["signal_type"] != "趋势"]
    if other:
        lines.append("  【规避减仓 - 偶发信号】(需观察)")
        for i in other[:3]:
            lines.append("    * {}({}) 宽度{:.0f},拥挤{:.0f},价格5日{:+5.1f}% -- 建议关注".format(
                i["name"], i["sw1"], i["width"], i["avg_cong"], i["chg_5d"]))
        lines.append("")

lines.append("  【观望】关注弱势磨底中宽度率先回升的板块")

# ---- 九、总结 ----
lines.append("")
lines.append("-" * 95)
lines.append("九、分析框架速查表")
lines.append("-" * 95)
lines.append("")
lines.append("  组合              场景              操作        趋势验证")
lines.append("  " + "-" * 75)
lines.append("  低拥挤+高宽度     场景1:启动初期    做多/加仓   价格趋势向上确认")
lines.append("  高拥挤+高宽度     场景2:主升中后段   持有/控仓   价格趋势转弱=减仓")
lines.append("  高拥挤+低宽度     场景3:见顶信号    卖出/避险   全周期下跌=确认")
lines.append("  低拥挤+低宽度     场景4:磨底观望    观望/等待   价格企稳=关注")
lines.append("")
lines.append("  ★趋势信号: 过去{}天一致在同场景 + 价格趋势同向，置信度高".format(HISTORY_DAYS))
lines.append("  ○偶发信号: 今日刚进入该场景，需等待确认")
lines.append("")
lines.append("  当前市场状态: {}".format("强势市场" if avg_w >= 40 else "弱势市场,建议谨慎"))
lines.append("  拥挤度状态: {}".format("资金抱团明显,注意高位风险" if avg_c >= 60 else "资金未集中,无明显板块泡沫"))
lines.append("  行情趋势: 5日平均{:+.2f}%, 10日平均{:+.2f}%, 20日平均{:+.2f}%".format(avg_chg5, avg_chg10, avg_chg20))
lines.append("")
lines.append("=" * 95)
lines.append("  报告生成完毕")
lines.append("=" * 95)

report_text = "\n".join(lines)

# 保存 -> report/ 文件夹，日期命名
REPORT_DIR = os.path.join(BASE_DIR, "report")
os.makedirs(REPORT_DIR, exist_ok=True)
today_str = datetime.now().strftime("%Y-%m-%d")
output_path = os.path.join(REPORT_DIR, "action_report_{}.txt".format(today_str))
with open(output_path, "w", encoding="utf-8") as f:
    f.write(report_text)

print("\n[已保存] {}".format(output_path))
print("  共分析 {} 个行业, 4个场景分布".format(len(industries)))

