"""
整合报告生成器 (analysis-hub)。

在 run.py 触发两个基础工具后调用：读取两者的最新产物，整合成一份
面向"下一交易日"的分析与建议报告，写到 analysis-hub/report/combined_{date}.md。

数据来源:
  日报  -> sw2-daily-report/report/action_report_*.md   (市场总体状态)
  趋势  -> sw2-trend-analysis/data/exports/sw_trend_summary_*.csv (UP/DOWN 榜单)

设计原则: 无论基础工具本次成败，都尽量用现有最新产物生成报告；
缺失或数据过期时在报告中显式说明，不静默失败。
"""

import os
import re
import csv
import glob
import sys
from datetime import date, datetime

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(HUB_DIR)

DAILY_REPORT_DIR = os.path.join(REPO_DIR, 'sw2-daily-report', 'report')
TREND_EXPORTS_DIR = os.path.join(REPO_DIR, 'sw2-trend-analysis', 'data', 'exports')
COMBINED_DIR = os.path.join(HUB_DIR, 'report')

TREND_SUMMARY_PREFIX = 'sw_trend_summary_'
DAILY_REPORT_PREFIX = 'action_report_'

TOP_N_UP = 30
TOP_N_DOWN = 20


def _latest_dated_file(directory, prefix, suffix):
    """返回目录下 {prefix}YYYY-MM-DD{suffix} 中日期最新的 (path, date_str)。"""
    if not os.path.isdir(directory):
        return None, None
    pattern = os.path.join(directory, '{}*{}'.format(prefix, suffix))
    best_path, best_date = None, None
    for p in glob.glob(pattern):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
        if m:
            d = m.group(1)
            if best_date is None or d > best_date:
                best_date, best_path = d, p
    return best_path, best_date


def _dated_file_asof(directory, prefix, suffix, as_of_date):
    """返回日期 <= as_of_date 中最新的 (path, date_str)，模拟"当日可见的最新产物"。

    as_of_date=None 时等价于 _latest_dated_file(取全局最新)。
    """
    if as_of_date is None:
        return _latest_dated_file(directory, prefix, suffix)
    if not os.path.isdir(directory):
        return None, None
    pattern = os.path.join(directory, '{}*{}'.format(prefix, suffix))
    best_path, best_date = None, None
    for p in glob.glob(pattern):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
        if m:
            d = m.group(1)
            if d > as_of_date:
                continue
            if best_date is None or d > best_date:
                best_date, best_path = d, p
    return best_path, best_date


def _staleness_note(data_date, today_str):
    """根据数据日期与今天的关系，返回一句话说明。"""
    if data_date is None:
        return '（无可用数据）'
    if data_date == today_str:
        return '（当期数据）'
    return '（注意：非当期数据，最新数据日期为 {}，今日为 {}）'.format(data_date, today_str)


def _parse_state_block(block):
    """解析宽度/拥挤度小节：状态、趋势、高/中/低分布。"""
    out = {}
    m = re.search(r'状态:\s*\*\*(.+?)\*\*', block)
    if m:
        out['state'] = m.group(1).strip()
    m = re.search(r'趋势:\s*(\S+(?:\s*\([+-]?[\d.]+/天\))?)', block)
    if m:
        out['trend'] = m.group(1).strip()
    m = re.search(r'高\(>=80\):\s*(\d+)\s*个\s*\|\s*中\(50~80\):\s*(\d+)\s*个\s*\|\s*低\(<50\):\s*(\d+)', block)
    if m:
        out.update(hi=int(m.group(1)), mid=int(m.group(2)), lo=int(m.group(3)))
    return out


def _parse_scene_matrix(text):
    """解析日报"二、九场景矩阵"表：{code: {name, total, trend, stable}}。缺失返回 {}。"""
    m = re.search(r'##\s*二、九场景矩阵(.*?)(?=\n##\s*三、|\Z)', text, re.S)
    if not m:
        return {}
    scenes = {}
    for mm in re.finditer(r'^\|\s*(S\d)(?::\s*([^|]+?))?\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)', m.group(1), flags=re.M):
        code, name, total, trend_n, stable_n = mm.groups()
        scenes[code] = {'code': code, 'name': (name or '').strip(), 'total': int(total),
                        'trend': int(trend_n), 'stable': int(stable_n)}
    return scenes


