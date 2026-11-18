"""sw2-market-env 数据访问层薄壳。

基础数据统一下沉到 general-data/（合并库 sw.db）。本文件仅做转发，
环境指标读库走 get_env_series / get_erp_series，纯读不写。

命名为 envdb.py 而非 db.py：combine_report 也会加载本目录，
同名的 db.py 会与 recommend 引擎期望的 sw2-daily-report/db.py 冲突。
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
connect = swdb.connect
get_env_series = swdb.get_env_series
get_erp_series = swdb.get_erp_series
latest_trade_date = swdb.latest_trade_date
