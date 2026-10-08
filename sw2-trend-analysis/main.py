"""多空趋势分析主流程（调度入口）。

流程：初始化库 → (可选)刷新行业与点位 → 加载 ε 表 → 逐基准日计算
各板块多窗口方向并写入 trend 表 → (可选)导出 CSV / Excel。

ε 表为空时所有方向会退化为 N/A，请先运行一次:
    python probe_epsilon.py

用法：
    python main.py                          # 刷新 + 计算最新基准日 + 导出
    python main.py --skip-fetch             # 不抓数，直接用库内数据计算
    python main.py --recompute-days 5       # 最近 5 个交易日全部重算（落库）
    python main.py --calc-date 2026-09-17   # 指定基准日（回放/回填）
    python main.py --skip-fetch --no-excel --no-csv   # 只算落库（hub 调度用）
"""
from __future__ import annotations

import argparse

import analyze
import config
import db


def _industries(conn) -> list[tuple[str, str]]:
    """从维表取行业列表 [(sw_code, sw_name)]，按 row_ord 排序。"""
    rows = conn.execute(
        'SELECT sw_code, sw_name FROM industry '
        'ORDER BY row_ord IS NULL, row_ord, sw_code'
    ).fetchall()
    return [(r['sw_code'], r['sw_name']) for r in rows]


def refresh_industries(conn) -> int:
    """从 AkShare 拉行业列表写入维表。返回行业数。"""
    import swfetch
    items = swfetch.get_second_industries()
    for i, it in enumerate(items):
        db.upsert_industry(conn, it['sw_code'], it['sw_name'], row_ord=i)
    conn.commit()
    print(f'[trend] 行业维表: {len(items)} 个板块')
    return len(items)


def fetch_all(conn, verbose: bool = True) -> None:
    """逐板块抓取点位并入库（失败板块告警继续）。"""
    import fetch as trend_fetch
    for code, name in _industries(conn):
        try:
            trend_fetch.fetch_and_store(conn, code, name, verbose=verbose)
        except Exception as e:
            print(f'  [WARN] {code} {name}: {e}')


def compute_date(conn, calc_date: str, epsilon_map: dict,
                 codes: list[tuple[str, str]] | None = None,
                 verbose: bool = True) -> dict:
    """计算单个基准日全市场趋势明细并写库。返回方向统计。"""
    created_at = analyze.now_iso()
    all_rows: list[dict] = []
    n_na_eps = 0
    for code, name in (codes if codes is not None else _industries(conn)):
        prices = db.get_recent_prices(
            conn, code, config.CALC_LOOKBACK_ROWS, as_of_date=calc_date)
        closes = [p['close'] for p in prices]
        if not closes:
            continue
        rows = analyze.analyze_one(code, name, closes, epsilon_map, calc_date, created_at)
        n_na_eps += sum(1 for r in rows if r['direction'] == 'N/A' and r['epsilon'] is None)
        all_rows.extend(rows)
    db.upsert_trends(conn, all_rows)

    stats = {'UP': 0, 'DOWN': 0, 'FLAT': 0, 'N/A': 0}
    for r in all_rows:
        stats[r['direction']] = stats.get(r['direction'], 0) + 1
    if verbose:
        n_codes = len({r['sw_code'] for r in all_rows})
        print('[trend] 基准日 {}: UP {} · DOWN {} · FLAT {} · N/A {}（板块 {}，窗口明细 {} 行）'.format(
            calc_date, stats['UP'], stats['DOWN'], stats['FLAT'], stats['N/A'],
            n_codes, len(all_rows)))
        if n_na_eps:
            print('[trend] [WARN] {} 行因 ε 缺失判为 N/A，请先运行 probe_epsilon.py'.format(n_na_eps))
    return stats


def _pick_calc_dates(conn, calc_date: str | None, recompute_days: int) -> list[str]:
    """决定本次要计算的基准日列表（升序）。

    --calc-date 指定 → 仅该日；否则取最近 recompute_days 个交易日。"""
    if calc_date:
        return [calc_date]
    dates = db.get_recent_trade_dates(conn, max(1, recompute_days))
    if not dates:
        raise SystemExit('price 表为空，请先刷新数据（去掉 --skip-fetch 或运行 refresh.py）。')
    return dates


def run(args) -> int:
    conn = db.connect()
    db.init_schema(conn)

    if not args.skip_fetch:
        refresh_industries(conn)
        fetch_all(conn)

    epsilon_map = db.get_epsilon_map(conn)
    if not epsilon_map:
        print('[trend] [WARN] ε 表为空：所有方向将判为 N/A。请先运行 probe_epsilon.py。')

    codes = _industries(conn)
    dates = _pick_calc_dates(conn, args.calc_date, args.recompute_days)
    print('[trend] 计划计算基准日: {} ... {}（共 {} 个）'.format(dates[0], dates[-1], len(dates)))

    for d in dates:
        compute_date(conn, d, epsilon_map, codes=codes)

    latest = dates[-1]

    if not args.no_csv:
        import export_csv
        export_csv.export_csv(calc_date=latest)

    if not args.no_excel:
        import export_excel
        trend_rows = db.get_trends_by_date(conn, latest)
        closes_map: dict[str, list[float]] = {}
        for code, _ in codes:
            prices = db.get_recent_prices(conn, code, config.CALC_LOOKBACK_ROWS, as_of_date=latest)
            closes_map[code] = [p['close'] for p in prices]
        summary = analyze.build_summary(trend_rows, closes_map=closes_map)
        snapshot = db.get_latest_snapshot(conn)
        path = export_excel.export(calc_date=latest, snapshot=snapshot,
                                   trend_rows=trend_rows, summary=summary)
        print(f'[trend] Excel → {path}')

    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='申万二级行业多空趋势分析')
    parser.add_argument('--calc-date', default=None,
                        help='计算基准日（YYYY-MM-DD），默认取 price 最新交易日')
    parser.add_argument('--skip-fetch', action='store_true',
                        help='跳过联网抓取，只用库内数据')
    parser.add_argument('--recompute-days', type=int, default=1,
                        help='重算最近 N 个交易日（默认 1，即仅最新）')
    parser.add_argument('--no-csv', action='store_true', help='不导出 CSV')
    parser.add_argument('--no-excel', action='store_true', help='不导出 Excel')
    args = parser.parse_args()
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
