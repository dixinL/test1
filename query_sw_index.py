# -*- coding: utf-8 -*-
"""
申万二级行业指数查询工具
查询所有申万二级行业的最新行情数据
支持筛选：按一级行业、按关键词
"""

import akshare as ak
import pandas as pd
import sys
import os
from datetime import datetime


def get_all_sw2_list():
    """获取全部申万二级行业列表"""
    df = ak.sw_index_second_info()
    # 去掉.SI后缀作为查询用的symbol
    df["symbol"] = df["行业代码"].str.replace(".SI", "", regex=False)
    return df


def calc_n_chg(closes, n):
    """计算N日涨跌幅，closes为收盘价列表(按时间升序), n为往前几条K线"""
    if len(closes) <= n:
        return None
    return round((closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100, 2)


def query_sw2_index(info_df, keywords=None, sw1_filter=None, max_workers=5):
    """
    查询申万二级行业指数行情

    参数:
        info_df: 行业列表DataFrame (来自 get_all_sw2_list)
        keywords: 关键词列表，筛选行业名称（如 ["医疗", "银行"]）
        sw1_filter: 一级行业名称列表，筛选上级行业（如 ["银行", "电子"]）
        max_workers: 并发数（当前为顺序执行，避免被封）
    """
    results = []
    total = len(info_df)
    fail_count = 0

    for idx, row in info_df.iterrows():
        code_full = row["行业代码"]
        symbol = row["symbol"]
        name = row["行业名称"]
        sw1 = row["上级行业"]
        count = row["成份个数"]

        # 关键词筛选
        if keywords:
            match = any(kw in name for kw in keywords)
            if not match:
                continue

        # 一级行业筛选
        if sw1_filter:
            if sw1 not in sw1_filter:
                continue

        # 直接调用API获取数据
        try:
            df = ak.index_hist_sw(symbol=symbol, period="day")

            if df is None or df.empty:
                fail_count += 1
                print(f"  [SKIP] {name}({code_full}) 数据为空")
                continue

            closes = df["收盘"].tolist()
            latest = df.iloc[-1]

            chg_1d = calc_n_chg(closes, 1)
            chg_3d = calc_n_chg(closes, 3)
            chg_5d = calc_n_chg(closes, 5)
            chg_10d = calc_n_chg(closes, 10)
            chg_20d = calc_n_chg(closes, 20)

            results.append({
                "代码": code_full,
                "名称": name,
                "一级行业": sw1,
                "成份数": count,
                "日期": str(latest["日期"])[:10],
                "收盘价": round(latest["收盘"], 2),
                "涨跌幅(%)": chg_1d if chg_1d is not None else "-",
                "3日涨跌(%)": chg_3d if chg_3d is not None else "-",
                "5日涨跌(%)": chg_5d if chg_5d is not None else "-",
                "10日涨跌(%)": chg_10d if chg_10d is not None else "-",
                "20日涨跌(%)": chg_20d if chg_20d is not None else "-",
                "成交量(亿手)": round(latest["成交量"], 2),
                "成交额(亿元)": round(latest["成交额"], 2),
            })
            print(f"  [{idx+1}/{total}] {name}({symbol}) | {latest['收盘']:.0f} | 日{chg_1d:+.1f}% 3日{chg_3d:+.1f}%" if chg_3d is not None else f"  [{idx+1}/{total}] {name}({symbol}) | {latest['收盘']:.0f}")
        except Exception as e:
            fail_count += 1
            print(f"  [{idx+1}/{total}] [FAIL] {name}({code_full}): {e}")

    result_df = pd.DataFrame(results)
    if not result_df.empty:
        result_df = result_df.sort_values("涨跌幅(%)", ascending=False)

    return result_df, fail_count


def print_summary(result_df):
    """打印汇总统计"""
    print(f"\n{'=' * 100}")
    print(f"  申万二级行业指数行情汇总")
    print(f"  数据日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 100}")
    print(f"  共 {len(result_df)} 个行业")
    print()

    if result_df.empty:
        return

    up = len(result_df[result_df["涨跌幅(%)"] > 0])
    down = len(result_df[result_df["涨跌幅(%)"] < 0])
    flat = len(result_df[result_df["涨跌幅(%)"] == 0])
    avg_chg = result_df["涨跌幅(%)"].mean()

    print(f"  上涨: {up} 个    下跌: {down} 个    平盘: {flat} 个")
    print(f"  平均涨跌幅: {avg_chg:+.2f}%")
    print()

    # 涨幅前10
    print(f"  【涨幅前10】")
    print(f"  {result_df.nlargest(10, '涨跌幅(%)')[['名称', '一级行业', '收盘价', '涨跌幅(%)', '3日涨跌(%)', '5日涨跌(%)', '10日涨跌(%)', '20日涨跌(%)']].to_string(index=False)}")
    print()

    # 跌幅前10
    print(f"  【跌幅前10】")
    print(f"  {result_df.nsmallest(10, '涨跌幅(%)')[['名称', '一级行业', '收盘价', '涨跌幅(%)', '3日涨跌(%)', '5日涨跌(%)', '10日涨跌(%)', '20日涨跌(%)']].to_string(index=False)}")
    print()

    # 按一级行业汇总
    print(f"  【一级行业平均涨跌】")
    sw1_summary = result_df.groupby("一级行业").agg(
        行业数=("涨跌幅(%)", "count"),
        平均涨跌=("涨跌幅(%)", "mean")
    ).round(2).sort_values("平均涨跌", ascending=False)
    print(f"  {sw1_summary.to_string()}")
    print()


def print_by_sw1(result_df):
    """按一级行业分类展示"""
    print(f"\n{'=' * 100}")
    print(f"  按一级行业分类详情")
    print(f"{'=' * 100}")

    for sw1 in sorted(result_df["一级行业"].unique()):
        sub = result_df[result_df["一级行业"] == sw1].sort_values("涨跌幅(%)", ascending=False)
        print(f"\n  --- [ {sw1} ] ({len(sub)} 个二级行业) ---")
        print(f"  {sub[['名称', '收盘价', '涨跌幅(%)', '3日涨跌(%)', '5日涨跌(%)', '10日涨跌(%)', '20日涨跌(%)', '成份数']].to_string(index=False)}")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    print("=" * 100)
    print("  申万二级行业指数查询")
    print("=" * 100)
    print()

    # 支持命令行参数
    # 用法: python query_sw_index.py                    # 查询全部
    #       python query_sw_index.py 银行                # 按关键词筛选
    #       python query_sw_index.py 银行 电子           # 多个关键词
    #       python query_sw_index.py --sw1 银行 电子     # 按一级行业筛选
    #       python query_sw_index.py --summary           # 只显示汇总

    keywords = None
    sw1_filter = None
    summary_only = False
    by_sw1 = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--sw1":
            sw1_filter = []
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                sw1_filter.append(args[i])
                i += 1
            continue
        elif arg == "--summary":
            summary_only = True
        elif arg == "--by-sw1":
            by_sw1 = True
        elif arg == "--all":
            pass
        else:
            if keywords is None:
                keywords = []
            keywords.append(arg)
        i += 1

    # 1. 获取行业列表
    print("正在获取申万二级行业列表...")
    info_df = get_all_sw2_list()
    print(f"共 {len(info_df)} 个申万二级行业")
    print()

    # 2. 查询行情
    filter_desc = ""
    if keywords:
        filter_desc = f" 筛选关键词: {keywords}"
    elif sw1_filter:
        filter_desc = f" 筛选一级行业: {sw1_filter}"
    else:
        filter_desc = " (全部)"

    print(f"正在查询行情数据...{filter_desc}")
    result_df, fail_count = query_sw2_index(info_df, keywords=keywords, sw1_filter=sw1_filter)

    if fail_count > 0:
        print(f"\n  失败: {fail_count} 个行业查询失败")

    if result_df.empty:
        print("  无数据!")
        sys.exit(0)

    # 3. 保存结果 (quotes/文件夹，日期命名)
    quotes_dir = os.path.join(os.path.dirname(__file__), "quotes")
    os.makedirs(quotes_dir, exist_ok=True)
    date_str = str(result_df.iloc[0]["日期"])
    output_file = os.path.join(quotes_dir, "sw2_index_quotes_{}.csv".format(date_str))
    result_df.to_csv(output_file, index=False, encoding="utf-8-sig")
    print(f"\n[已保存] {output_file}")

    # 4. 打印
    if summary_only:
        print_summary(result_df)
    elif by_sw1:
        print_by_sw1(result_df)
    else:
        print_summary(result_df)
        print_by_sw1(result_df)
