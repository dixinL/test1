"""
乐股乐谷 (legulegu.com) 申万2级接口 数据查询与存储

职责: 只负责调用API获取数据，保存为CSV到对应目录
图表生成由 draw_heatmap.py 负责

支持的接口:
  - 均线市场宽度 (width):   /api/stockdata/member-ship/ma-market-width
  - 行业拥挤度 (congestion): /api/stockdata/sw-congestion

使用方式:
  python sw2_market_width_api.py                    # 拉取全部 (宽度 + 拥挤度)
  python sw2_market_width_api.py width              # 仅市场宽度
  python sw2_market_width_api.py congestion         # 仅拥挤度

数据存储:
  width/       width_YYYY-MM-DD.csv        市场宽度数据
  congestion/  congestion_YYYY-MM-DD.csv    行业拥挤度数据
"""

import hashlib
import json
import os
import sys
from datetime import date, timedelta

import requests
import pandas as pd


# ============================================================
# 数据存储目录规范
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WIDTH_DIR = os.path.join(BASE_DIR, "width")
QUOTES_DIR = os.path.join(BASE_DIR, "quotes")
CONGESTION_DIR = os.path.join(BASE_DIR, "congestion")


# ============================================================
# CSV 存储工具
# ============================================================

def _save_to_dir(directory, filename, data_dict):
    """保存数据到指定目录的CSV文件"""
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)

    dates = data_dict.get("dates", [])
    sw_code_names = data_dict.get("swCodeNames", [])

    if "maMarketWidth" in data_dict:
        rows = []
        for item in sw_code_names:
            code = item["indexCode"]
            name = item["indexName"]
            row_data = {"代码": code, "名称": name}
            values = data_dict["maMarketWidth"].get(code, [])
            for i, d in enumerate(dates):
                if i < len(values):
                    v = values[i]
                    val = v.get("value20", 0) if isinstance(v, dict) else (v or 0)
                    row_data[d] = int(val)
                else:
                    row_data[d] = 0
            rows.append(row_data)
        df = pd.DataFrame(rows) if rows else pd.DataFrame()

    elif "congestions" in data_dict:
        rows = []
        for item in sw_code_names:
            code = item["indexCode"]
            name = item["indexName"]
            row_data = {"代码": code, "名称": name}
            values = data_dict["congestions"].get(code, [])
            for i, d in enumerate(dates):
                if i < len(values):
                    v = values[i]
                    if isinstance(v, dict):
                        t = v.get("turnoverRateFQuantile", 0) or 0
                        a = v.get("amountCongestionQuantile", 0) or 0
                        row_data[d + "_换手"] = round(t, 1)
                        row_data[d + "_成交额拥挤"] = round(a, 1)
                    else:
                        row_data[d + "_换手"] = 0
                        row_data[d + "_成交额拥挤"] = 0
                else:
                    row_data[d + "_换手"] = 0
                    row_data[d + "_成交额拥挤"] = 0
            rows.append(row_data)
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
    else:
        return None

    if not df.empty:
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        print("[Saved] {}".format(filepath))
    return filepath


# ============================================================
# Token 与 HTTP
# ============================================================

def xT(d=None) -> str:
    if d is None:
        d = date.today()
    elif isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def generate_token(d=None) -> str:
    date_str = xT(d)
    token = md5_hash(date_str)
    print("[Token] {} -> {}".format(date_str, token))
    return token


_session = requests.Session()
_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://legulegu.com/',
}


def _do_request(url: str, params: dict) -> dict:
    if "token" not in params:
        params["token"] = generate_token()
    query_parts = ["{}={}".format(k, v) for k, v in params.items()]
    full_url = "{}?{}".format(url, "&".join(query_parts))
    print("[URL] {}".format(full_url))

    try:
        _session.get("https://legulegu.com/", headers=_headers, timeout=10)
    except Exception:
        pass

    response = _session.get(full_url, headers=_headers, timeout=30)

    if response.status_code == 200 and response.text.strip():
        try:
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}
    return {"error": "HTTP {}".format(response.status_code)}


# ============================================================
# API 接口函数
# ============================================================

def fetch_ma_market_width(days_back: int = 30, level: int = 2,
                           ma_type: str = "value20", verbose=True) -> dict:
    """获取均线市场宽度数据，自动存CSV到 width/"""
    url = "https://legulegu.com/api/stockdata/member-ship/ma-market-width"
    today = date.today()
    start_date = today - timedelta(days=days_back + 10)

    params = {
        "level": level,
        "startDate": xT(start_date),
        "endDate": xT(today),
        "previous": False,
        "next": False,
        "maType": ma_type,
        "severalTradeDays": days_back,
    }
    data = _do_request(url, params)

    if verbose and "error" not in data:
        # 用数据中最新交易日日期命名，而非今天
        dates = data.get("dates", [])
        latest_date = dates[-1] if dates else xT(date.today())
        _save_to_dir(WIDTH_DIR, "width_{}.csv".format(latest_date), data)

    return data


def fetch_sw_congestion(days_back: int = 30, level: int = 2, verbose=True) -> dict:
    """获取行业拥挤度数据，自动存CSV到 congestion/"""
    url = "https://legulegu.com/api/stockdata/sw-congestion"
    params = {
        "level": level,
        "severalTradeDays": days_back,
    }
    data = _do_request(url, params)

    if verbose and "error" not in data:
        # 用数据中最新交易日日期命名，而非今天
        dates = data.get("dates", [])
        latest_date = dates[-1] if dates else xT(date.today())
        _save_to_dir(CONGESTION_DIR, "congestion_{}.csv".format(latest_date), data)

    return data


