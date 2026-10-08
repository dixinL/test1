# -*- coding: utf-8 -*-
"""sw2-trend-analysis 数据访问层薄壳。

基础数据已统一下沉到 general-data/（合并库 sw.db + 获取层 swfetch）。
本文件仅做转发，函数签名与合并前完全一致，下游逻辑零改动。
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERAL_DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "general-data"))
if GENERAL_DATA_DIR not in sys.path:
    sys.path.insert(0, GENERAL_DATA_DIR)

import swdb  # noqa: E402

connect = swdb.connect
init_schema = swdb.init_schema

# industry 维表（fetch 路径 refresh_industries 使用）
upsert_industry = swdb.upsert_industry

# price（合并库含 sw_name 冗余列，读写口径不变）
get_max_trade_date = swdb.get_max_trade_date
get_recent_trade_dates = swdb.get_recent_trade_dates
upsert_prices = swdb.upsert_prices
get_recent_prices = swdb.get_recent_prices
get_all_prices = swdb.get_all_prices
get_latest_snapshot = swdb.get_latest_snapshot

# trend / epsilon
upsert_trends = swdb.upsert_trends
get_trends_by_date = swdb.get_trends_by_date
upsert_epsilon = swdb.upsert_epsilon
get_epsilon_map = swdb.get_epsilon_map
