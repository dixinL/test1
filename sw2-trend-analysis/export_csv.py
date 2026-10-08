"""把某计算基准日的多空分析结果导出为 CSV（与 Excel 同源同口径）。

产出一份：
- sw_trend_summary_<date>.csv ：每板块一行，各窗口方向平铺（带数值）+ UP/DOWN
                                计数 + 一致性 + 关注榜标注（对应 Excel「汇总」sheet）。

逐窗口明细不再单独导 CSV：其内容与 Excel 的 MA5~MA360 各 sheet 完全等价
（且 Excel 版还带 UP/DOWN 上色），故省去以减小体积、避免冗余。

用法：
    python export_csv.py [--calc-date 2026-08-26]
    未指定 --calc-date 时取库内最新的 calc_date。
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3

import analyze
import config


def _latest_calc_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute('SELECT MAX(calc_date) AS d FROM trend').fetchone()
    return row['d'] if row and row['d'] else None


def export_csv(calc_date: str | None = None) -> tuple[str, str]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    if calc_date is None:
        calc_date = _latest_calc_date(conn)
        if calc_date is None:
            raise SystemExit('trend 表为空，请先运行 main.py 生成计算结果。')

    trend_rows = [
        dict(r)
        for r in conn.execute(
            'SELECT * FROM trend WHERE calc_date = ? ORDER BY window, sw_code',
            (calc_date,),
        )
    ]
    if not trend_rows:
        raise SystemExit(f'未找到 calc_date={calc_date} 的明细。')

    codes = {r['sw_code'] for r in trend_rows}
    closes_map: dict[str, list[float]] = {}
    for code in codes:
        rows = conn.execute(
            'SELECT close FROM price WHERE sw_code = ? ORDER BY trade_date DESC LIMIT ?',
            (code, config.CALC_LOOKBACK_ROWS),
        ).fetchall()
        closes_map[code] = [r['close'] for r in reversed(rows)]

    summary = analyze.build_summary(trend_rows, closes_map=closes_map)
    up_list, down_list = analyze.split_watchlists(summary)
    up_codes = {s['sw_code'] for s in up_list}
    down_codes = {s['sw_code'] for s in down_list}

    os.makedirs(config.EXPORT_DIR, exist_ok=True)
    dir_headers = [f'方向_{w}天' for w in config.WINDOWS]
    dirv_cols = [f'dirv_{w}' for w in config.WINDOWS]
    summary_path = os.path.join(
        config.EXPORT_DIR, f'sw_trend_summary_{calc_date}.csv'
    )

    header = (
        ['行业代码', '行业名称']
        + dir_headers
        + [
            '综合强度分', '自身历史分位', '上行窗口数',
            '下行窗口数', '一致性', '斜率均值(未加权)', '关注榜',
        ]
    )

    def sort_key(s):
        sc = s.get('score', 0.0)
        if s['sw_code'] in up_codes:
            return (0, -sc)
        if s['sw_code'] in down_codes:
            return (1, sc)
        return (2, 0.0)

    with open(summary_path, 'w', newline='', encoding='utf-8-sig') as f:
        wr = csv.writer(f)
        wr.writerow(header)
        for s in sorted(summary, key=sort_key):
            tag = 'UP榜' if s['sw_code'] in up_codes else (
                'DOWN榜' if s['sw_code'] in down_codes else ''
            )
            row = [s['sw_code'], s['sw_name']] + [s[c] for c in dirv_cols]
            row += [
                s.get('score'),
                s.get('self_quantile'),
                s['up_count'],
                s['down_count'],
                s['consistency'],
                s['slope_norm_mean'],
                tag,
            ]
            wr.writerow(row)

    conn.close()

    print(f'基准日 {calc_date}：')
    print(f'  汇总榜单 → {summary_path}（{len(summary)} 行）')
    print('  逐窗口明细 → 见 Excel 的 MA5~MA360 sheet（不再单独导 CSV）')

    return summary_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='导出多空分析结果为 CSV')
    parser.add_argument(
        '--calc-date',
        default=None,
        help='计算基准日（YYYY-MM-DD），默认取库内最新',
    )
    args = parser.parse_args()
    export_csv(calc_date=args.calc_date)
