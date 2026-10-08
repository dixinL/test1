"""推荐引擎配置 (analysis-hub)。

集中所有可调旋钮：打分权重、分项归一化阈值、硬性过滤开关与阈值、
选股数量、持有期。目标是"稳中求进"——确定性/稳定项权重 > 收益弹性项。

设计说明：
- recommend.py 打分与过滤、backtest.py 回测都读这里的默认值。
- 回测择优后的最优过滤组合写到旁路文件 recommend_best.json；
  recommend.py 优先读它，缺失时回退本文件的 FILTERS_DEFAULT。
- 独立自包含，不依赖其它子项目的 config。
"""

SCORE_WEIGHTS = {'CONS': 40, 'STR': 20, 'MOM': 5, 'HEAT': 20, 'POS': 0, 'WID': 0, 'MA': 5}

NORM = {
    'cons_consistent_bonus': 12,
    'cons_intermittent_bonus': 6,
    'cons_fight_cap': 45,
    'mom_both_up': 100,
    'mom_one_up': 65,
    'mom_flat': 45,
    'mom_any_down': 15,
    'pos_lo': 0.5,
    'pos_hi': 0.95,
    'heat_neutral': 60,
    'wid_neutral': 60,
    'ma_windows': {5: 7, 10: 6, 30: 5},
    'ma_lo': 0.01,
    'ma_hi': 0.1,
    'ma_neutral': 60,
}

FILTERS_DEFAULT = {
    'congestion_high': {'on': False, 'max_quantile': 85.0},
    'self_quantile_high': {'on': True, 'max_quantile': 0.95},
    'short_dir_down': {'on': False},
    'width_low': {'on': False, 'min_value20': 30.0},
}

ONLY_UP_BOARD = True
MIN_REC_SCORE = 70.0
TOP_K = 5
STALE_MAX_TRADING_DAYS = 10
MIN_CANDIDATES = 1
HORIZONS = [5, 10, 20]
BACKTEST_N_MIN = 150
TIER_LONG = 'long'
TIER_SHORT = 'short'
BEST_CONFIG_FILE = 'recommend_best.json'
