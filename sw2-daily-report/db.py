"""sw2-daily-report 数据访问层薄壳。

基础数据已统一下沉到 general-data/（合并库 sw.db + 获取层 swfetch）。
本文件仅做转发，函数签名与合并前完全一致，下游逻辑零改动。
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERAL_DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'general-data'))

if GENERAL_DATA_DIR not in sys.path:
    sys.path.insert(0, GENERAL_DATA_DIR)

import swdb

DB_PATH = swdb.DB_PATH
QUOTES_COLUMNS = swdb.QUOTES_COLUMNS
connect = swdb.connect
init_schema = swdb.init_schema
strip_si = swdb.strip_si
to_full_code = swdb.to_full_code
is_valid_code = swdb.is_valid_code
calc_n_chg = swdb.calc_n_chg
upsert_industry = swdb.upsert_industry
upsert_width_from_api = swdb.upsert_width_from_api
upsert_congestion_from_api = swdb.upsert_congestion_from_api
upsert_price_series = swdb.upsert_price_series
build_width_dict = swdb.build_width_dict
build_congestion_dict = swdb.build_congestion_dict
build_quotes_df = swdb.build_quotes_df
latest_trade_date = swdb.latest_trade_date