def extract_daily_state(report_path):
    """从日报 Markdown 中抽取"一、市场总体状态"整段及结构化字段。找不到返回 None。"""
    if not report_path or not os.path.isfile(report_path):
        return None
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        return None

    m = re.search(r'##\s*一、市场总体状态(.*?)(?=\n##\s*二、|\Z)', text, re.S)
    if not m:
        return None
    section = m.group(1).strip()

    verdict = ''
    vm = re.search(r'当前市场判断.*?\n\s*[-*]?\s*(.+)', section)
    if vm:
        verdict = vm.group(1).strip().replace('**', '')

    state = {'section': section, 'verdict': verdict}

    for b in re.split(r'^###\s*', section, flags=re.M):
        if b.startswith('市场宽度'):
            state['width'] = _parse_state_block(b)
        elif b.startswith('市场拥挤度'):
            state['congestion'] = _parse_state_block(b)

    state['scenario'] = verdict.split('=')[-1].strip() if '=' in verdict else verdict
    state['scenes'] = _parse_scene_matrix(text)
    return state


def _parse_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


_SCORE_GLOSS = {
    'CONS': '一致性',
    'STR': '强度分位',
    'MOM': '短动能',
    'HEAT': '不过热',
    'POS': '自身分位',
    'WID': '宽度',
    'MA': '贴线度',
}

_SPARK_CHARS = '▁▂▃▄▅▆▇█'


def _sparkline(vals):
    """收盘价序列 -> 8 档块字符简笔图（首尾归一化到区间）。全平/数据不足有兜底。"""
    vals = [v for v in vals or [] if isinstance(v, (int, float))]
    if len(vals) < 5:
        return ''
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return '→' * len(vals)
    span = hi - lo
    return ''.join(_SPARK_CHARS[min(7, int((v - lo) / span * 7.999))] for v in vals)


def _recent_closes(codes, n=20, as_of_date=None):
    """从 general-data/sw.db 取各板块最近 n 个交易日收盘（升序）。缺失板块跳过。

    仅用于走势展示；as_of_date 回放时截断到基准日。
    """
    import sqlite3
    db = os.path.join(REPO_DIR, 'general-data', 'sw.db')
    if not codes or not os.path.isfile(db):
        return {}
    conn = None
    try:
        conn = sqlite3.connect(db)
        out = {}
        for c in codes:
            if as_of_date:
                rows = conn.execute(
                    'SELECT close FROM price WHERE sw_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?',
                    (c, as_of_date, n)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT close FROM price WHERE sw_code=? ORDER BY trade_date DESC LIMIT ?',
                    (c, n)).fetchall()
            if len(rows) >= 5:
                out[c] = [r[0] for r in reversed(rows)]
        return out
    except Exception:
        return {}
    finally:
        if conn:
            conn.close()


def extract_trend_lists(csv_path):
    """从趋势汇总 CSV 提取按综合强度分排序的 UP / DOWN 榜单。"""
    if not csv_path or not os.path.isfile(csv_path):
        return None
    rows = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    except Exception:
        return None

    if not rows:
        return None

    def score(r):
        v = _parse_float(r.get('综合强度分'))
        return v if v is not None else 0.0

    up = [r for r in rows if (r.get('关注榜') or '').strip() == 'UP榜']
    down = [r for r in rows if (r.get('关注榜') or '').strip() == 'DOWN榜']
    up.sort(key=score, reverse=True)
    down.sort(key=score)
    return {'up': up, 'down': down, 'total': len(rows)}