# ============================================================
# 数据矩阵提取 (从API返回的dict中)
# ============================================================

def extract_matrix(data: dict, value_key: str = None):
    """
    从API数据中提取二维矩阵
    
    返回: (matrix, row_names, dates, col_labels, vmax)
    """
    dates = data.get("dates", [])
    sw_code_names = data.get("swCodeNames", [])

    if value_key is None:
        if "maMarketWidth" in data:
            value_key = "value20"
            data_field = "maMarketWidth"
        elif "congestions" in data:
            value_key = "turnoverRateFQuantile"
            data_field = "congestions"
        else:
            raise ValueError("无法识别的数据结构")
    else:
        data_field = "maMarketWidth" if "maMarketWidth" in data else "congestions"

    raw_data = data.get(data_field, {})
    n_rows = len(sw_code_names)
    n_cols = len(dates)
    matrix = []
    row_names = []
    all_values = []

    for code_item in sw_code_names:
        code = code_item["indexCode"]
        name = code_item["indexName"]
        row_names.append(name)
        row_values = []
        if code in raw_data:
            values = raw_data[code]
            for j in range(n_cols):
                if j < len(values):
                    item = values[j]
                    val = item.get(value_key, 0) if isinstance(item, dict) else (item or 0)
                    iv = int(val) if val != 0 else 0
                    row_values.append(iv)
                    if iv > 0:
                        all_values.append(iv)
                else:
                    row_values.append(0)
        else:
            row_values = [0] * n_cols
        matrix.append(row_values)

    vmax = max(all_values) if all_values else 100
    col_labels = [d.split("-")[1] + "-" + d.split("-")[2] for d in dates]

    return matrix, row_names, dates, col_labels, vmax


# ============================================================
# 从CSV文件提取矩阵 (供 draw_heatmap.py 使用)
# ============================================================

def extract_matrix_from_csv(csv_path: str, data_type: str = "width"):
    """
    从已保存的CSV文件中读取并提取矩阵
    
    参数:
        csv_path: CSV文件路径
        data_type: "width" 或 "congestion"
    
    返回: (matrix, row_names, dates, col_labels, vmax)
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig")

    # 提取日期列
    if data_type == "width":
        date_cols = [c for c in df.columns if c not in ("代码", "名称")]
    else:
        # congestion: 去掉 _换手 和 _成交额拥挤 后缀
        all_cols = [c for c in df.columns if c not in ("代码", "名称")]
        date_set = set()
        for c in all_cols:
            base = c.rsplit("_", 1)[0]
            date_set.add(base)
        date_cols = sorted(date_set)

    dates = date_cols
    row_names = df["名称"].tolist()
    n_rows = len(row_names)
    n_cols = len(dates)
    matrix = []
    all_values = []

    for _, row in df.iterrows():
        row_values = []
        for d in dates:
            if data_type == "width":
                val = row.get(d, 0)
            else:
                t_col = "{}_换手".format(d)
                val = row.get(t_col, 0)
            try:
                iv = int(val) if isinstance(val, (int, float)) and not (isinstance(val, float) and __import__('math').isnan(val)) else 0
            except (ValueError, TypeError):
                iv = 0
            row_values.append(iv)
            if iv > 0:
                all_values.append(iv)
        matrix.append(row_values)

    vmax = max(all_values) if all_values else 100
    col_labels = [d.split("-")[1] + "-" + d.split("-")[2] for d in dates]

    return matrix, row_names, dates, col_labels, vmax


def get_chart_title(data_type: str, dates=None) -> str:
    titles = {"width": "均线市场宽度 (MA20)", "congestion": "行业拥挤度 (换手率分位数)"}
    title = titles.get(data_type, data_type)
    if dates and len(dates) >= 2:
        title += "\n{} ~ {}".format(dates[0], dates[-1])
    return title


# ============================================================
# 命令行入口
# ============================================================

def parse_args(argv):
    args = argv[1:] if argv else []
    api_types = []
    for a in args:
        if a.lower() in ("width", "congestion"):
            api_types.append(a.lower())
        elif a in ("-h", "--help"):
            return None
    # 默认两个都拉
    if not api_types:
        api_types = ["width", "congestion"]
    return api_types


def print_usage():
    print("""
乐股乐谷 申万2级接口 数据查询工具

用法:
  python sw2_market_width_api.py [接口...]

接口 (可多选，默认全部):
  width       均线市场宽度
  congestion  行业拥挤度

示例:
  python sw2_market_width_api.py                  # 拉取全部
  python sw2_market_width_api.py width            # 仅市场宽度
  python sw2_market_width_api.py congestion       # 仅拥挤度

输出:
  width/width_YYYY-MM-DD.csv
  congestion/congestion_YYYY-MM-DD.csv

生成图表请用: python draw_heatmap.py
""")


if __name__ == "__main__":
    api_types = parse_args(sys.argv)

    if api_types is None:
        print_usage()
        sys.exit(0)

    print("=" * 60)
    print("  乐股乐谷 申万2级接口 数据查询")
    print("  接口: {}".format(", ".join(api_types)))
    print("=" * 60)

    for t in api_types:
        print("\n[{}/{}] 获取 {} ...".format(
            api_types.index(t) + 1, len(api_types),
            {"width": "均线市场宽度", "congestion": "行业拥挤度"}[t]))

        if t == "width":
            fetch_ma_market_width(days_back=30, level=2)
        else:
            fetch_sw_congestion(days_back=30, level=2)

    print("\n" + "=" * 60)
    print("  数据获取完成!")
    print("  图表生成: python draw_heatmap.py [width|congestion] [png|html|both]")
    print("=" * 60)
