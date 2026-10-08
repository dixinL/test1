"""AkShare 数据拉取层（薄壳）。

实际抓取逻辑已统一下沉到 general-data/swfetch：
- 板块列表: swfetch.get_second_industries()
- 指数日线: swfetch.fetch_hist_raw() / clean_hist() / fetch_and_store_price()

本文件保留 fetch_and_store 入口以兼容 main.py / probe_epsilon.py 的调用。
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERAL_DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'general-data'))

if GENERAL_DATA_DIR not in sys.path:
    sys.path.insert(0, GENERAL_DATA_DIR)

import swfetch
import db

normalize_code = swfetch.normalize_code
get_second_industries = swfetch.get_second_industries


def fetch_and_store(
    conn, sw_code: str, sw_name: str, verbose: bool = True
) -> dict:
    """拉取单板块 → 校验 → 增量写 price。返回统计 dict。"""
    return swfetch.fetch_and_store_price(conn, sw_code, sw_name, verbose=verbose)
