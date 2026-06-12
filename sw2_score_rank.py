# -*- coding: utf-8 -*-
"""
申万二级行业量化评分排名工具

评分规则（三项加权）：
  1. 价格趋势 (35%): CSV中 3/5/10/20 日涨跌幅的加权趋势
  2. 市场宽度趋势 (35%): 近 3/5/10/20 天的市场宽度变化趋势
  3. 拥挤度趋势 (30%): 近 3/5/10/20 天的拥挤度变化趋势

数据来源:
  quotes/sw2_index_quotes_*.csv   行情涨跌幅
  width/width_*.csv               市场宽度
  congestion/congestion_*.csv     行业拥挤度

输出: rank/sw2_score_rank_YYYY-MM-DD.csv  所有行业综合得分排序表
"""

import os
import sys
import subprocess
import pandas as pd
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据目录
WIDTH_DIR = os.path.join(BASE_DIR, "width")
QUOTES_DIR = os.path.join(BASE_DIR, "quotes")
CONGESTION_DIR = os.path.join(BASE_DIR, "congestion")
RANK_DIR = os.path.join(BASE_DIR, "rank")

# ============================================================
# 权重配置
# ============================================================
W_PRICE = 0.35    # 价格趋势权重
W_WIDTH = 0.35    # 市场宽度趋势权重
W_CONG = 0.30     # 拥挤度趋势权重

# 时间衰减权重: 近 -> 远
T_WEIGHTS = [0.4, 0.3, 0.2, 0.1]  # 对应 3日/5日/10日/20日
T_OFFSETS = [3, 5, 10, 20]


# ============================================================
# 工具函数: 查找最新CSV
# ============================================================

def _find_csv_by_date(directory, prefix, target_date):
    """查找指定日期的CSV文件"""
    target_name = "{}{}.csv".format(prefix, target_date)
    path = os.path.join(directory, target_name)
    return path if os.path.exists(path) else None


def _find_latest_csv(directory, prefix):
    """在目录中查找最新的CSV文件 (回退用)"""
    if not os.path.isdir(directory):
        return None
    csvs = sorted([f for f in os.listdir(directory) if f.startswith(prefix) and f.endswith(".csv")], reverse=True)
    return os.path.join(directory, csvs[0]) if csvs else None


# ============================================================
# 1a. 加载行情 CSV (quotes/)
# ============================================================

