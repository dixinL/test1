"""推荐引擎 (analysis-hub)：把两工具的一级数据合成"下一交易日"标的推荐。

产出一个 0~100 的推荐指数 rec_score(二级数据)与可解释的分项推导链，
按"稳中求进"（确定性为主、收益弹性为辅）打分，叠加可配置硬性过滤，
在"精而少"约束下给出重点推荐标的(允许空推荐)。

特征来源(全部支持按 as_of_date 截断，无前视泄漏)：
  趋势特征(必选) -> 直接复用 sw2-trend-analysis 的 analyze/db，用 <= D 的价格窗口
                    现算 build_summary，取 score/self_quantile/up_count/consistency/
                    dir_5/dir_10/slope_norm_mean。任意历史日可算 -> 支持长回测。
  近端特征(短档) -> sw2-daily-report 的 db.build_width_dict / build_congestion_dict，
                    取该日 value20 / 拥挤度分位。仅近端(约 2026-04-29 起)可用。

用法：
  python recommend.py                 # 取最新交易日、短档、默认(或 best)过滤
  python recommend.py --date 2026-09-01 --tier long
  python recommend.py --tier short --json   # 打印 JSON
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import recommend_config as C

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(HUB_DIR)
TREND_DIR = os.path.join(REPO_DIR, 'sw2-trend-analysis')
DAILY_DIR = os.path.join(REPO_DIR, 'sw2-daily-report')
REPORT_DIR = os.path.join(HUB_DIR, 'report')

_MOD_CACHE = {}


def _load_module(alias, directory, filename):
    """以唯一别名从指定目录加载模块(隔离同名 db/config 冲突)。"""
    if alias in _MOD_CACHE:
        return _MOD_CACHE[alias]
    path = os.path.join(directory, filename)
    added = directory not in sys.path
    if added:
        sys.path.insert(0, directory)
    try:
        spec = importlib.util.spec_from_file_location(alias, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod
        spec.loader.exec_module(mod)
    finally:
        if added:
            try:
                sys.path.remove(directory)
            except ValueError:
                pass
    _MOD_CACHE[alias] = mod
    return mod


def _import_trend():
    """加载趋势项目的 analyze / db / config(唯一别名，避免与日报 db 冲突)。"""


    added = TREND_DIR not in sys.path
    if added:
        sys.path.insert(0, TREND_DIR)
    try:
        import config as t_config
        import db as t_db
        import analyze as t_analyze
    finally:
        if added:
            try:
                sys.path.remove(TREND_DIR)
            except ValueError:
                pass
    return t_analyze, t_db, t_config


def _import_daily_db():
    """以唯一别名加载日报项目的 db(其 build_width_dict/build_congestion_dict)。"""
    return _load_module('daily_db', DAILY_DIR, 'db.py')


def build_trend_features(as_of_date, conn=None):
    """用 <= as_of_date 的价格窗口现算每板块趋势二级特征。

    返回 {code6: {sw_name, score, self_quantile, up_count, down_count,
                  consistency, dir_5, dir_10, slope_norm_mean, pvm_w}}。
    pvm_w 为贴线度特征：短窗口(5/10/30) price_vs_ma 按权重加权乖离率。
    as_of_date=None 时取库内最新交易日的视图。
    最新数据距基准日超过 STALE_MAX_TRADING_DAYS 个交易日未更新的板块剔除
    （防已撤销指数凭冻结行情参与打分）。
    """
    t_analyze, t_db, t_config = _import_trend()
    own = conn is None
    if own:
        conn = t_db.connect()
    try:
        epsilon_map = t_db.get_epsilon_map(conn)
        created_at = t_analyze.now_iso()

        codes = [r['sw_code'] for r in conn.execute(
            'SELECT DISTINCT sw_code FROM price ORDER BY sw_code')]

        cal = [r['trade_date'] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM price ORDER BY trade_date')]

        if as_of_date:
            cal = [d for d in cal if d <= as_of_date]
        cal_idx = {d: i for i, d in enumerate(cal)}
        base = cal[-1] if cal else None

        all_rows = []
        closes_map = {}
        stale_skipped = []
        cdate = as_of_date
        for code in codes:
            rows = t_db.get_recent_prices(
                conn, code, t_config.CALC_LOOKBACK_ROWS, as_of_date=as_of_date)
            if not rows:
                continue
            latest = rows[-1]['trade_date']
            if base is not None:
                li = cal_idx.get(latest)
                if li is None or cal_idx[base] - li > C.STALE_MAX_TRADING_DAYS:
                    stale_skipped.append((code, latest))
                    continue
            closes = [r['close'] for r in rows]
            closes_map[code] = closes
            name = rows[0]['sw_name']
            all_rows.extend(t_analyze.analyze_one(
                code, name, closes, epsilon_map, cdate or '', created_at))

        if stale_skipped:
            print('[trend-feats] 剔除停更板块 {} 个（基准日 {} 前超 {} 个交易日无数据）: {}'.format(
                len(stale_skipped), base, C.STALE_MAX_TRADING_DAYS,
                ', '.join('{}({})'.format(c, d) for c, d in stale_skipped)))

        summary = t_analyze.build_summary(all_rows, closes_map=closes_map)

        mw = C.NORM['ma_windows']
        pvm_pairs = {}
        for r in all_rows:
            w = r.get('window')
            if w in mw and r.get('price_vs_ma') is not None:
                pvm_pairs.setdefault(r['sw_code'], []).append(
                    (mw[w], r['price_vs_ma']))
        pvm_w = {}
        for code, pairs in pvm_pairs.items():
            wsum = sum(w for w, _ in pairs)
            pvm_w[code] = sum(w * v for w, v in pairs) / wsum if wsum else None

        feats = {}
        for s in summary:
            feats[s['sw_code']] = {
                'sw_name': s['sw_name'],
                'score': s.get('score', 0.0),
                'self_quantile': s.get('self_quantile'),
                'up_count': s.get('up_count', 0),
                'down_count': s.get('down_count', 0),
                'consistency': s.get('consistency', ''),
                'dir_5': s.get('dir_5', 'N/A'),
                'dir_10': s.get('dir_10', 'N/A'),
                'slope_norm_mean': s.get('slope_norm_mean', 0.0),
                'pvm_w': pvm_w.get(s['sw_code']),
            }
        return feats
    finally:
        if own:
            conn.close()


def build_daily_features(as_of_date):
    """近端 width/congestion 特征。返回 {code6: {value20, turnover_q, amount_q}}。

    该日无数据(超出近端覆盖)时返回空 dict，短档过滤/打分按缺失处理。
    """
    d_db = _import_daily_db()
    out = {}
    try:
        wd = d_db.build_width_dict(as_of_date=as_of_date)
        w_dates = wd.get('dates', [])
        if w_dates:
            last = w_dates[-1]
            idx = len(w_dates) - 1
            for item in wd.get('swCodeNames', []):
                full = item['indexCode']
                code6 = full.replace('.SI', '')
                seq = wd.get('maMarketWidth', {}).get(full, [])
                if idx < len(seq):
                    out.setdefault(code6, {})['value20'] = seq[idx].get('value20')
                out.setdefault(code6, {})['_w_date'] = last
    except Exception:
        pass
    try:
        cg = d_db.build_congestion_dict(as_of_date=as_of_date)
        c_dates = cg.get('dates', [])
        if c_dates:
            idx = len(c_dates) - 1
            for item in cg.get('swCodeNames', []):
                full = item['indexCode']
                code6 = full.replace('.SI', '')
                seq = cg.get('congestions', {}).get(full, [])
                if idx < len(seq):
                    out.setdefault(code6, {})['turnover_q'] = \
                        seq[idx].get('turnoverRateFQuantile')
                    out.setdefault(code6, {})['amount_q'] = \
                        seq[idx].get('amountCongestionQuantile')
    except Exception:
        pass
    return out


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _score_cons(f):
    """方向一致性：up_count/7 基底 + 一致性标签加分，方向打架封顶。"""
    base = f.get('up_count', 0) / 7.0 * 100.0

    consi = f.get('consistency', '') or ''
    if '连续上行' in consi:
        base += C.NORM['cons_consistent_bonus']
    elif '一致偏上行' in consi:
        base += C.NORM['cons_intermittent_bonus']
    if '方向打架' in consi:
        base = min(base, C.NORM['cons_fight_cap'])

    v = _clip(base)
    raw = 'up_count={}/7, 一致性={}'.format(f.get('up_count', 0), consi or 'N/A')
    return v, raw


def _score_str(f, score_pctile):
    """趋势强度稳健：综合强度分在当日横截面(score>0)里的百分位。"""
    v = _clip(score_pctile * 100.0)
    raw = '综合强度分={:+.5g}, 横截面分位={:.0%}'.format(
        f.get('score', 0.0), score_pctile)
    return v, raw


def _score_mom(f):
    """短周期动能：5/10 天方向组合。"""
    d5, d10 = f.get('dir_5', 'N/A'), f.get('dir_10', 'N/A')

    if 'DOWN' in (d5, d10):
        v = C.NORM['mom_any_down']
    elif d5 == 'UP' and d10 == 'UP':
        v = C.NORM['mom_both_up']
    elif d5 == 'UP' or d10 == 'UP':
        v = C.NORM['mom_one_up']
    else:
        v = C.NORM['mom_flat']

    return float(v), 'dir_5={}, dir_10={}'.format(d5, d10)


def _score_pos(f):
    """不过高：自身历史分位在 [lo,hi] 线性递减，<lo 满分，缺失中性。"""
    sq = f.get('self_quantile')
    if sq is None:
        return 60.0, '自身分位=N/A(中性)'

    lo, hi = C.NORM['pos_lo'], C.NORM['pos_hi']

    if sq <= lo:
        v = 100.0
    elif sq >= hi:
        v = 0.0
    else:
        v = (hi - sq) / (hi - lo) * 100.0

    return _clip(v), '自身分位={:.0%}'.format(sq)


def _score_ma(f):
    """贴线度：短窗口加权乖离率 |pvm_w| 越小越好（贴线=确定性高）；
    大幅高于均线=追高风险，大幅低于=趋势弱。缺失中性。"""
    pvm = f.get('pvm_w')
    if pvm is None:
        return float(C.NORM['ma_neutral']), '乖离率=N/A(中性)'

    lo, hi = C.NORM['ma_lo'], C.NORM['ma_hi']
    a = abs(pvm)

    if a <= lo:
        v = 100.0
    elif a >= hi:
        v = 0.0
    else:
        v = (hi - a) / (hi - lo) * 100.0

    return _clip(v), '乖离率={:+.2%}'.format(pvm)


def _score_heat(f):
    """不过热：100 - max(换手分位, 成交额分位)；缺失中性。"""
    t, a = f.get('turnover_q'), f.get('amount_q')

    vals = [x for x in (t, a) if x is not None]
    if not vals:
        return float(C.NORM['heat_neutral']), '拥挤度=N/A(中性)'

    mx = max(vals)
    return _clip(100.0 - mx), '拥挤度max={:.0f}'.format(mx)


def _score_wid(f):
    """宽度健康：value20 直接作分；缺失中性。"""
    v20 = f.get('value20')
    if v20 is None:
        return float(C.NORM['wid_neutral']), '宽度=N/A(中性)'

    return _clip(float(v20)), 'value20={:.0f}'.format(v20)


def score_one(f, score_pctile, tier, weights=None):
    """合成 rec_score(0~100) + 各分项推导链。

    七项打分；长档缺 HEAT/WID(近端专有)，自动跳过并重新归一化权重。
    weights=None 时用配置默认权重；否则用传入的自定义权重(tune_knobs 调优用)。
    """
    w = weights if weights else C.SCORE_WEIGHTS

    parts = {}
    parts['CONS'] = _score_cons(f)
    parts['STR'] = _score_str(f, score_pctile)
    parts['MOM'] = _score_mom(f)
    parts['POS'] = _score_pos(f)
    parts['MA'] = _score_ma(f)
    if tier == C.TIER_SHORT:
        parts['HEAT'] = _score_heat(f)
        parts['WID'] = _score_wid(f)

    num = 0.0
    wsum = 0.0
    for key, (val, raw) in parts.items():
        wsum += w.get(key, 0)

    for key, (val, raw) in parts.items():
        num += w.get(key, 0) * val

    rec = num / wsum if wsum else 0.0

    breakdown = []
    for key, (val, raw) in parts.items():
        wt = w.get(key, 0)
        contrib = wt * val / wsum if wsum else 0.0
        breakdown.append({
            '项': key,
            '权重': wt,
            '原始': raw,
            '归一分': round(val, 1),
            '贡献': round(contrib, 1),
        })

    return round(rec, 1), breakdown


def apply_filters(f, filters):
    """返回 (passed: bool, reasons: [被否决原因])。缺失字段的过滤自动跳过。"""
    reasons = []

    fc = filters.get('congestion_high', {})
    if fc.get('on'):
        vals = [x for x in (f.get('turnover_q'), f.get('amount_q')) if x is not None]
        if vals and max(vals) >= fc['max_quantile']:
            reasons.append('拥挤度过高(max={:.0f}>={:.0f})'.format(
                max(vals), fc['max_quantile']))

    fq = filters.get('self_quantile_high', {})
    if fq.get('on'):
        sq = f.get('self_quantile')
        if sq is not None and sq >= fq['max_quantile']:
            reasons.append('自身分位过高({:.0%}>={:.0%})'.format(sq, fq['max_quantile']))

    fd = filters.get('short_dir_down', {})
    if fd.get('on'):
        if f.get('dir_5') == 'DOWN' or f.get('dir_10') == 'DOWN':
            reasons.append('短期方向向下(dir_5={}, dir_10={})'.format(
                f.get('dir_5'), f.get('dir_10')))

    fw = filters.get('width_low', {})
    if fw.get('on'):
        v20 = f.get('value20')
        if v20 is not None and v20 < fw['min_value20']:
            reasons.append('宽度过低(value20={:.0f}<{:.0f})'.format(v20, fw['min_value20']))

    return len(reasons) == 0, reasons


def _load_best_filters():
    """优先读 recommend_best.json 的过滤组合；缺失则用配置默认。"""
    path = os.path.join(HUB_DIR, C.BEST_CONFIG_FILE)
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as fp:
                obj = json.load(fp)
            if isinstance(obj, dict) and 'filters' in obj:
                return obj['filters'], obj
        except Exception:
            pass
    return C.FILTERS_DEFAULT, None


def recommend(as_of_date=None, tier=C.TIER_SHORT, filters=None,
              trend_feats=None, daily_feats=None,
              min_rec_score=None, score_weights=None):
    """生成某日推荐。返回结构化 dict(含 Top-K、每标的推导链、过滤统计)。

    trend_feats/daily_feats 可外部传入(回测复用，避免重复算)；否则内部装配。
    filters=None 时用 best/默认过滤组合。
    min_rec_score/score_weights=None 时用配置默认(tune_knobs 调优时传自定义值)。
    """
    best_meta = None
    if filters is None:
        filters, best_meta = _load_best_filters()

    min_rs = min_rec_score if min_rec_score is not None else C.MIN_REC_SCORE
    if min_rec_score is None and best_meta and 'min_rec_score' in best_meta:
        min_rs = best_meta['min_rec_score']
    sw = score_weights
    if sw is None and best_meta and 'score_weights' in best_meta:
        sw = best_meta['score_weights']
    if sw is None:
        sw = dict(C.SCORE_WEIGHTS)

    if trend_feats is None:
        trend_feats = build_trend_features(as_of_date)
    if daily_feats is None:
        daily_feats = build_daily_features(as_of_date) if tier == C.TIER_SHORT else {}

    feats = {}
    for code, tf in trend_feats.items():
        merged = dict(tf)
        merged.update(daily_feats.get(code, {}))
        feats[code] = merged

    cand = {c: f for c, f in feats.items()
            if not C.ONLY_UP_BOARD or f.get('score', 0.0) > 0}

    pos_scores = sorted([f['score'] for f in cand.values() if f.get('score', 0) > 0])

    def _pctile(x):
        if not pos_scores:
            return 0.0
        below = sum(1 for v in pos_scores if v < x)
        equal = sum(1 for v in pos_scores if v == x)
        return (below + 0.5 * equal) / len(pos_scores)

    scored = []
    filtered_out = []
    for code, f in cand.items():
        passed, reasons = apply_filters(f, filters)
        rec, breakdown = score_one(f, _pctile(f.get('score', 0.0)), tier,
                                   weights=sw)
        item = {
            'code': code,
            'name': f.get('sw_name', ''),
            'rec_score': rec,
            'breakdown': breakdown,
            'score': f.get('score', 0.0),
            'self_quantile': f.get('self_quantile'),
            'consistency': f.get('consistency', ''),
            'dir_5': f.get('dir_5'),
            'dir_10': f.get('dir_10'),
            'value20': f.get('value20'),
            'turnover_q': f.get('turnover_q'),
            'amount_q': f.get('amount_q'),
        }
        if not passed:
            item['rejected'] = reasons
            filtered_out.append(item)
            continue
        if rec < min_rs:
            item['rejected'] = ['推荐指数不足({:.1f}<{:.1f})'.format(rec, min_rs)]
            filtered_out.append(item)
            continue
        scored.append(item)

    scored.sort(key=lambda x: -x['rec_score'])
    picks = scored[:C.TOP_K] if len(scored) >= C.MIN_CANDIDATES else []

    return {
        'as_of_date': as_of_date,
        'tier': tier,
        'filters': filters,
        'best_meta': best_meta,
        'n_universe': len(feats),
        'n_candidate_up': len(cand),
        'n_passed': len(scored),
        'n_rejected': len(filtered_out),
        'picks': picks,
        'empty_reason': None if picks else '通过过滤且达标者不足，宁缺毋滥不推荐',
        'runners_up': scored[C.TOP_K:C.TOP_K + 5],
        'min_rec_score': min_rs,
        'score_weights': sw,
        'rejected_items': sorted(filtered_out, key=lambda x: -x['rec_score']),
        'up_feats': {c: {'sw_name': f.get('sw_name', ''),
                         'score': f.get('score', 0.0),
                         'pvm_w': f.get('pvm_w'),
                         'dir_5': f.get('dir_5'),
                         'self_quantile': f.get('self_quantile')}
                     for c, f in cand.items()},
    }


def _parse_args(argv):
    date = None
    tier = C.TIER_SHORT
    as_json = False

    for i, a in enumerate(argv):
        if a == '--date' and i + 1 < len(argv):
            date = argv[i + 1].strip()
        elif a.startswith('--date='):
            date = a.split('=', 1)[1].strip()
        elif a == '--tier' and i + 1 < len(argv):
            tier = argv[i + 1].strip()
        elif a.startswith('--tier='):
            tier = a.split('=', 1)[1].strip()
        elif a == '--json':
            as_json = True

    return date, tier, as_json


def main():
    date, tier, as_json = _parse_args(sys.argv[1:])
    res = recommend(as_of_date=date, tier=tier)

    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print('推荐日={} 档位={} | 池={} UP候选={} 通过={} 淘汰={}'.format(
        res['as_of_date'] or '最新', res['tier'], res['n_universe'],
        res['n_candidate_up'], res['n_passed'], res['n_rejected']))

    if not res['picks']:
        print('→ 今日不推荐（{}）'.format(res['empty_reason']))
        return 0

    print('重点推荐 {} 个：'.format(len(res['picks'])))
    for p in res['picks']:
        print('  {} {}  推荐指数={:.1f} | 强度={:+.5g} | 一致性={} | dir5/10={}/{}'.format(
            p['code'], p['name'], p['rec_score'], p['score'],
            p['consistency'], p['dir_5'], p['dir_10']))

    os.makedirs(REPORT_DIR, exist_ok=True)
    tag = date or 'latest'
    out = os.path.join(REPORT_DIR, 'recommendation_{}.json'.format(tag))
    with open(out, 'w', encoding='utf-8') as fp:
        json.dump(res, fp, ensure_ascii=False, indent=2)
    print('已存: {}'.format(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