def _fmt_industry(r, closes_map=None):
    code = (r.get('行业代码') or '').strip()
    name = (r.get('行业名称') or '').strip()
    sc = _parse_float(r.get('综合强度分'))
    sc_s = '{:+.5g}'.format(sc) if sc is not None else 'N/A'
    d10 = (r.get('方向_10天') or '').split('(')[0].strip()
    consi = (r.get('一致性') or '').strip()
    quant = (r.get('自身历史分位') or '').strip()
    qf = _parse_float(quant)
    quant_s = '{:.0%}'.format(qf) if qf is not None else 'N/A'
    tail = ''
    spark = _sparkline((closes_map or {}).get(code))
    if spark:
        tail = ' | 走势 {}'.format(spark)
    return '{} {} | 强度={} | 10日={} | 分位={} | {}{}'.format(
        code, name, sc_s, d10 or 'N/A', quant_s, consi or 'N/A', tail)


def _top_up_commentary(up_rows, closes_map=None):
    """UP 榜补充观察：仅在头部出现强度断层时输出一行（其余字段与榜单行重叠，不重复展示）。"""
    lines = []
    if not up_rows:
        return lines
    svals = [_parse_float(r.get('综合强度分')) or 0 for r in up_rows]
    if len(svals) >= 4:
        diffs = [svals[i] - svals[i + 1] for i in range(len(svals) - 1)]
        avg_d = sum(diffs) / len(diffs) if diffs else 0
        if diffs and max(diffs) > 0 and avg_d > 0:
            i_max = diffs.index(max(diffs))
            if i_max >= 1 and max(diffs) > 2.5 * avg_d:
                lines.append('- 强度断层：第 {} 名({:+.4g})之后骤降至 {:+.4g}，头部梯队与后段分界明显。'.format(
                    i_max + 1, svals[i_max], svals[i_max + 1]))
    return lines


def _safe_recommend(as_of_date):
    """调用推荐引擎(延迟导入)。任何异常返回 None，由调用方降级处理。"""
    try:
        import recommend
    except Exception as e:
        print('[combine] 推荐引擎不可用: {}'.format(e))
        return None
    try:
        return recommend.recommend(as_of_date=as_of_date, tier='short')
    except Exception as e:
        print('[combine] 推荐引擎运行失败: {}'.format(e))
        return None


