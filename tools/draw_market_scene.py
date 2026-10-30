"""根据每日总览报告绘制"大盘九场景"走势折线图。

数据源:
  - analysis-hub/report/combined_*.md 的"### 当前市场判断"小节：
      大盘九场景行（场景代码/名称/操作 + 高拥挤行业占比，右侧轴驱动）
      + 内嵌"### 市场宽度"的均值行（左侧轴驱动）
  - 大盘指数日线：general-data/sw.db 的 index_daily 表（refresh.py 随基础数据一起刷新）
场景→颜色映射与 sw2-daily-report/action_report.py 的 SCENE_MATRIX 保持一致。

输出: tools/report/market_scene_trend.png
用法: python tools/draw_market_scene.py [输出路径]（仓库根目录下运行）
"""

import bisect
import glob
import os
import re
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # test1/
HUB_DIR = os.path.join(REPO_DIR, 'analysis-hub')
REPORT_DIR = os.path.join(HUB_DIR, 'report')
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report')

# 与 action_report.SCENE_MATRIX 同步: (宽度档, 拥挤档) -> (场景名, 颜色, 操作)
SCENES = {
    (2, 0): ('S1:强势启动', '#22c55e', '积极做多'),
    (2, 1): ('S2:加速上行', '#84cc16', '追多控仓'),
    (2, 2): ('S3:极端过热', '#eab308', '逢高减仓'),
    (1, 0): ('S4:回暖蓄势', '#06b6d4', '逢低吸纳'),
    (1, 1): ('S5:盘整选择', '#8b5cf6', '耐心等待'),
    (1, 2): ('S6:高位滞涨', '#f97316', '逐步撤离'),
    (0, 0): ('S7:冰封观望', '#3b82f6', '远离观望'),
    (0, 1): ('S8:探底企稳', '#6366f1', '密切跟踪'),
    (0, 2): ('S9:恐慌出逃', '#ef4444', '坚决规避'),
}
W_TIERS = ('低', '中', '高')   # 宽度档: <50 / 50~80 / >=80
C_TIERS = ('低', '中', '高')   # 拥挤档: <15% / 15%~30% / >=30%

INDEX_SYMBOL = 'sh000001'      # 大盘指数: sh000001=上证指数, sz399001=深证成指
INDEX_NAME = '上证指数'


def parse_reports():
    """解析全部 combined 报告，返回按日期升序的行列表与跳过清单。"""
    rows, skipped = [], []
    for p in sorted(glob.glob(os.path.join(REPORT_DIR, 'combined_*.md'))):
        dm = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
        if not dm:
            continue
        with open(p, 'r', encoding='utf-8') as f:
            text = f.read()
        m9 = re.search(r'大盘九场景[:：]\s*([^\n]+)', text)
        av = re.search(r'^-\s*均值:\s*([\d.]+)', text, re.M)
        if not m9 or not av:
            skipped.append(os.path.basename(p))
            continue
        raw = m9.group(1).replace('*', '').strip()
        sm = re.match(r'(S\d)[:：]\s*([^(（]+)', raw)
        pm = re.search(r'占比\S*\s*([\d.]+)\s*%', raw)
        wm = re.search(r'宽度(低|中|高)', raw)
        if not sm or not pm or not wm:
            skipped.append(os.path.basename(p))
            continue
        w_avg = float(av.group(1))
        w_tier = W_TIERS.index(wm.group(1))
        w_tier_calc = 0 if w_avg < 50 else 1 if w_avg < 80 else 2
        rows.append({
            'date': dm.group(1), 'code': sm.group(1), 'name': sm.group(2).strip(),
            'share': float(pm.group(1)), 'w_avg': w_avg,
            'w_tier': w_tier, 'w_tier_calc': w_tier_calc,
        })
    return rows, skipped


def merge_segments(rows):
    """相邻同场景合并 -> [(start_idx, end_idx, code)]。"""
    segs = []
    for i, r in enumerate(rows):
        if segs and segs[-1][2] == r['code']:
            segs[-1] = (segs[-1][0], i, r['code'])
        else:
            segs.append((i, i, r['code']))
    return segs


