# -*- coding: utf-8 -*-
"""推荐过滤组合回测择优 (analysis-hub)。

对 recommend_config.FILTERS 里的 4 个过滤旋钮做网格搜索，在历史交易日上
逐日生成推荐并计算未来 HORIZONS 个持有期的收益，按"稳中求进"口径选出
最优过滤组合，写入旁路文件 recommend_best.json（recommend.py 优先读取）。

用法（供 run.py --backtest 调度，也可手动运行）:
  python backtest.py                     # 网格搜索 + 回测 + 写 recommend_best.json
  python backtest.py --dates-back 120    # 回看 120 个交易日（默认 90）
  python backtest.py --step 2            # 每 2 个交易日取一个基准日（默认 1）
  python backtest.py --min-rec-score 65  # 调优时同时搜索推荐指数门槛
  python backtest.py --report            # 只打印当前 best/默认配置对比，不回测

说明：
- 趋势特征计算较重，每个基准日只算一次并在所有过滤组合间复用。
- 样本内总推荐数低于 BACKTEST_N_MIN 的组合视为样本不足，不参与择优。
- 评分口径（稳中求进）：主指标 20 日平均收益，并列时依次比较
  10 日平均收益、5 日平均收益、胜率、样本数。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from datetime import datetime

import recommend
import recommend_config as C

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
BEST_PATH = os.path.join(HUB_DIR, C.BEST_CONFIG_FILE)

# 网格：每个旋钮的候选取值（on=False 的项忽略其阈值）
GRID = {
    'congestion_high': [
        {'on': False},
        {'on': True, 'max_quantile': 85.0},
        {'on': True, 'max_quantile': 90.0},
    ],
    'self_quantile_high': [
        {'on': False},
        {'on': True, 'max_quantile': 0.90},
        {'on': True, 'max_quantile': 0.95},
        {'on': True, 'max_quantile': 0.98},
    ],
    'short_dir_down': [{'on': False}, {'on': True}],
    'width_low': [
        {'on': False},
        {'on': True, 'min_value20': 30.0},
        {'on': True, 'min_value20': 20.0},
    ],
}

KNOB_ORDER = ['congestion_high', 'self_quantile_high', 'short_dir_down', 'width_low']


def _grid_combos():
    keys = KNOB_ORDER
    for values in itertools.product(*(GRID[k] for k in keys)):
        yield {k: dict(v) for k, v in zip(keys, values)}


def _filters_key(filters):
    return json.dumps(filters, sort_keys=True, ensure_ascii=False)


def _base_dates(conn, t_db, dates_back, step):
    """回测基准日列表（升序，按 step 抽样）。"""
    dates = t_db.get_recent_trade_dates(conn, dates_back)
    return dates[::max(1, step)]


def _forward_returns(conn, code, as_of_date, horizons):
    """以 as_of_date 收盘价为基准，未来第 h 个交易日的收益率（小数）。缺数据返回 None。"""
    rows = conn.execute(
        'SELECT trade_date, close FROM price '
        'WHERE sw_code = ? AND trade_date >= ? ORDER BY trade_date',
        (code, as_of_date)).fetchall()
    if len(rows) < 2:
        return {h: None for h in horizons}
    base_close = rows[0]['close']
    if not base_close:
        return {h: None for h in horizons}
    out = {}
    for h in horizons:
        if h < len(rows):
            out[h] = rows[h]['close'] / base_close - 1.0
        else:
            out[h] = None
    return out


def _combo_stats(conn, dates, combos, min_rec_score):
    """逐基准日运行所有组合的推荐并累计前瞻收益。

    返回 {combo_key: {'n_picks', 'n_dates_with_picks', 'ret': {h: [r...]}}}。
    """
    t_analyze, t_db, t_config = recommend._import_trend()
    stats = {k: {'n_picks': 0, 'n_dates': 0, 'ret': {h: [] for h in C.HORIZONS}}
             for k in map(_filters_key, combos)}

    for di, d in enumerate(dates):
        trend_feats = recommend.build_trend_features(d, conn=conn)
        daily_feats = recommend.build_daily_features(d)
        # 前瞻收益只依赖基准日与代码，逐组合 picks 去重计算
        fwd_cache = {}
        for combo in combos:
            key = _filters_key(combo)
            res = recommend.recommend(
                as_of_date=d, tier=C.TIER_SHORT, filters=combo,
                trend_feats=trend_feats, daily_feats=daily_feats,
                min_rec_score=min_rec_score)
            picks = res.get('picks') or []
            if picks:
                st = stats[key]
                st['n_dates'] += 1
            for p in picks:
                code = p['code']
                if code not in fwd_cache:
                    fwd_cache[code] = _forward_returns(conn, code, d, C.HORIZONS)
                fr = fwd_cache[code]
                st = stats[key]
                st['n_picks'] += 1
                for h in C.HORIZONS:
                    if fr.get(h) is not None:
                        st['ret'][h].append(fr[h])
        print('[backtest] 基准日 {}/{} {} 完成'.format(di + 1, len(dates), d))
        sys.stdout.flush()
    return stats


def _score_combo(st):
    """稳中求进评分元组（越大越好）：20日收益 → 10日 → 5日 → 胜率 → 样本。"""
    hs = sorted(C.HORIZONS, reverse=True)
    parts = []
    for h in hs:
        rs = st['ret'].get(h) or []
        parts.append(sum(rs) / len(rs) if rs else -1.0)
    all_rs = [r for h in hs for r in (st['ret'].get(h) or [])]
    hit = sum(1 for r in all_rs if r > 0) / len(all_rs) if all_rs else 0.0
    parts.append(hit)
    parts.append(st['n_picks'])
    return tuple(parts)


def _print_ranking(stats, combos, top=8):
    rows = []
    for combo in combos:
        key = _filters_key(combo)
        st = stats[key]
        sc = _score_combo(st)
        rows.append((sc, combo, st))
    rows.sort(key=lambda x: x[0], reverse=True)
    hs = sorted(C.HORIZONS, reverse=True)
    print('\n{:>4} {:<64} {:>7} {:>7} {:>7} {:>7} {:>7} {:>6}'.format(
        '名次', '过滤组合', 'n_picks', 'n_dates', *['{}日均'.format(h) for h in hs], '胜率'))
    for i, (sc, combo, st) in enumerate(rows[:top]):
        desc = _describe(combo)
        means = []
        for h in hs:
            rs = st['ret'].get(h) or []
            means.append('{:.2%}'.format(sum(rs) / len(rs)) if rs else 'N/A')
        all_rs = [r for h in hs for r in (st['ret'].get(h) or [])]
        hit = '{:.1%}'.format(sum(1 for r in all_rs if r > 0) / len(all_rs)) if all_rs else 'N/A'
        print('{:>4} {:<64} {:>7} {:>7} {:>7} {:>7} {:>7} {:>6}'.format(
            i + 1, desc, st['n_picks'], st['n_dates'], *means, hit))
    return rows


def _describe(combo):
    parts = []
    for k in KNOB_ORDER:
        v = combo[k]
        if not v.get('on'):
            continue
        detail = {kk: vv for kk, vv in v.items() if kk != 'on'}
        parts.append(k if not detail else '{}({})'.format(k, detail))
    return '+'.join(parts) if parts else '(无过滤)'


def cmd_report():
    best_filters, best_meta = recommend._load_best_filters()
    print('当前生效过滤组合 ({}):'.format(
        'recommend_best.json' if best_meta else 'FILTERS_DEFAULT'))
    print(json.dumps(best_filters, ensure_ascii=False, indent=2))
    if best_meta:
        print('\n元信息:')
        print(json.dumps({k: v for k, v in best_meta.items() if k != 'filters'},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_tune(args):
    t_analyze, t_db, t_config = recommend._import_trend()
    conn = t_db.connect()
    try:
        dates = _base_dates(conn, t_db, args.dates_back, args.step)
        if len(dates) < 5:
            print('[backtest] [ERROR] 可用基准日不足（{} 个），请先刷新/落库数据'.format(len(dates)))
            return 1
        print('[backtest] 基准日 {} → {}（共 {} 个），持有期 {}，组合网格 {} 个'.format(
            dates[0], dates[-1], len(dates), C.HORIZONS, len(list(_grid_combos()))))

        combos = list(_grid_combos())
        min_candidates = [C.MIN_REC_SCORE]
        if args.min_rec_score is not None:
            min_candidates = [args.min_rec_score]

        results = {}
        for min_rs in min_candidates:
            print('\n[backtest] === min_rec_score = {} ==='.format(min_rs))
            stats = _combo_stats(conn, dates, combos, min_rs)
            rows = _print_ranking(stats, combos)
            qualified = [(sc, combo, st) for sc, combo, st in rows
                         if st['n_picks'] >= C.BACKTEST_N_MIN]
            if not qualified:
                print('[backtest] [WARN] 无组合达到样本下限 {}，放宽为全部排序'.format(C.BACKTEST_N_MIN))
                qualified = rows
            results[min_rs] = (qualified[0][0], qualified[0][1], qualified[0][2], stats)

        # 取主评分最高（若只搜了一个 min_rs 即为它）
        best_min_rs = max(results, key=lambda k: results[k][0])
        best_score, best_combo, best_st, _ = results[best_min_rs]

        print('\n[backtest] 最优组合: {}'.format(_describe(best_combo)))
        print('[backtest] min_rec_score={} 样本 {} 个推荐'.format(best_min_rs, best_st['n_picks']))

        payload = {
            'filters': best_combo,
            'tuned_at': datetime.now().isoformat(timespec='seconds'),
            'method': 'grid-search backtest (稳中求进: 20日→10日→5日→胜率→样本)',
            'dates_back': args.dates_back,
            'step': args.step,
            'n_base_dates': len(dates),
            'n_picks': best_st['n_picks'],
            'horizons': C.HORIZONS,
            'min_rec_score': best_min_rs,
            'score_weights': dict(C.SCORE_WEIGHTS),
            'backtest_n_min': C.BACKTEST_N_MIN,
        }
        if args.dry_run:
            print('[backtest] --dry-run: 不写入 {}'.format(BEST_PATH))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            with open(BEST_PATH, 'w', encoding='utf-8') as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            print('[backtest] 已写入 {}'.format(BEST_PATH))
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='推荐过滤组合回测择优')
    parser.add_argument('--dates-back', type=int, default=90,
                        help='回看基准日数量（默认 90 个交易日）')
    parser.add_argument('--step', type=int, default=1,
                        help='基准日抽样步长（默认 1，即每个交易日）')
    parser.add_argument('--min-rec-score', type=float, default=None,
                        help='同时用指定推荐指数门槛调优（默认只测配置值）')
    parser.add_argument('--report', action='store_true',
                        help='只打印当前生效配置，不回测')
    parser.add_argument('--dry-run', action='store_true',
                        help='回测但不写 recommend_best.json')
    args = parser.parse_args()

    if args.report:
        return cmd_report()
    return cmd_tune(args)


if __name__ == '__main__':
    sys.exit(main())