def build_recommendation_sections(rec_res):
    """拆分产出推荐相关三块，供 generate 按重要性排布：

    picks  -> 重点推荐（过滤组合说明 + 达标标的与推导链 / 空仓说明）
    near   -> 离达标最近（推荐指数门槛差距最小的前 10，无则空串）
    detail -> 筛选明细（候选池统计 + 方法 + 淘汰表）

    rec_res=None(引擎不可用/运行失败)时各块返回占位说明，绝不影响后续章节。
    """
    res = rec_res
    if res is None:
        return ('（推荐引擎不可用或运行失败，略。）', '', '（推荐引擎不可用，无筛选明细。）')

    # ---- 过滤组合元信息（随重点推荐展示，解释打分与过滤口径）----
    meta_lines = []
    meta = res.get('best_meta')
    if meta:
        meta_lines.append('- 过滤组合来自回测择优（{} 档 · 主指标持有 {} 日 · 区间 {}）；打分口径「稳中求进」。'.format(
            meta.get('tier', '?'), meta.get('key_horizon', '?'),
            '~'.join(meta.get('backtest_range') or ['?', '?'])))
    else:
        meta_lines.append('- 过滤组合为配置默认（未启用回测择优旁路）；打分口径「稳中求进」。')
        dis_path = os.path.join(HUB_DIR, 'recommend_best.json.disabled')
        if os.path.isfile(dis_path):
            try:
                import json as _json
                with open(dis_path, 'r', encoding='utf-8') as fp:
                    d = _json.load(fp)
                rng = '~'.join(d.get('backtest_range') or ['?', '?'])
                n1 = (d.get('phase1_best') or {}).get('n')
                meta_lines.append('- 注：存在已停用的择优结果（区间 {}，样本数 {} 低于可信下限 150，停用旁路；其阈值已并入配置默认）。'.format(rng, n1))
            except Exception:
                pass

    # ---- picks：达标标的与推导链（紧凑排版）----
    picks_lines = list(meta_lines)
    picks = res.get('picks') or []
    if not picks:
        picks_lines.append('**下一交易日：不推荐（宁缺毋滥）。** 原因：{}。'.format(res.get('empty_reason') or '无达标标的'))
    else:
        for i, p in enumerate(picks, 1):
            sq = p.get('self_quantile')
            sq_s = '{:.0%}'.format(sq) if isinstance(sq, (int, float)) else 'N/A'
            picks_lines.append('### {}. {} {} — 推荐指数 {:.1f}'.format(i, p['code'], p['name'], p['rec_score']))
            picks_lines.append('- 关键面：综合强度分={:+.5g} | 一致性={} | 5/10日方向={}/{} | 自身分位={}'.format(
                p.get('score', 0.0), p.get('consistency', 'N/A'),
                p.get('dir_5'), p.get('dir_10'), sq_s))
            extra = []
            if p.get('turnover_q') is not None:
                extra.append('换手拥挤度={:.0f}'.format(p['turnover_q']))
            if p.get('amount_q') is not None:
                extra.append('成交额拥挤度={:.0f}'.format(p['amount_q']))
            if p.get('value20') is not None:
                extra.append('市场宽度value20={:.0f}'.format(p['value20']))
            if extra:
                picks_lines.append('- 拥挤/宽度：' + ' | '.join(extra))
            chain = ' ; '.join(
                '{}({},归一{}→贡献{})'.format(b['项'], b['原始'], b['归一分'], b['贡献'])
                for b in p.get('breakdown', []))
            picks_lines.append('- 推导链：{}'.format(chain))

    # ---- near：离达标最近（表格，与淘汰表同风格）----
    near_lines = []
    rej0 = res.get('rejected_items') or []
    score_cut0 = [r for r in rej0
                  if (r.get('rejected') or [''])[0].startswith('推荐指数不足')]
    if score_cut0:
        near_lines.append('| 推荐指数 | 板块 | 差距 | 短板（归一最低 2 项） |')
        near_lines.append('|---|---|---|---|')
        for r in score_cut0[:10]:
            bd = [b for b in (r.get('breakdown') or []) if b.get('权重', 0) > 0]
            weak = sorted(bd, key=lambda b: b.get('归一分', 100))[:2]
            weak_txt = '、'.join(
                '{}({}，归一 {:.0f}/100)'.format(
                    b.get('项'), _SCORE_GLOSS.get(b.get('项'), ''), b.get('归一分', 0))
                for b in weak)
            near_lines.append('| {:.1f} | {} {} | 差 {:.1f} | {} |'.format(
                r['rec_score'], r['code'], r['name'],
                (res.get('min_rec_score') or 0) - r['rec_score'],
                weak_txt or '整体偏弱'))

    # ---- detail：候选池统计 + 方法 + 淘汰表（紧凑排版）----
    detail_lines = []
    detail_lines.append('- 候选池 {} 个板块 · UP 榜候选 {} · 通过过滤且达标 {} · 淘汰 {}。'.format(
        res['n_universe'], res['n_candidate_up'], res['n_passed'], res['n_rejected']))

    fw = res.get('filters') or {}
    on_filters = []
    if (fw.get('self_quantile_high') or {}).get('on'):
        on_filters.append('自身分位<{:.0%}'.format(fw['self_quantile_high']['max_quantile']))
    if (fw.get('congestion_high') or {}).get('on'):
        on_filters.append('拥挤度<{}'.format(fw['congestion_high']['max_quantile']))
    if (fw.get('width_low') or {}).get('on'):
        on_filters.append('宽度≥{}'.format(fw['width_low']['min_value20']))
    if (fw.get('short_dir_down') or {}).get('on'):
        on_filters.append('短期方向非DOWN')

    wtxt = '、'.join(
        '{}{:.0%}'.format(k.lower(), v / 100.0)
        for k, v in sorted((res.get('score_weights') or {}).items(), key=lambda kv: -kv[1])
        if v > 0)
    detail_lines.append('- 方法：UP榜(综合强度分>0) → 硬过滤[{}] → 推荐指数(权重: {})≥{:.0f} 才推荐；宁缺毋滥，空仓也是判断。'.format(
        '、'.join(on_filters) or '无', wtxt or '默认', res.get('min_rec_score') or 0))

    rej = res.get('rejected_items') or []
    if rej:
        score_cut = [r for r in rej
                     if (r.get('rejected') or [''])[0].startswith('推荐指数不足')]
        hard = [r for r in rej if r not in score_cut]
        detail_lines.append('#### 淘汰 {} 个（硬性过滤 {} 个 · 推荐指数门槛 {} 个）'.format(
            len(rej), len(hard), len(score_cut)))
        shown = rej[:20]
        detail_lines.append('| 推荐指数 | 板块 | 强度分 | 一致性 | 5/10日 | 自身分位 | 淘汰原因 |')
        detail_lines.append('|---|---|---|---|---|---|---|')
        for r in shown:
            sq = r.get('self_quantile')
            detail_lines.append('| {:.1f} | {} {} | {:+.4g} | {} | {}/{} | {} | {} |'.format(
                r.get('rec_score', 0), r.get('code'), r.get('name'),
                r.get('score', 0) or 0, r.get('consistency') or '?',
                r.get('dir_5') or '?', r.get('dir_10') or '?',
                '{:.0%}'.format(sq) if isinstance(sq, (int, float)) else '?',
                '；'.join(r.get('rejected') or [''])))
        if len(rej) > len(shown):
            detail_lines.append('（其余 {} 个略。）'.format(len(rej) - len(shown)))

    return ('\n'.join(picks_lines), '\n'.join(near_lines), '\n'.join(detail_lines))


