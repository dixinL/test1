"""
市场环境仪表盘生成器 (sw2-market-env)。

汇总市场环境指标（ERP 估值环境 / 利率 / 量能 / 波动 / 广度 / 主线结构），
生成环境日报，写到 report/market_env_{date}.md。
纯读库（general-data/sw.db），不联网抓取；数据由 general-data/refresh.py
的 erp / env 刷新步骤入库（5 年滚动窗口，与 purge 同口径）。

指标口径详见同目录《市场环境指标说明.md》。

用法:
    python env_report.py                     # 基准日 = 各指标可用数据最新日
    python env_report.py --date 2026-11-10   # 历史回放（指标按日对齐，无未来函数）
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, 'report')

import envdb


def _parse_as_of_date(argv):
    """从命令行解析 --date YYYY-MM-DD（历史回放用），不传返回 None。"""
    for i, a in enumerate(argv):
        if a == '--date' and i + 1 < len(argv):
            return argv[i + 1].strip()
        elif a.startswith('--date='):
            return a.split('=', 1)[1].strip()
    return None


def _series(indicator, as_of):
    """某环境指标序列 [(trade_date, value)] 升序，剔除 value 为 None 的行。

    as_of_date=None 取到最新；回放时 get_env_series 内部按日截断，
    分位计算天然无未来函数。空表/查询异常返回 []。"""
    try:
        rows = envdb.get_env_series(indicator, as_of_date=as_of)
    except Exception:
        rows = []
    return [(r['trade_date'], r['value']) for r in rows if r['value'] is not None]


def _pct(series):
    """分位：序列内 value <= 当前值（序列末行）的占比（%）；样本 <2 返回 None。"""
    if len(series) < 2:
        return None
    cur = series[-1][1]
    return sum(1 for _d, v in series if v <= cur) / len(series) * 100


def _fmt(indicator, value):
    """按指标格式化当前值。"""
    if indicator == 'turnover':
        if value >= 10000:
            return '{:.2f} 万亿元'.format(value / 10000.0)
        return '{:.0f} 亿元'.format(value)
    if indicator in ('high20', 'low20'):
        return '{:.0f} 家'.format(value)
    return '{:.2f}%'.format(value)


def _mainline_label(top5, disp):
    """主线档位文字：离散度越大主线越清晰；Top5 均值为负即普跌。"""
    if top5 is None or disp is None:
        return None
    if top5 < 0:
        return '普跌市，无主线'
    if disp >= 8:
        return '主线清晰'
    if disp >= 3:
        return '主线一般'
    return '普涨/均衡，无明显主线'


def _metric_line(label, indicator, series, base):
    """`- {label}：{值}（分位 x%［，截至 d］）`；序列空返回 None。

    序列末行日期早于基准日时附"截至"标注（数据滞后提示）。"""
    if not series:
        return None
    d, v = series[-1]
    p = _pct(series)
    seg = '分位 {:.1f}%'.format(p) if p is not None else '样本不足'
    if d != base:
        seg += '，截至 {}'.format(d)
    return '- {}：{}（{}）'.format(label, _fmt(indicator, v), seg)


def build_env_dashboard(as_of_date=None):
    """构建环境仪表盘 markdown。返回 (基准日, 文本)。"""
    us = _series('us10y', as_of_date)
    dr = _series('dr007', as_of_date)
    to = _series('turnover', as_of_date)
    vol = _series('vol20', as_of_date)
    hi = _series('high20', as_of_date)
    lo = _series('low20', as_of_date)
    mt = _series('mainline_top5', as_of_date)
    mdisp = _series('mainline_disp', as_of_date)
    try:
        erp_rows = envdb.get_erp_series('000300', as_of_date=as_of_date)
    except Exception:
        erp_rows = []
    erp_valid = [r for r in erp_rows if r['erp'] is not None]

    # 基准日：显式 --date，否则取各指标可用数据的最新日期
    latest = [s[-1][0] for s in (us, dr, to, vol, hi, lo, mt, mdisp) if s]
    if erp_valid:
        latest.append(erp_valid[-1]['trade_date'])
    base = as_of_date or (max(latest) if latest else None)
    if not base:
        return None, None

    lines = ['# 市场环境仪表盘（{}）'.format(base), '',
             '> 环境辅助层，仅展示、不参与三态/4 档判定。分位 = 库内序列'
             '（5 年滚动窗口）内 value <= 当前值的占比；回放模式按日对齐，'
             '无未来函数。指标口径详见《市场环境指标说明.md》。', '']

    # 一、估值与利率
    lines += ['## 一、估值与利率', '']
    if erp_valid:
        last = erp_valid[-1]
        pct = sum(1 for r in erp_valid if r['erp'] <= last['erp']) / len(erp_valid) * 100
        seg = '分位 {:.1f}%'.format(pct)
        if last['trade_date'] != base:
            seg += '，截至 {}'.format(last['trade_date'])
        lines.append('- ERP 估值环境：沪深300 PE {:.2f} → EY {:.2f}% − 10Y国债 {:.2f}% '
                     '= **{:.2f}%**（{}）'.format(
                         last['pe'], last['ey'], last['rf'], last['erp'], seg))
    for label, indicator, series in (
            ('10Y 美债', 'us10y', us),
            ('DR007（存款类机构 7 天回购定盘）', 'dr007', dr)):
        ln = _metric_line(label, indicator, series, base)
        if ln:
            lines.append(ln)
    lines.append('')

    # 二、量能与波动
    lines += ['## 二、量能与波动', '']
    for label, indicator, series in (
            ('两市成交额', 'turnover', to),
            ('20 日波动率（年化）', 'vol20', vol)):
        ln = _metric_line(label, indicator, series, base)
        if ln:
            lines.append(ln)
    lines.append('')

    # 三、市场广度（家数为绝对值，不参与分位）
    lines += ['## 三、市场广度', '']
    if hi or lo:
        h = hi[-1][1] if hi else None
        l = lo[-1][1] if lo else None
        if h is not None and l is not None:
            net = int(h) - int(l)
            tone = '强市广度' if net > 0 else ('弱市广度' if net < 0 else '均衡')
            lines.append('- 创 20 日新高 {} 家 / 新低 {} 家（净额 {:+d}，{}）'.format(
                int(h), int(l), net, tone))
        elif h is not None:
            lines.append('- 创 20 日新高 {} 家'.format(h))
        else:
            lines.append('- 创 20 日新低 {} 家'.format(l))
    lines.append('')

    # 四、主线结构（离散度 = Top5 均值 − 全体行业中位数）
    lines += ['## 四、主线结构', '']
    if mt:
        top5 = mt[-1][1]
        disp = mdisp[-1][1] if mdisp else None
        names_row = _latest_label('mainline_names', as_of_date)
        names_txt = ''
        if names_row is not None and names_row['label']:
            names_txt = '主线：{} · '.format(names_row['label'])
        if disp is not None:
            lines.append('- {}主线强度 {:.2f}% · 离散度 {:+.2f}% → **{}**'.format(
                names_txt, top5, disp, _mainline_label(top5, disp) or '样本不足'))
        else:
            lines.append('- {}主线强度（Top5 均值）{:.2f}%'.format(names_txt, top5))
    lines.append('')
    return base, '\n'.join(lines)


def _latest_label(indicator, as_of):
    """取某指标最新非空行的 Row（含 label 列）；无数据/异常返回 None。"""
    try:
        rows = envdb.get_env_series(indicator, as_of_date=as_of)
    except Exception:
        return None
    rows = [r for r in rows if r['value'] is not None]
    return rows[-1] if rows else None


def _mainline_text(top5, disp, names_row):
    """主线片段：有名单时 `主线：{名单}（强度 x · 离散度 ±x → 档位）`，
    否则回退 `主线强度 x · 离散度 ±x → 档位`。disp 缺失返回 None。"""
    if disp is None:
        return None
    tail = '强度 {:.2f}% · 离散度 {:+.2f}% → {}'.format(
        top5, disp, _mainline_label(top5, disp) or '样本不足')
    if names_row is not None and names_row['label']:
        return '主线：{}（{}）'.format(names_row['label'], tail)
    return '主线强度 ' + tail


def build_env_lines(as_of_date=None):
    """紧凑两行（combined 总览报告注入用），返回 (基准日, [行...])。

    - 市场环境：DR007 / 10Y美债 / 两市成交 / 20日波动，各带分位
    - 广度与主线：20日新高/新低净额 + 主线强度·离散度档位
    库内无任何数据返回 (None, None)。"""
    us = _series('us10y', as_of_date)
    dr = _series('dr007', as_of_date)
    to = _series('turnover', as_of_date)
    vol = _series('vol20', as_of_date)
    hi = _series('high20', as_of_date)
    lo = _series('low20', as_of_date)
    mt = _series('mainline_top5', as_of_date)
    mdisp = _series('mainline_disp', as_of_date)

    latest = [s[-1][0] for s in (us, dr, to, vol, hi, lo, mt, mdisp) if s]
    base = as_of_date or (max(latest) if latest else None)
    if not base:
        return None, None

    def _seg(series, indicator):
        """`值（分位 x%［，截至 d］）`：末行日期早于基准日时附滞后标注。"""
        d, v = series[-1]
        p = _pct(series)
        seg = '分位 {:.1f}%'.format(p) if p is not None else '样本不足'
        if d != base:
            seg += '，截至 {}'.format(d)
        return '{}（{}）'.format(_fmt(indicator, v), seg)

    parts = []
    if dr:
        parts.append('DR007 ' + _seg(dr, 'dr007'))
    if us:
        parts.append('10Y美债 ' + _seg(us, 'us10y'))
    if to:
        parts.append('两市成交 ' + _seg(to, 'turnover'))
    if vol:
        parts.append('20日波动 ' + _seg(vol, 'vol20'))
    line1 = '- 市场环境：' + ' | '.join(parts) if parts else None

    parts = []
    if hi and lo:
        net = int(hi[-1][1]) - int(lo[-1][1])
        tone = '强市' if net > 0 else ('弱市' if net < 0 else '均衡')
        parts.append('20日新高 {} / 新低 {}（净额 {:+d}，{}广度）'.format(
            int(hi[-1][1]), int(lo[-1][1]), net, tone))
    if mt:
        top5 = mt[-1][1]
        disp = mdisp[-1][1] if mdisp else None
        names_row = _latest_label('mainline_names', as_of_date)
        mtxt = _mainline_text(top5, disp, names_row)
        if mtxt:
            parts.append(mtxt)
    line2 = '- 广度与主线：' + ' | '.join(parts) if parts else None

    lines = [l for l in (line1, line2) if l]
    return base, (lines or None)


def main() -> int:
    as_of = _parse_as_of_date(sys.argv[1:])
    base, md = build_env_dashboard(as_of)
    if not md:
        print('[env-report] [ERROR] 库内无任何环境指标数据，无法生成报告')
        return 1
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, 'market_env_{}.md'.format(base))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md)
    print('[env-report] 已生成: {}（基准日 {}）'.format(path, base))
    return 0


if __name__ == '__main__':
    sys.exit(main())
