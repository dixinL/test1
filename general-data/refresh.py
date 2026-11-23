"""general-data 基础数据统一刷新入口。

把 8 张表（industry / price / width / congestion / valuation / index_daily /
erp / env_daily）的增量刷新集中在一个入口，供 run.py 第 0 步调度，也可手动运行：

用法:
    python refresh.py                # 全量刷新（含估值，最慢）
    python refresh.py --no-valuation # 跳过估值（日常够用，速度快）
    python refresh.py --no-erp       # 跳过 ERP 估值环境
    python refresh.py --no-env       # 跳过市场环境指标（利率/量能/波动/广度/主线）
    python refresh.py --days-back 30 # 指定乐咕宽度/拥挤度回看天数
    python refresh.py --valuation-since 20260101  # 估值区间回填起点 (YYYYMMDD)
    python refresh.py --purge        # 清除 5 年滚动窗口外行情并 VACUUM
    python refresh.py --stats        # 只打印各表覆盖情况，不刷新
    python refresh.py --retry-failed # 只补跑上次失败的段（读 .refresh_failed 标记，CI 兜底）

写入全部幂等：price 只补新日期；width/congestion/valuation/env_daily 主键冲突
原样保留；重复运行"新增 0 行"即代表已就绪。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta

import swdb
import swfetch

# 随基础数据一起刷新的大盘指数（代码, 名称）；换沪深300 改为 ('sz399300', '沪深300')
DEFAULT_INDEX = ('sh000001', '上证指数')

RETAIN_YEARS = 5                   # 滚动保留窗口（--purge 清除窗口外行情）

# 刷新失败标记文件：记录失败的段名（每行一个），供 --retry-failed 跨风控窗口补跑。
# 触发场景：东财 push2 对数据中心 IP 概率性断连（RemoteDisconnected），
# 脚本内秒级重试无效；CI workflow 检测到标记后 sleep 数分钟再补跑。
FAILED_MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '.refresh_failed')


def _mark_failed(section: str, err) -> None:
    """把失败段追加进标记文件（去重），供 workflow 检测与 --retry-failed 补跑。"""
    existing = set()
    if os.path.isfile(FAILED_MARKER):
        with open(FAILED_MARKER, encoding='utf-8-sig') as f:
            existing = {ln.strip() for ln in f if ln.strip()}
    existing.add(section)
    with open(FAILED_MARKER, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sorted(existing)))
    print('[refresh] [MARK] {} 刷新失败，已写标记待补跑: {}'.format(section, err))


def retry_failed(conn) -> int:
    """补跑标记文件中的失败段；成功段从标记剔除，仍失败段保留待下次补跑。

    全部成功清标记并退出 0；仍有失败也退出 0（仅告警，靠次日运行自愈）。"""
    if not os.path.isfile(FAILED_MARKER):
        print('[refresh] 无失败标记，无需补跑')
        return 0
    with open(FAILED_MARKER, encoding='utf-8-sig') as f:
        sections = [ln.strip().lstrip('\ufeff') for ln in f if ln.strip()]
    print('[refresh] 补跑失败段: {}'.format(', '.join(sections)))
    remaining = set(sections)
    for s in sections:
        fn = RETRY_SECTIONS.get(s)
        if fn is None:
            remaining.discard(s)
            continue
        try:
            fn(conn)
            remaining.discard(s)
            print('[refresh] [OK] {} 补跑成功'.format(s))
        except Exception as e:
            print('[refresh] [WARN] {} 补跑仍失败: {}'.format(s, e))
    if remaining:
        with open(FAILED_MARKER, 'w', encoding='utf-8') as f:
            f.write('\n'.join(sorted(remaining)))
        print('[refresh] [WARN] 仍有失败段: {}，标记保留待下次补跑'.format(
            ', '.join(sorted(remaining))))
    else:
        os.remove(FAILED_MARKER)
        print('[refresh] 全部补跑成功，标记已清除')
    return 0


def refresh_industries(conn) -> int:
    """行业维表：全量列表 upsert。返回行业数。"""
    items = swfetch.get_second_industries()
    for i, it in enumerate(items):
        swdb.upsert_industry(conn, it['sw_code'], it['sw_name'], row_ord=i)
    conn.commit()
    print('[refresh] industry: {} 个板块'.format(len(items)))
    return len(items)


def refresh_prices(conn, industries, verbose=True) -> dict:
    """点位：逐板块增量拉取。返回 {'ok': n, 'failed': n, 'inserted': n}。"""
    ok = failed = inserted = 0
    for it in industries:
        stat = swfetch.fetch_and_store_price(
            conn, it['sw_code'], it['sw_name'], verbose=verbose)
        if stat['status'] == 'ok':
            ok += 1
            inserted += stat['inserted']
        else:
            failed += 1
    print('[refresh] price: 成功 {} · 失败 {} · 新增 {} 行'.format(ok, failed, inserted))
    return {'ok': ok, 'failed': failed, 'inserted': inserted}


def refresh_width(conn, days_back) -> int:
    """市场宽度。返回新增行数。"""
    n = swfetch.fetch_and_store_width(conn, days_back=days_back)
    print('[refresh] width: 新增 {} 行'.format(n))
    return n


def refresh_congestion(conn, days_back) -> int:
    """拥挤度。返回新增行数。"""
    # 乐咕 sw-congestion 接口 severalTradeDays 仅接受 5~30，40 天窗口返回空
    n = swfetch.fetch_and_store_congestion(conn, days_back=min(days_back, 30))
    print('[refresh] congestion: 新增 {} 行'.format(n))
    return n


def refresh_valuation(conn, verbose=True, since=None) -> int:
    """估值：增量拉取；--valuation-since 指定起点 (YYYYMMDD)。返回新增行数。

    起点取三者最大：since 参数 / 库内最新日期+1（增量）/ 空表时近 60 天。
    全量历史回填请显式传 --valuation-since 20230101（乐咕分页约 5s/页，
    3 年约 2400 页，日常增量仅数页，避免 CI 超时）。"""
    today = date.today()
    end = today.strftime('%Y%m%d')
    try:
        latest = conn.execute('SELECT MAX(trade_date) FROM valuation').fetchone()[0]
    except Exception:
        latest = None
    if since:
        start = since
    elif latest:
        d = datetime.strptime(latest, '%Y-%m-%d') + timedelta(days=1)
        start = d.strftime('%Y%m%d')
    else:
        start = (today - timedelta(days=60)).strftime('%Y%m%d')
    if start > end:
        print('[refresh] valuation: 已是最新（{} 起）'.format(latest))
        return 0
    n = swfetch.fetch_and_store_valuation(conn, start, end)
    print('[refresh] valuation: 新增 {} 行'.format(n))
    return n


def refresh_index(conn) -> int:
    """大盘指数日线（东方财富 kline，增量）。返回新增行数。"""
    n = swfetch.fetch_and_store_index(conn, DEFAULT_INDEX[0], DEFAULT_INDEX[1])
    print('[refresh] index({}): 新增 {} 行'.format(DEFAULT_INDEX[1], n))
    return n


def refresh_erp(conn) -> int:
    """ERP 估值环境（沪深300 PE + 10Y 国债）。全历史拉取，幂等增量。"""
    n = swfetch.fetch_and_store_erp(conn)
    print('[refresh] erp: 新增 {} 行'.format(n))
    return n


def refresh_vol20(conn) -> int:
    """波动率（辅助层）：上证指数 20 日已实现波动率（年化%，样本标准差×√250）。

    基于 index_daily 收盘序列全量重算 + INSERT OR IGNORE 幂等。"""
    rows = conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE code = 'sh000001' "
        'ORDER BY trade_date').fetchall()
    closes = [(r['trade_date'], r['close']) for r in rows]
    out = []
    for i in range(20, len(closes)):
        rets = [(closes[j][1] / closes[j - 1][1] - 1) * 100
                for j in range(i - 19, i + 1)]
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        out.append((closes[i][0], round((var ** 0.5) * (250 ** 0.5), 2)))
    n = swdb.upsert_env_rows(conn, 'vol20', out)
    print('[refresh] vol20: 重算 {} 行 · 新增 {} 行'.format(len(out), n))
    return n


def refresh_mainline(conn, top_n=5, window=20) -> int:
    """主线结构（辅助层）：基于 price 表全部行业现算近 20 日涨幅。

    存三个指标——mainline_top5：Top{} 行业均值（主线强度）；
    mainline_disp：Top{} 均值 − 全体行业中位数（离散度，越大主线越清晰）；
    mainline_names：Top{} 行业名单（value=强度，label=名单串）。
    全量重算 + INSERT OR IGNORE 幂等。"""
    dates = [r['trade_date'] for r in conn.execute(
        'SELECT DISTINCT trade_date FROM price ORDER BY trade_date')]
    idx = {d: i for i, d in enumerate(dates)}
    aligned = {}
    for r in conn.execute(
            'SELECT p.sw_code, COALESCE(i.sw_name, p.sw_name) AS name, '
            'p.trade_date, p.close FROM price p '
            'LEFT JOIN industry i ON i.sw_code = p.sw_code ORDER BY p.trade_date'):
        name, vals = aligned.setdefault(r['sw_code'], (r['name'], [None] * len(dates)))
        i = idx.get(r['trade_date'])
        if i is not None:
            vals[i] = r['close']

    top5_rows, disp_rows, names_rows = [], [], []
    for i in range(window, len(dates)):
        chgs = []
        for code, (name, vals) in aligned.items():
            c_now, c_prev = vals[i], vals[i - window]
            if c_now and c_prev:
                chgs.append(((c_now / c_prev - 1) * 100, name or code, code))
        if len(chgs) < 10:
            continue
        chgs.sort(reverse=True)
        top5 = sum(c[0] for c in chgs[:top_n]) / top_n
        srt = sorted(c[0] for c in chgs)
        m = len(srt)
        median = srt[m // 2] if m % 2 else (srt[m // 2 - 1] + srt[m // 2]) / 2
        top5_rows.append((dates[i], round(top5, 2)))
        disp_rows.append((dates[i], round(top5 - median, 2)))
        names_rows.append((dates[i], round(top5, 2),
                           '/'.join(c[1] for c in chgs[:top_n])))
    n1 = swdb.upsert_env_rows(conn, 'mainline_top5', top5_rows)
    n2 = swdb.upsert_env_rows(conn, 'mainline_disp', disp_rows)
    n3 = swdb.upsert_env_rows(conn, 'mainline_names', names_rows)
    print('[refresh] mainline: 重算 {} 行 · 新增 top5 {} / disp {} / names {} 行'.format(
        len(top5_rows), n1, n2, n3))
    return n1 + n2 + n3


def refresh_env(conn) -> dict:
    """市场环境指标（辅助层）：抓取利率/量能/广度 + 现算波动率/主线。

    us10y / dr007 / high20 / low20 / turnover 由 swfetch.fetch_and_store_env
    抓取；vol20 / mainline_top5 / mainline_disp 从库内数据现算。"""
    stats = swfetch.fetch_and_store_env(conn)
    print('[refresh] env: ' + ' · '.join(
        '{} {}'.format(k, v) for k, v in sorted(stats.items())))
    refresh_vol20(conn)
    refresh_mainline(conn)
    return stats


# 可补跑的段（在全部段函数定义之后声明，引用函数对象）
RETRY_SECTIONS = {
    'index': refresh_index,
    'erp': refresh_erp,
    'env': refresh_env,
    'width': lambda conn: refresh_width(conn, 40),
    'congestion': lambda conn: refresh_congestion(conn, 40),
    'valuation': lambda conn: refresh_valuation(conn),
}


def purge_old_data(conn, today: date) -> int:
    """清除滚动窗口（RETAIN_YEARS 年）外的行情数据并 VACUUM 瘦身。

    覆盖所有带 trade_date 的行情表（price/width/congestion/valuation/
    index_daily）与环境指标表 env_daily/erp——环境指标与 ERP 统一跟随
    5 年滚动窗口，抓取层（swfetch.env_cutoff_date）入库前同步过滤，
    窗口外旧行不会被刷新灌回。维表/参数表（industry/trend/epsilon）量小
    且与窗口无关，不参与。删除发生才 VACUUM。返回删除行数。"""
    cutoff = today.replace(year=today.year - RETAIN_YEARS).isoformat()
    deleted = 0
    for table in ('price', 'width', 'congestion', 'valuation', 'index_daily',
                  'env_daily', 'erp'):
        cur = conn.execute(
            'DELETE FROM {} WHERE trade_date < ?'.format(table), (cutoff,))
        deleted += cur.rowcount
    if deleted:
        conn.commit()
        conn.execute('VACUUM')
        conn.commit()
    print('[refresh] purge: cutoff {} · 删除 {} 行{}'.format(
        cutoff, deleted, '（已 VACUUM 瘦身）' if deleted else ''))
    return deleted


def show_stats(conn) -> int:
    """打印各表覆盖情况（行数 + 关键列的 min/max），不刷新。"""
    checks = [
        ('industry', 'row_ord'),
        ('price', 'trade_date'),
        ('width', 'trade_date'),
        ('congestion', 'trade_date'),
        ('valuation', 'trade_date'),
        ('index_daily', 'trade_date'),
        ('erp', 'trade_date'),
        ('env_daily', 'trade_date'),
        ('trend', 'calc_date'),
        ('epsilon', 'updated_at'),
    ]
    print('[refresh] === 各表覆盖情况 ===')
    for table, col in checks:
        n = conn.execute('SELECT COUNT(*) FROM {}'.format(table)).fetchone()[0]
        if n == 0:
            print('  {:<12} 0 行（空表）'.format(table))
            continue
        try:
            lo, hi = conn.execute(
                'SELECT MIN({c}), MAX({c}) FROM {t}'.format(c=col, t=table)).fetchone()
            print('  {:<12} {:>7} 行  {} : {} ~ {}'.format(table, n, col, lo, hi))
        except Exception:
            print('  {:<12} {:>7} 行'.format(table, n))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='general-data 基础数据统一刷新')
    parser.add_argument('--no-valuation', action='store_true',
                        help='跳过估值表（估值拉取最慢，日常刷新可跳过）')
    parser.add_argument('--no-erp', action='store_true',
                        help='跳过 ERP 估值环境刷新（沪深300 PE + 10Y 国债）')
    parser.add_argument('--no-env', action='store_true',
                        help='跳过市场环境指标刷新（利率/量能/波动/广度/主线）')
    parser.add_argument('--retry-failed', action='store_true',
                        help='只补跑上次刷新失败的段（读 .refresh_failed 标记），供 CI 兜底')
    parser.add_argument('--days-back', type=int, default=40,
                        help='乐咕宽度/拥挤度回看自然日数（默认 40）')
    parser.add_argument('--valuation-since', type=str, default=None,
                        help='估值回填起点 (YYYYMMDD)，默认增量（空表时近 60 天）')
    parser.add_argument('--purge', action='store_true',
                        help='清除 {} 年滚动窗口外行情（CI 每年 5 月 1 日自动执行）'.format(RETAIN_YEARS))
    parser.add_argument('--stats', action='store_true',
                        help='只打印各表覆盖情况，不刷新')
    args = parser.parse_args()

    t0 = time.time()
    conn = swdb.connect()
    swdb.init_schema(conn)

    if args.stats:
        rc = show_stats(conn)
        conn.close()
        return rc

    if args.retry_failed:
        rc = retry_failed(conn)
        conn.close()
        return rc

    n_industry = refresh_industries(conn)
    industries = [
        {'sw_code': r['sw_code'], 'sw_name': r['sw_name']}
        for r in conn.execute(
            'SELECT sw_code, sw_name FROM industry ORDER BY row_ord IS NULL, row_ord, sw_code'
        ).fetchall()
    ]
    if not industries:
        print('[refresh] [ERROR] 行业维表为空，无法继续')
        return 1

    refresh_prices(conn, industries)

    try:
        refresh_width(conn, args.days_back)
    except Exception as e:
        _mark_failed('width', e)
        print('[refresh] [WARN] width 刷新失败: {}'.format(e))

    try:
        refresh_congestion(conn, args.days_back)
    except Exception as e:
        _mark_failed('congestion', e)
        print('[refresh] [WARN] congestion 刷新失败: {}'.format(e))

    try:
        refresh_index(conn)
    except Exception as e:
        _mark_failed('index', e)
        print('[refresh] [WARN] index 刷新失败: {}'.format(e))

    if not args.no_erp:
        try:
            refresh_erp(conn)
        except Exception as e:
            _mark_failed('erp', e)
            print('[refresh] [WARN] erp 刷新失败: {}'.format(e))

    if not args.no_env:
        try:
            refresh_env(conn)
        except Exception as e:
            _mark_failed('env', e)
            print('[refresh] [WARN] env 刷新失败: {}'.format(e))

    if not args.no_valuation:
        try:
            refresh_valuation(conn, since=args.valuation_since)
        except Exception as e:
            _mark_failed('valuation', e)
            print('[refresh] [WARN] valuation 刷新失败: {}'.format(e))

    if args.purge:
        purge_old_data(conn, date.today())

    conn.close()
    print('[refresh] 完成，用时 {:.1f}s'.format(time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