def build_outlook(daily_state, trend, rec_res=None, daily_date=None, trend_date=None):
    """结合日报市场判词 + 九场景矩阵 + 趋势双榜 + UP 榜均线位置，给出下一交易日判断。

    结论先行（信息按重要性排布）：
      ### 当前市场判断 —— 日报市场级判词（仓位基调的源头）
      ### 综合建议     —— 仓位/介入/升降级条件/风险提示
      ### 判断依据     —— 数据基准/市场基调/宽拥结构/九场景结构/多空结构/均线位置

    判断链（各维度各司其职，汇入"综合判断"）：
      仓位基调 <- 市场级四场景（宽度×拥挤度，与日报判词生成逻辑一致）
      结构修正 <- 九场景 S1~S9 分布（修复侧/强势侧/风险侧/冰封侧）
      参与方式 <- UP 榜绝对占比
      介入时点 <- UP 榜加权乖离率
    """
    lines = []
    verdict = daily_state.get('verdict') if daily_state else ''
    scenario = daily_state.get('scenario') if daily_state else ''
    w = daily_state.get('width') if daily_state else None
    g = daily_state.get('congestion') if daily_state else None
    scenes = (daily_state or {}).get('scenes') or {}

    if daily_date or trend_date:
        lines.append('- 数据基准：日报 {} · 趋势榜 {}'.format(
            daily_date or '缺失', trend_date or '缺失'))

    # 市场基调：优先场景判词，无场景时回退日报判词
    if scenario and w and g:
        lines.append('- 市场基调：{}（宽度{}·{}；拥挤度{}·{}）'.format(
            scenario, w.get('state', 'N/A'), w.get('trend', 'N/A'),
            g.get('state', 'N/A'), g.get('trend', 'N/A')))
    elif verdict:
        lines.append('- 市场基调：{}'.format(verdict))
    else:
        lines.append('- 市场基调：日报未提供明确判断，参考下方原始状态。')

    if w and g and 'hi' in w and 'hi' in g:
        lines.append('- 宽拥结构：宽度 高{}/中{}/低{}；拥挤度 高{}/中{}/低{}'.format(
            w['hi'], w['mid'], w['lo'], g['hi'], g['mid'], g['lo']))

    # 九场景结构（修复侧=S4+S8，强势侧=S1~S3，风险侧=S6+S9，冰封=S7）
    sc_total = sum(s.get('total', 0) for s in scenes.values())
    sc_fix = scenes.get('S4', {}).get('total', 0) + scenes.get('S8', {}).get('total', 0)
    sc_fix_trend = scenes.get('S4', {}).get('trend', 0) + scenes.get('S8', {}).get('trend', 0)
    sc_strong = sum(scenes.get(k, {}).get('total', 0) for k in ('S1', 'S2', 'S3'))
    sc_risk = sum(scenes.get(k, {}).get('total', 0) for k in ('S6', 'S9'))
    sc_frozen = scenes.get('S7', {}).get('total', 0)
    if scenes and sc_total:
        top2 = sorted(scenes.values(), key=lambda s: -s.get('total', 0))[:2]
        top2_txt = ' · '.join('{} {} 个'.format(s.get('name') or s.get('code') or '?', s.get('total', 0)) for s in top2)
        lines.append('- 九场景结构：{}；修复侧(S4+S8) {} 个(★趋势 {}) · 强势侧(S1~S3) {} 个 · 风险侧(S6+S9) {} 个 · 冰封 {} 个（共 {}）'.format(
            top2_txt, sc_fix, sc_fix_trend, sc_strong,
            sc_risk, sc_frozen, sc_total))

    # 多空结构
    up_ratio = None
    if trend:
        n_up = len(trend['up'])
        n_down = len(trend['down'])
        up_ratio = n_up / trend['total'] if trend['total'] else 0.0
        lines.append('- 多空结构：UP 榜 {} 个 / DOWN 榜 {} 个（共 {} 个板块，UP 占比 {:.0%}）。'.format(
            n_up, n_down, trend['total'], up_ratio))
    else:
        lines.append('- 多空结构：趋势数据缺失，无法给出板块级多空对比。')

    # 介入时点：UP 榜加权乖离率（pvm_w）中位数
    med = None
    pvm = [f['pvm_w'] for f in ((rec_res or {}).get('up_feats') or {}).values()
           if isinstance(f.get('pvm_w'), (int, float))]
    if pvm:
        ps = sorted(pvm)
        n = len(ps)
        med = ps[n // 2] if n % 2 else (ps[n // 2 - 1] + ps[n // 2]) / 2
        near = sum(1 for p in pvm if abs(p) <= 0.02)
        far = sum(1 for p in pvm if p > 0.05)
        below = sum(1 for p in pvm if p < 0)
        if med > 0.05:
            ma_txt = '整体远离短期均线（中位 {:+.1f}%），追高风险大'
        elif med > 0.02:
            ma_txt = '乖离偏高（中位 {:+.1f}%），不宜追高'
        elif med >= 0:
            ma_txt = '整体贴线（中位 {:+.1f}%），形态健康'
        else:
            ma_txt = '多在短期均线下方（中位 {:+.1f}%），待反转确认'
        lines.append(('- 均线位置：UP 榜' + ma_txt +
                      '。贴线(|乖离|≤2%) {} 个 · 上方远离(>5%) {} 个 · 均线下方 {} 个').format(
            med * 100, near, far, below))
    else:
        lines.append('- 均线位置：数据缺失，无法评估 UP 板块乖离状态。')

    # 仓位基调：来自日报判词（与 action_report 场景生成逻辑一致）
    bits = []
    if scenario == '弱势磨底':
        base, shift = '仓位=防守试探', '宽度站上 40 或修复侧持续扩大→可加仓；板块再破前低→退回观望'
    elif scenario == '高位见顶风险':
        base, shift = '仓位=坚决防守', '宽度回升站上 40→重新评估；拥挤度维持高位→继续减仓'
    elif scenario == '主升行情':
        base, shift = '仓位=顺势做多', '拥挤度逼近 80 或宽度回落→逐步止盈'
    elif scenario == '启动初期':
        base, shift = '仓位=积极做多', '拥挤度升破 60→转追多控仓'
    elif '启动' in verdict or '上行' in verdict or '强势' in verdict:
        base, shift = '仓位=积极做多', None
    elif '弱' in verdict or '下行' in verdict or '回调' in verdict:
        base, shift = '仓位=防守为主', None
    else:
        base, shift = '仓位=中性灵活', None
    bits.append(base)
    if scenes and sc_total:
        if sc_fix / sc_total >= 0.3:
            bits.append('修复侧聚集(S4+S8 占 {:.0%}，★趋势 {})，磨底后段可布局超卖反弹与回踩确认品种'.format(
                sc_fix / sc_total, sc_fix_trend))
        elif sc_frozen / sc_total >= 0.4:
            bits.append('冰封占比 {:.0%}，多数板块仍在阴跌，勿抄底'.format(sc_frozen / sc_total))
        if sc_strong / sc_total >= 0.2:
            bits.append('强势侧占 {:.0%}，普涨扩散确认'.format(sc_strong / sc_total))
        if sc_risk / sc_total >= 0.25:
            bits.append('风险侧占 {:.0%}，规避高位滞涨与恐慌破位品种'.format(sc_risk / sc_total))
    if up_ratio is not None:
        if up_ratio < 0.15:
            bits.append('UP 占比过低，只做 UP 榜头部结构性机会')
        elif up_ratio < 0.4:
            bits.append('可向二线 UP 品种适度扩散')
        else:
            bits.append('普涨环境，可均衡配置')
    if med is not None:
        if med > 0.05:
            bits.append('介入=切勿追高，等回踩均线')
        elif med > 0.02:
            bits.append('介入=不宜追高')
        elif med >= 0:
            bits.append('介入=回踩不破可跟')
        else:
            bits.append('介入=等反转确认')
    # ---- 结论先行：当前市场判断 → 综合建议 → 判断依据（lines 为依据列表，紧凑排版）----
    out = ['### 当前市场判断']
    if verdict:
        out.append('- {}'.format(verdict))
    else:
        out.append('- 日报未提供明确判断，参考下方判断依据。')
    out.append('### 综合建议')
    out.append('- 综合判断：{}。'.format('；'.join(bits)))
    if shift:
        out.append('- 升降级条件：{}'.format(shift))
    out.append('- 风险提示：以上为基于历史数据的规则化推断，非投资建议；实盘需结合当日盘面与资金面二次确认。')
    out.append('### 判断依据')
    out.extend(lines)
    return '\n'.join(out)


def generate(tool_results=None, as_of_date=None):
    """生成整合报告。

    tool_results: {'daily': exit_code, 'trend': exit_code} 可选。
    as_of_date:   历史回放基准日(YYYY-MM-DD)；None 时取最新产物(默认每日执行，行为不变)。
    """
    os.makedirs(COMBINED_DIR, exist_ok=True)
    today_str = as_of_date if as_of_date else date.today().strftime('%Y-%m-%d')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    daily_path, daily_date = _dated_file_asof(DAILY_REPORT_DIR, DAILY_REPORT_PREFIX, '.md', as_of_date)
    trend_path, trend_date = _dated_file_asof(TREND_EXPORTS_DIR, TREND_SUMMARY_PREFIX, '.csv', as_of_date)

    daily_state = extract_daily_state(daily_path)
    trend = extract_trend_lists(trend_path)
    rec_res = _safe_recommend(as_of_date)

    # 排版契约：紧凑排版——空行仅用于 Markdown 语法必需处（如表格前），
    # 不使用装饰性 ---；标题行前后不加空行（标题可安全打断列表/段落）。
    parts = []
    parts.append('# 申万二级行业整合分析报告')
    header = '生成时间: {} | 目标交易日: 下一个交易日'.format(now_str)
    if tool_results:
        status_bits = []
        for k in ('daily', 'trend'):
            if k in tool_results:
                status_bits.append('{}={}'.format(k, 'OK' if tool_results[k] == 0 else 'FAIL'))
        # trend(二级)=run.py 第 1 步自动注入的二级数据重算，同样计入 trend 状态
        if 'trend' not in tool_results and 'trend(二级)' in tool_results:
            status_bits.append('trend={}'.format(
                'OK' if tool_results['trend(二级)'] == 0 else 'FAIL'))
        if status_bits:
            header += ' | 本次工具运行: {}'.format(' | '.join(status_bits))
    parts.append(header)

    rec_picks, rec_near, rec_detail = build_recommendation_sections(rec_res)

    # 一、下一交易日分析与建议（结论：市场判断 → 建议 → 依据 → 重点推荐 → 离达标）
    parts.append('## 一、下一交易日分析与建议')
    if daily_state is None and trend is None:
        parts.append('两大数据源均缺失，无法生成分析。请先运行 daily 与 trend 工具。')
    else:
        parts.append(build_outlook(daily_state, trend, rec_res, daily_date, trend_date))
    parts.append('### 重点推荐（达标标的）')
    parts.append(rec_picks)
    if rec_near:
        parts.append('### 离达标最近')
        parts.append(rec_near)

    # 二、市场总体状态（来自日报）
    parts.append('## 二、市场总体状态（来自日报）')
    parts.append('数据日期: {} {}'.format(
        daily_date or '无', _staleness_note(daily_date, today_str)))
    if daily_state and daily_state.get('section'):
        sec_txt = daily_state['section']
        i = sec_txt.find('### 当前市场判断')
        if i >= 0:
            j = sec_txt.find('\n### ', i + 1)
            sec_txt = (sec_txt[:i] + (sec_txt[j + 1:] if j >= 0 else '')).strip()
        # 紧凑排版：剔除引用段落中的空行（该段无表格，标题/列表相邻可安全省略空行）
        sec_txt = '\n'.join(ln for ln in sec_txt.splitlines() if ln.strip())
        parts.append(sec_txt)
    else:
        parts.append('未找到日报的市场总体状态数据。')

    # 三、推荐筛选明细（候选池统计 + 方法 + 淘汰表）
    parts.append('## 三、推荐筛选明细')
    parts.append(rec_detail)

    # 四、趋势榜（来自趋势分析）
    parts.append('## 四、趋势 UP 前 {} / DOWN 前 {}（来自趋势分析）'.format(TOP_N_UP, TOP_N_DOWN))
    parts.append('数据日期: {} {}'.format(
        trend_date or '无', _staleness_note(trend_date, today_str)))
    if trend:
        list_codes = [(r.get('行业代码') or '').strip()
                      for r in trend['up'][:TOP_N_UP] + trend['down'][:TOP_N_DOWN]]
        closes_map = _recent_closes(list_codes, as_of_date=as_of_date)

        parts.append('### UP 关注榜（前 {}，按综合强度分降序；走势=近20日收盘简笔图）'.format(TOP_N_UP))
        if trend['up']:
            for r in trend['up'][:TOP_N_UP]:
                parts.append('- {}'.format(_fmt_industry(r, closes_map)))
            for ln in _top_up_commentary(trend['up'], closes_map):
                parts.append(ln)
        else:
            parts.append('- （无 UP 榜数据）')

        parts.append('### DOWN 关注榜（前 {}，按综合强度分升序）'.format(TOP_N_DOWN))
        if trend['down']:
            for r in trend['down'][:TOP_N_DOWN]:
                parts.append('- {}'.format(_fmt_industry(r, closes_map)))
        else:
            parts.append('- （无 DOWN 榜数据）')
    else:
        parts.append('未找到趋势分析的榜单数据。')

    # 免责声明与正文之间保留一个空行（避免被吸收进榜单列表项）
    parts.append('')
    parts.append('*本报告由 analysis-hub 自动整合生成，仅供研究参考，不构成投资建议。*')

    content = '\n'.join(parts) + '\n'
    out_path = os.path.join(COMBINED_DIR, 'combined_{}.md'.format(today_str))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('[combine] 整合报告已生成: {}'.format(out_path))
    if daily_date != today_str or trend_date != today_str:
        print('[combine] 提示: 部分数据非当期 (日报={}, 趋势={}, 今日={})'.format(daily_date, trend_date, today_str))
    return out_path


if __name__ == '__main__':
    _as_of = None
    _argv = sys.argv[1:]
    for _i, _a in enumerate(_argv):
        if _a == '--date' and _i + 1 < len(_argv):
            _as_of = _argv[_i + 1].strip()
        elif _a.startswith('--date='):
            _as_of = _a.split('=', 1)[1].strip()
    generate(as_of_date=_as_of)
