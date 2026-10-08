"""general-data 基础数据统一刷新入口。

把 5 张表（industry / price / width / congestion / valuation）的增量刷新
集中在一个入口，供 run.py 第 0 步调度，也可手动运行：

用法:
    python refresh.py                # 全量刷新（含估值，最慢）
    python refresh.py --no-valuation # 跳过估值（日常够用，速度快）
    python refresh.py --days-back 30 # 指定乐咕宽度/拥挤度回看天数
    python refresh.py --valuation-since 20260101  # 估值区间回填起点 (YYYYMMDD)
    python refresh.py --stats        # 只打印各表覆盖情况，不刷新

写入全部幂等：price 只补新日期；width/congestion/valuation 主键冲突
原样保留；重复运行"新增 0 行"即代表已就绪。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta

import swdb
import swfetch


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


def show_stats(conn) -> int:
    """打印各表覆盖情况（行数 + 关键列的 min/max），不刷新。"""
    checks = [
        ('industry', 'row_ord'),
        ('price', 'trade_date'),
        ('width', 'trade_date'),
        ('congestion', 'trade_date'),
        ('valuation', 'trade_date'),
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
    parser.add_argument('--days-back', type=int, default=40,
                        help='乐咕宽度/拥挤度回看自然日数（默认 40）')
    parser.add_argument('--valuation-since', type=str, default=None,
                        help='估值回填起点 (YYYYMMDD)，默认增量（空表时近 60 天）')
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
        print('[refresh] [WARN] width 刷新失败: {}'.format(e))

    try:
        refresh_congestion(conn, args.days_back)
    except Exception as e:
        print('[refresh] [WARN] congestion 刷新失败: {}'.format(e))

    if not args.no_valuation:
        try:
            refresh_valuation(conn, since=args.valuation_since)
        except Exception as e:
            print('[refresh] [WARN] valuation 刷新失败: {}'.format(e))

    conn.close()
    print('[refresh] 完成，用时 {:.1f}s'.format(time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