def load_index(start_date, end_date, symbol=INDEX_SYMBOL):
    """从 general-data/sw.db 的 index_daily 表读取指数日线（refresh.py 维护）。

    无数据返回 []，图中不含指数但不影响场景主图。"""
    db_path = os.path.join(REPO_DIR, 'general-data', 'sw.db')
    if not os.path.isfile(db_path):
        print('[scene-chart] [WARN] 未找到基础数据库，图中不含指数: {}'.format(db_path))
        return []
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            'SELECT trade_date, close FROM index_daily '
            'WHERE code = ? AND trade_date >= ? AND trade_date <= ? '
            'ORDER BY trade_date', (symbol, start_date, end_date)).fetchall()
    except sqlite3.OperationalError:
        print('[scene-chart] [WARN] 库中无 index_daily 表，请先运行 general-data/refresh.py')
        return []
    finally:
        conn.close()
    if not rows:
        print('[scene-chart] [WARN] index_daily 无 {} 在 {}~{} 的数据，'
              '请先运行 general-data/refresh.py'.format(symbol, start_date, end_date))
    return [(r[0], float(r[1])) for r in rows]


def draw(rows, out_path, idx_series=None):
    dates = [r['date'] for r in rows]
    n = len(rows)
    segs = merge_segments(rows)
    def cinfo(code):
        for (w, c), (name, col, act) in SCENES.items():
            if name.startswith(code + ':'):
                return name, col, act
        return code, '#999999', ''

    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax1 = plt.subplots(figsize=(15, 7.2), dpi=150)
    ax2 = ax1.twinx()

    # 场景背景带（同场景连续日合并）
    for s, e, code in segs:
        _name, col, _act = cinfo(code)
        ax1.axvspan(s - 0.5, e + 0.5, color=col, alpha=0.20, lw=0, zorder=0)

    # 阈值参考线 + 分区标注
    for y in (50, 80):
        ax1.axhline(y, color='#666666', ls='--', lw=0.9, zorder=1)
    for y in (15, 30):
        ax2.axhline(y, color='#c0392b', ls=':', lw=1.0, zorder=1)
    for y, t in ((25, '宽度低'), (65, '宽度中'), (90, '宽度高')):
        ax1.text(0.4, y, t, color='#888888', fontsize=8, va='center', zorder=1)
    for y, t in ((7.5, '占比低'), (22.5, '占比中'), (65, '占比高')):
        ax2.text(n - 0.6, y, t, color='#c0392b', alpha=0.55, fontsize=8,
                 va='center', ha='right', zorder=1)

    # 双驱动折线
    xs = list(range(n))
    ax1.plot(xs, [r['w_avg'] for r in rows], '-o', ms=2.8, lw=1.6,
             color='#1f4e79', label='宽度均值（左轴）', zorder=3)
    ax2.plot(xs, [r['share'] for r in rows], '--s', ms=2.8, lw=1.3,
             color='#c0392b', label='高拥挤行业占比（右轴）', zorder=3)

    # 大盘指数（外侧右轴；报告日无交易数据时沿用前一交易日收盘，asof 映射）
    idx_line = None
    if idx_series:
        idx_dates = [d for d, _v in idx_series]
        idx_vals = [v for _d, v in idx_series]
        mapped = []
        for d in dates:
            i = bisect.bisect_right(idx_dates, d) - 1
            mapped.append(idx_vals[i] if i >= 0 else float('nan'))
        ax3 = ax1.twinx()
        ax3.spines['right'].set_position(('outward', 46))
        idx_line, = ax3.plot(xs, mapped, '-', lw=1.8, color='#e67e22',
                             label='{}（外侧右轴）'.format(INDEX_NAME), zorder=2)
        vals = [v for v in mapped if v == v]
        if vals:
            lo, hi = min(vals), max(vals)
            pad = (hi - lo) * 0.08 or hi * 0.02
            ax3.set_ylim(lo - pad, hi + pad)
        ax3.set_ylabel(INDEX_NAME, color='#e67e22')
        ax3.tick_params(axis='y', colors='#e67e22', labelsize=8)

    # 场景段标注（>=3 天才标，避免窄段文字重叠）
    for s, e, code in segs:
        if e - s + 1 >= 3:
            name, col, _act = cinfo(code)
            ax1.text((s + e) / 2, 97.5, '{} {}\n{}天'.format(
                code, name.split(':', 1)[1], e - s + 1),
                ha='center', va='top', fontsize=8, color='#333333', zorder=4)

    # 最新状态
    last = rows[-1]
    lname, _col, lact = cinfo(last['code'])
    ax1.text(0.995, 1.03,
             '最新 {}: {}（宽度{} + 占比{:.0f}%）→ {}'.format(
                 last['date'], lname, W_TIERS[last['w_tier']],
                 last['share'], lact),
             transform=ax1.transAxes, ha='right', va='bottom', fontsize=9,
             bbox=dict(fc='#f7f7f7', ec='#cccccc', boxstyle='round,pad=0.35'))

    # 轴与图例
    ax1.set_ylim(0, 100)
    ax2.set_ylim(0, 100)
    ax1.set_xlim(-0.8, n - 0.2)
    ax1.set_ylabel('宽度均值')
    ax2.set_ylabel('高拥挤行业占比 (%)', color='#c0392b')
    ax2.tick_params(axis='y', colors='#c0392b')
    step = max(1, n // 14)
    ax1.set_xticks(xs[::step])
    ax1.set_xticklabels([d[5:] for d in dates[::step]],
                        rotation=45, fontsize=7.5)
    ax1.tick_params(axis='y', labelsize=8)
    ax2.tick_params(axis='y', labelsize=8)

    handles = [ax1.lines[0], ax2.lines[0]]
    if idx_line is not None:
        handles.append(idx_line)
    seen = []
    for _s, _e, code in segs:
        if code not in seen:
            seen.append(code)
    for code in sorted(seen):
        name, col, _act = cinfo(code)
        handles.append(Patch(fc=col, alpha=0.45, label='{} {}'.format(
            code, name.split(':', 1)[1])))
    ax1.legend(handles=handles, loc='upper left', fontsize=8, ncol=2,
               framealpha=0.92)

    plt.title('大盘九场景走势（宽度均值 50/80 × 高拥挤行业占比 15%/30%）',
              fontsize=12, pad=28)
    src = '每日总览报告 combined_*.md'
    if idx_line is not None:
        src += ' + {}日线(index_daily)'.format(INDEX_NAME)
    fig.text(0.995, 0.005,
             '数据: {} | {} ~ {} 共 {} 个报告日'.format(
                 src, dates[0], dates[-1], n),
             ha='right', fontsize=7.5, color='#888888')
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches='tight')
    plt.close(fig)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(OUT_DIR, 'market_scene_trend.png')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    rows, skipped = parse_reports()
    if not rows:
        print('[scene-chart] 未解析到任何报告数据')
        return 2
    print('[scene-chart] 解析 {} 份报告（{} ~ {}）'.format(
        len(rows), rows[0]['date'], rows[-1]['date']))
    if skipped:
        print('[scene-chart] 跳过 {} 份（缺大盘九场景/均值行）: {}'.format(
            len(skipped), ', '.join(skipped[:5]) + ('...' if len(skipped) > 5 else '')))

    bad = [r for r in rows if r['w_tier'] != r['w_tier_calc']]
    if bad:
        print('[scene-chart] [WARN] {} 天宽度档与均值推导不一致: {}'.format(
            len(bad), ', '.join('{}(标{}算{})'.format(
                r['date'], W_TIERS[r['w_tier']], W_TIERS[r['w_tier_calc']])
                for r in bad[:8])))

    from collections import Counter
    dist = Counter('{} {}'.format(r['code'], r['name']) for r in rows)
    print('[scene-chart] 场景分布:')
    for k, v in sorted(dist.items()):
        print('  {:<14} {:>3} 天 ({:.0%})'.format(k, v, v / len(rows)))

    segs = merge_segments(rows)
    print('[scene-chart] 场景段（>=2 天）:')
    for s, e, code in segs:
        if e - s + 1 >= 2:
            print('  {} ~ {}  {}  {}天'.format(
                rows[s]['date'][5:], rows[e]['date'][5:], code, e - s + 1))

    idx_series = load_index(rows[0]['date'], rows[-1]['date'])
    if idx_series:
        print('[scene-chart] 指数: {} {} ~ {} 共 {} 个交易日'.format(
            INDEX_NAME, idx_series[0][0], idx_series[-1][0], len(idx_series)))

    draw(rows, out_path, idx_series)
    print('[scene-chart] 已生成: {}'.format(out_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
