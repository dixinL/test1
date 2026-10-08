"""全局配置。

集中管理窗口、ε 分位数、一致性口径、计算取数窗口、路径、重试与数据校验参数。
板块列表运行时从 AkShare 获取，不在此硬编码。
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(BASE_DIR, 'data')
EXPORT_DIR = os.path.join(DATA_DIR, 'exports')
DB_PATH = os.path.join(REPO_DIR, 'general-data', 'sw.db')
EPSILON_PROBE_CSV = os.path.join(DATA_DIR, 'epsilon_probe.csv')

WINDOWS = [5, 10, 30, 60, 90, 180, 360]
CALC_LOOKBACK_ROWS = 400
EPSILON_QUANTILE = 0.4
PROBE_REPORT_QUANTILES = [0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9]
MIN_SLOPE_SAMPLES = 30
CONSISTENCY_MIN_COUNT = 3
WINDOW_WEIGHTS = {5: 7, 10: 6, 30: 5, 60: 4, 90: 3, 180: 2, 360: 1}
DIVERGENCE_PENALTY = 0.5
ENABLE_SELF_QUANTILE = True

import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'general-data'))
from swfetch import FETCH_SLEEP