def load_quotes_csv():
    """加载行情数据: 优先今天 -> 调API -> 回退最新"""
    os.makedirs(QUOTES_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 先找当天
    csv_path = _find_csv_by_date(QUOTES_DIR, "sw2_index_quotes_", today_str)
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[命中] 行情: {} ({})".format(os.path.basename(csv_path), df.shape))
        return df

    # 2) 调API生成
    print("[INFO] quotes/ 下无今日({})数据，正在拉取...".format(today_str))
    subprocess.run([sys.executable, os.path.join(BASE_DIR, "query_sw_index.py"), "--summary"],
                   cwd=BASE_DIR, capture_output=True)

    # 3) 再试当天
    csv_path = _find_csv_by_date(QUOTES_DIR, "sw2_index_quotes_", today_str)
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[已获取] 行情: {}".format(os.path.basename(csv_path)))
        return df

    # 4) 回退最新
    csv_path = _find_latest_csv(QUOTES_DIR, "sw2_index_quotes_")
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[回退] 行情: {}".format(os.path.basename(csv_path)))
        return df

    print("ERROR: 无法获取行情CSV")
    return None


def build_name_map(df):
    """名称 -> {chg_3d, chg_5d, chg_10d, chg_20d} 映射"""
    m = {}
    if df is None or df.empty:
        return m
    for _, r in df.iterrows():
        try:
            m[r["名称"]] = {
                "chg_3d": float(r["3日涨跌(%)"]) if str(r["3日涨跌(%)"]) != "-" else 0,
                "chg_5d": float(r["5日涨跌(%)"]) if str(r["5日涨跌(%)"]) != "-" else 0,
                "chg_10d": float(r["10日涨跌(%)"]) if str(r["10日涨跌(%)"]) != "-" else 0,
                "chg_20d": float(r["20日涨跌(%)"]) if str(r["20日涨跌(%)"]) != "-" else 0,
            }
        except (ValueError, TypeError):
            pass
    return m


def _calc_n_chg(closes, n):
    """计算N日涨跌幅, closes按时间升序"""
    if len(closes) <= n:
        return None
    return round((closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100, 2)


def load_or_generate_quotes_for_date(target_date_str):
    """
    加载指定日期的行情数据。
    先找 quotes/ 下对应CSV，没有就调 akshare 接口生成。
    返回 name_map 或 None
    """
    os.makedirs(QUOTES_DIR, exist_ok=True)
    target_path = os.path.join(QUOTES_DIR, "sw2_index_quotes_{}.csv".format(target_date_str))

    # 1) 已有文件直接加载
    if os.path.exists(target_path):
        df = pd.read_csv(target_path, encoding="utf-8-sig")
        print("  [命中] {}".format(os.path.basename(target_path)))
        return build_name_map(df)

    # 2) 没有 → 调 akshare 生成该日期快照
    print("  [生成] 正在获取 {} 的行情快照...".format(target_date_str))
    import akshare as ak

    # 获取行业列表
    try:
        info_df = ak.sw_index_second_info()
    except Exception as e:
        print("  [ERROR] 获取行业列表失败: {}".format(e))
        return None

    info_df["symbol"] = info_df["行业代码"].str.replace(".SI", "", regex=False)

    results = []
    total = len(info_df)
    for idx, row in info_df.iterrows():
        code_full = row["行业代码"]
        symbol = row["symbol"]
        name = row["行业名称"]

        try:
            hist = ak.index_hist_sw(symbol=symbol, period="day")
            if hist is None or hist.empty or "收盘" not in hist.columns:
                continue

            closes = hist["收盘"].tolist()
            dates_list = hist["日期"].tolist()

            # 找到 target_date 在历史中的位置（取最近一个 <= target_date 的交易日）
            target_idx = -1
            for di, d in enumerate(dates_list):
                ds = str(d)[:10]
                if ds <= target_date_str:
                    target_idx = di
                else:
                    break

            if target_idx < 0:
                continue

            # 以该位置为基准计算各周期涨跌幅
            chg_1d = _calc_n_chg(closes[:target_idx + 1], 1)
            chg_3d = _calc_n_chg(closes[:target_idx + 1], 3)
            chg_5d = _calc_n_chg(closes[:target_idx + 1], 5)
            chg_10d = _calc_n_chg(closes[:target_idx + 1], 10)
            chg_20d = _calc_n_chg(closes[:target_idx + 1], 20)

            latest = hist.iloc[target_idx]

            results.append({
                "代码": code_full,
                "名称": name,
                "一级行业": row.get("上级行业", ""),
                "成份数": row.get("成份个数", ""),
                "日期": str(latest["日期"])[:10],
                "收盘价": round(latest["收盘"], 2),
                "涨跌幅(%)": chg_1d if chg_1d is not None else "-",
                "3日涨跌(%)": chg_3d if chg_3d is not None else "-",
                "5日涨跌(%)": chg_5d if chg_5d is not None else "-",
                "10日涨跌(%)": chg_10d if chg_10d is not None else "-",
                "20日涨跌(%)": chg_20d if chg_20d is not None else "-",
                "成交量(亿手)": round(latest.get("成交量", 0), 2),
                "成交额(亿元)": round(latest.get("成交额", 0), 2),
            })
        except Exception as e:
            pass

    if not results:
        print("  [WARN] 未生成任何行情数据")
        return None

    result_df = pd.DataFrame(results)
    result_df.to_csv(target_path, index=False, encoding="utf-8-sig")
    print("  [已保存] {} ({})".format(os.path.basename(target_path), len(results)))
    return build_name_map(result_df)


# ============================================================
# 1b. 加载市场宽度 (width/) -> 转内部格式
# ============================================================

def _parse_width_df(df):
    """解析width CSV为内部dict格式"""
    data = {"dates": [], "swCodeNames": [], "maMarketWidth": {}}
    date_cols = [c for c in df.columns if c not in ("代码", "名称")]
    data["dates"] = date_cols
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        name = row.get("名称", "")
        if code and name:
            data["swCodeNames"].append({"indexCode": code, "indexName": name})
            values = [{"value20": int(row.get(d, 0)) if isinstance(row.get(d, 0), (int, float)) else 0} for d in date_cols]
            data["maMarketWidth"][code] = values
    print("  转换: {} 行业, {} 天".format(len(data["swCodeNames"]), len(date_cols)))
    return data


def load_width_data():
    """
    加载市场宽度数据: 优先今天 -> 调API -> 回退最新
    返回 {dates:[], swCodeNames:[], maMarketWidth:{code:[{value20:N},...]}}
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 先找当天
    csv_path = _find_csv_by_date(WIDTH_DIR, "width_", today_str)
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[命中] 宽度: {} ({})".format(os.path.basename(csv_path), df.shape))
        return _parse_width_df(df)

    # 2) 调接口
    print("[INFO] width/ 下无今日({})数据，自动调用 API ...".format(today_str))
    subprocess.run([sys.executable, os.path.join(BASE_DIR, "sw2_market_width_api.py"), "width"],
                   cwd=BASE_DIR, capture_output=True)

    # 3) 再试当天
    csv_path = _find_csv_by_date(WIDTH_DIR, "width_", today_str)
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[已获取] 宽度: {}".format(os.path.basename(csv_path)))
        return _parse_width_df(df)

    # 4) 回退最新
    csv_path = _find_latest_csv(WIDTH_DIR, "width_")
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[回退] 宽度: {}".format(os.path.basename(csv_path)))
        return _parse_width_df(df)

    print("ERROR: 无法获取市场宽度数据")
    return None


# ============================================================
# 1c. 加载拥挤度 (congestion/) -> 转内部格式
# ============================================================

def _parse_congestion_df(df):
    """解析congestion CSV为内部dict格式"""
    data = {"dates": [], "swCodeNames": [], "congestions": {}}
    all_cols = [c for c in df.columns if c not in ("代码", "名称")]
    date_set = set()
    for c in all_cols:
        base = c.rsplit("_", 1)[0]
        date_set.add(base)
    dates = sorted(date_set)
    data["dates"] = dates
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
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


def load_congestion_data():
    """
    加载拥挤度数据: 优先今天 -> 调API -> 回退最新
    返回 {dates:[], swCodeNames:[], congestions:{code:[{...},...]}}
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 先找当天
    csv_path = _find_csv_by_date(CONGESTION_DIR, "congestion_", today_str)
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[命中] 拥挤度: {} ({})".format(os.path.basename(csv_path), df.shape))
        return _parse_congestion_df(df)

    # 2) 调接口
    print("[INFO] congestion/ 下无今日({})数据，自动调用 API ...".format(today_str))
    subprocess.run([sys.executable, os.path.join(BASE_DIR, "sw2_market_width_api.py"), "congestion"],
                   cwd=BASE_DIR, capture_output=True)

    # 3) 再试当天
    csv_path = _find_csv_by_date(CONGESTION_DIR, "congestion_", today_str)
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[已获取] 拥挤度: {}".format(os.path.basename(csv_path)))
        return _parse_congestion_df(df)

    # 4) 回退最新
    csv_path = _find_latest_csv(CONGESTION_DIR, "congestion_")
    if csv_path:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        print("[回退] 拥挤度: {}".format(os.path.basename(csv_path)))
        return _parse_congestion_df(df)

    print("ERROR: 无法获取拥挤度数据")
    return None


# ============================================================
# 2. 数据提取辅助
# ============================================================

def get_value_at_offset(values, offset):
    """从values列表中获取第offset个元素的值"""
    if not values or offset >= len(values) or offset < 0:
        return None
    v = values[offset]
    if isinstance(v, dict):
        return v.get("value20") or v.get("turnoverRateFQuantile") or 0
    return v if v is not None else 0


def get_width_change(w_data, code, offset, base_offset=0):
    """市场宽度N日变化量 (基准日 - N日前)"""
    vals = w_data["maMarketWidth"].get(code, [])
    now = get_value_at_offset(vals, base_offset)
    then = get_value_at_offset(vals, base_offset + offset)
    if now is None or then is None:
        return 0
    return (now - then)


def get_congestion_change(c_data, code, offset, base_offset=0):
    """拥挤度N日变化量 (基准日 - N日前)，取换手率+成交额均值"""
    vals = c_data["congestions"].get(code, [])

    def avg_cong(v):
        if isinstance(v, dict):
            t = v.get("turnoverRateFQuantile", 0) or 0
            a = v.get("amountCongestionQuantile", 0) or 0
            return (t + a) / 2
        return 0

    now = avg_cong(vals[base_offset]) if base_offset < len(vals) else 0
    then_idx = base_offset + offset
    then = avg_cong(vals[then_idx]) if then_idx < len(vals) else 0
    return (now - then)


# ============================================================
# 3. 计算三项得分
# ============================================================

def calc_price_score(name_map, name):
    """价格趋势得分: raw = chg_3d*0.4 + chg_5d*0.3 + chg_10d*0.2 + chg_20d*0.1"""
    q = name_map.get(name, {})
    raw = 0
    for w_i, offset in zip(T_WEIGHTS, T_OFFSETS):
        chg = q.get("chg_{}d".format(offset), 0)
        raw += chg * w_i
    return raw


def calc_width_score(w_data, code, base_offset=0):
    """市场宽度趋势得分: 变化加权 + 当前水平"""
    vals = w_data["maMarketWidth"].get(code, [])
    now = get_value_at_offset(vals, base_offset) or 0

    raw = 0
    for w_i, offset in zip(T_WEIGHTS, T_OFFSETS):
        change = get_width_change(w_data, code, offset, base_offset=base_offset)
        raw += change * w_i

    raw += now * 0.15  # 基准日宽度绝对位置加分
    return raw


def calc_congestion_score(c_data, code, base_offset=0):
    """拥挤度趋势得分: 高拥挤下降=好, 低拥挤上升=好, 中等趋近50=好"""
    vals = c_data["congestions"].get(code, [])
    if not vals or base_offset >= len(vals) or not isinstance(vals[base_offset], dict):
        return 0

    t0 = vals[base_offset].get("turnoverRateFQuantile", 0) or 0
    a0 = vals[base_offset].get("amountCongestionQuantile", 0) or 0
    cur_cong = (t0 + a0) / 2

    raw = 0
    for w_i, offset in zip(T_WEIGHTS, T_OFFSETS):
        change = get_congestion_change(c_data, code, offset, base_offset=base_offset)

        if cur_cong >= 60:
            raw += (-change) * w_i          # 高拥挤: 降=好
        elif cur_cong <= 40:
            raw += change * w_i             # 低拥挤: 升=好
        else:
            dist_now = abs(cur_cong - 50)
            new_cong = cur_cong - change     # N日前值
            dist_then = abs(new_cong - 50)
            if dist_now < dist_then:
                raw += w_i * 0.5
            elif dist_now > dist_then:
                raw += w_i * (-0.5)

    return raw


# ============================================================
# 4. 百分位归一化
# ============================================================

def percentile_normalize(scores, reverse=False):
    """将原始分数归一化到 0-100 (百分位排名)"""
    if not scores or len(scores) < 2:
        return [50] * len(scores)

    n = len(scores)
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=reverse)
    result = [0] * n
    for rank, (orig_idx, _) in enumerate(indexed):
        result[orig_idx] = round(rank / (n - 1) * 100, 1)
    return result


# ============================================================
# 5. 主流程
# ============================================================

def main():
    print("=" * 80)
    print("  申万二级行业量化评分排名")
    print("  价格趋势({:.0%}) + 宽度趋势({:.0%}) + 拥挤度趋势({:.0%})".format(W_PRICE, W_WIDTH, W_CONG))
    print("=" * 80)
    print()

    # --- 加载三项数据 ---
    print("[1/3] 加载行情数据 ...")
    quotes_df = load_quotes_csv()
    name_map = build_name_map(quotes_df)

    print()
    print("[2/3] 加载市场宽度数据 ...")
    w_data = load_width_data()

    print()
    print("[3/3] 加载拥挤度数据 ...")
    c_data = load_congestion_data()

    if not w_data or not c_data:
        print("ERROR: 缺少必要数据!")
        return

    # 构建行业映射
    code_to_name = {}
    for item in w_data["swCodeNames"]:
        code_to_name[item["indexCode"]] = item["indexName"]
    for item in c_data["swCodeNames"]:
        if item["indexCode"] not in code_to_name:
            code_to_name[item["indexCode"]] = item["indexName"]

    # ---- 对齐基准日期 (三个数据源) ----
    # width/congestion/quotes 的最新交易日可能不同
    # 以三者共有的最新日期为基准, 分别确定各自在该基准的 offset
    dates_w = w_data.get("dates", [])
    dates_c = c_data.get("dates", [])

    # 提取行情日期
    quotes_date = None
    if quotes_df is not None and not quotes_df.empty:
        quotes_date = str(quotes_df.iloc[0]["日期"])[:10]

    set_w, set_c = set(dates_w), set(dates_c)
    common_wc = sorted(set_w & set_c)

    if common_wc:
        base_date = common_wc[-1]  # 共同最新
    else:
        latest_w = dates_w[-1] if dates_w else ""
        latest_c = dates_c[-1] if dates_c else ""
        base_date = max(latest_w, latest_c)

    base_offset_w = dates_w.index(base_date) if base_date in set_w else (len(dates_w) - 1)
    base_offset_c = dates_c.index(base_date) if base_date in set_c else (len(dates_c) - 1)
    actual_w = dates_w[base_offset_w]
    actual_c = dates_c[base_offset_c]

    # 行情对齐检查
    q_info = ""
    if quotes_date:
        q_info = " | quotes={}".format(quotes_date)
        if quotes_date != base_date and quotes_date != actual_w and quotes_date != actual_c:
            print("  [WARN] 行情日期({})与基准日期({})不一致".format(quotes_date, base_date))

    print("\n[INFO] 基准日期对齐: 基准={} | width[{}]={} | cong[{}]={}{}".format(
        base_date, base_offset_w, actual_w, base_offset_c, actual_c, q_info))

    # ---- 确定"3天前"的基准偏移量 ----
    # 用 width 的日期序列来定位"3天前"(因为宽度数据通常更完整)
    offset_3d_ago_w = base_offset_w - 3
    if offset_3d_ago_w < 0:
        offset_3d_ago_w = 0
        date_3d_ago_str = dates_w[0]
    else:
        date_3d_ago_str = dates_w[offset_3d_ago_w]

    # congestion 的 3天前也要对应
    offset_3d_ago_c = base_offset_c - 3
    if offset_3d_ago_c < 0:
        offset_3d_ago_c = 0

    print("[INFO] 3天前: {} (width_off={}, cong_off={})".format(
        date_3d_ago_str, offset_3d_ago_w, offset_3d_ago_c))

    # ---- 尝试获取3天前的行情数据 (没有就调 akshare 接口生成) ----
    name_map_3d = None
    if offset_3d_ago_w > 0 and date_3d_ago_str != "N/A":
        print("\n[INFO] 获取3天前({})行情数据...".format(date_3d_ago_str))
        name_map_3d = load_or_generate_quotes_for_date(date_3d_ago_str)
        if not name_map_3d:
            print("[WARN] 3天前行情获取失败，价格趋势将使用今日数据近似")
    else:
        print("\n[WARN] 数据不足3天，无法计算3天前得分")

    # ---- 收集所有行业及其得分 ----
    all_codes = set(w_data["maMarketWidth"].keys()) | set(c_data["congestions"].keys())

    rows = []
    for code in all_codes:
        name = code_to_name.get(code, code)

        # 今日行情匹配
        use_name_map = name_map if name in name_map else None

        # 3天前行情 (已通过 load_or_generate_quotes_for_date 获取)
        nm_3d = name_map_3d

        if not use_name_map:
            continue

        # ===== 当日得分 =====
        s_price = calc_price_score(use_name_map, name)
        s_width = calc_width_score(w_data, code, base_offset=base_offset_w)
        s_cong = calc_congestion_score(c_data, code, base_offset=base_offset_c)

        # ===== 3天前得分 =====
        if offset_3d_ago_w > 0 and date_3d_ago_str != "N/A":
            s_price_3d = calc_price_score(nm_3d, name) if nm_3d else s_price
            s_width_3d = calc_width_score(w_data, code, base_offset=offset_3d_ago_w)
            s_cong_3d = calc_congestion_score(c_data, code, base_offset=offset_3d_ago_c)
        else:
            s_price_3d = s_width_3d = s_cong_3d = None

        rows.append({
            "code": code,
            "name": name,
            "s_price_raw": s_price,
            "s_width_raw": s_width,
            "s_cong_raw": s_cong,
            # 3天前原始分
            "s_price_raw_3d": s_price_3d,
            "s_width_raw_3d": s_width_3d,
            "s_cong_raw_3d": s_cong_3d,
        })

    print("\n有效行业: {} 个".format(len(rows)))
    if not rows:
        print("无有效数据!")
        return

    # ===== 百分位归一化: 当日得分 =====
    price_norm = percentile_normalize([r["s_price_raw"] for r in rows])
    width_norm = percentile_normalize([r["s_width_raw"] for r in rows])
    cong_norm = percentile_normalize([r["s_cong_raw"] for r in rows])

    # ===== 百分位归一化: 3天前得分 (仅对有数据的行业) =====
    rows_with_3d = [i for i, r in enumerate(rows) if r["s_price_raw_3d"] is not None]

    price_norm_3d = [50.0] * len(rows)
    width_norm_3d = [50.0] * len(rows)
    cong_norm_3d = [50.0] * len(rows)

    if rows_with_3d:
        p3 = percentile_normalize([rows[i]["s_price_raw_3d"] for i in rows_with_3d])
        w3 = percentile_normalize([rows[i]["s_width_raw_3d"] for i in rows_with_3d])
        c3 = percentile_normalize([rows[i]["s_cong_raw_3d"] for i in rows_with_3d])
        for idx, orig_i in enumerate(rows_with_3d):
            price_norm_3d[orig_i] = round(p3[idx], 1)
            width_norm_3d[orig_i] = round(w3[idx], 1)
            cong_norm_3d[orig_i] = round(c3[idx], 1)

    # 组装最终得分
    for i, r in enumerate(rows):
        r["s_price"] = round(price_norm[i], 1)
        r["s_width"] = round(width_norm[i], 1)
        r["s_cong"] = round(cong_norm[i], 1)
        r["total"] = round(
            r["s_price"] * W_PRICE +
            r["s_width"] * W_WIDTH +
            r["s_cong"] * W_CONG, 1
        )
        # 3天前综合分
        if r["s_price_raw_3d"] is not None:
            r["total_3d"] = round(
                price_norm_3d[i] * W_PRICE +
                width_norm_3d[i] * W_WIDTH +
                cong_norm_3d[i] * W_CONG, 1
            )
        else:
            r["total_3d"] = None

    rows.sort(key=lambda x: x["total"], reverse=True)

    # ============================================================
    # 输出 -> rank/
    # ============================================================
    os.makedirs(RANK_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    result_df = pd.DataFrame(rows)
    result_df = result_df[["code", "name", "total", "s_price", "s_width", "s_cong", "total_3d"]]
    result_df.columns = ["代码", "名称", "综合得分", "价格趋势(35%)", "宽度趋势(35%)", "拥挤度趋势(30%)",
                         "3天前得分"]
    result_df.insert(0, "排名", range(1, len(result_df) + 1))

    output_csv = os.path.join(RANK_DIR, "sw2_score_rank_{}.csv".format(today_str))
    result_df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print("[已保存] {}".format(output_csv))

    # 打印结果
    print()
    print("=" * 90)
    print("  TOP 30 (综合得分从高到低)")
    print("=" * 90)
    display_cols = [c for c in result_df.columns if c in ("排名","名称","综合得分","3天前得分")]
    print(result_df.head(30)[display_cols].to_string(index=False))

    # 变化方向统计
    valid_both = result_df[result_df["3天前得分"].notna()]
    up_count = sum(1 for _, r in valid_both.iterrows() if r["综合得分"] > r["3天前得分"])
    down_count = sum(1 for _, r in valid_both.iterrows() if r["综合得分"] < r["3天前得分"])
    same_count = sum(1 for _, r in valid_both.iterrows() if r["综合得分"] == r["3天前得分"])
    print()
    print("  得分变化 vs 3天前({}): 上升{} / 持平{} / 下降{}".format(
        date_3d_ago_str, up_count, same_count, down_count))

    print()
    print("=" * 90)
    print("  BOTTOM 10")
    print("=" * 90)
    print(result_df.tail(10)[display_cols].to_string(index=False))

    # 统计
    scores = result_df["综合得分"].tolist()
    print()
    print("=" * 80)
    print("  得分分布")
    print("=" * 80)
    print("  均值: {:.1f}  中位数: {:.1f}  最高: {:.1f}  最低: {:.1f}".format(
        sum(scores) / len(scores),
        sorted(scores)[len(scores) // 2],
        max(scores),
        min(scores),
    ))
    print("  >=70: {} 个    >=60: {} 个    <30: {} 个".format(
        sum(1 for s in scores if s >= 70),
        sum(1 for s in scores if s >= 60),
        sum(1 for s in scores if s < 30),
    ))
    print("  三项均值: 价格={:.1f}  宽度={:.1f}  拥挤={:.1f}".format(
        result_df["价格趋势(35%)"].mean(),
        result_df["宽度趋势(35%)"].mean(),
        result_df["拥挤度趋势(30%)"].mean(),
    ))
    print()
    print("  完成! 共 {} 个行业 | 结果存于 rank/".format(len(rows)))


if __name__ == "__main__":
    main()
