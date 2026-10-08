"""ε 阈值探针：为每个 (板块, 窗口) 校准判向阈值并写入 epsilon 表。

口径：取近 config.CALC_LOOKBACK_ROWS 个交易日的滚动 slope_norm 绝对值分布，
取 config.EPSILON_QUANTILE 分位作为该板块该窗口的 ε。样本数不足
config.MIN_SLOPE_SAMPLES 的组合跳过（对应板块窗口判向保持 N/A，避免乱判）。

可重复运行（幂等）：source='probe' 不会覆盖手工校准行（source='manual'）。
同时输出完整分位报告 CSV（config.PROBE_REPORT_QUANTILES）到
config.EPSILON_PROBE_CSV，便于人工复核与手工调整。

用法：
    python probe_epsilon.py                     # 对全部板块全部窗口探针
    python probe_epsilon.py --as-of 2026-09-17  # 截断到指定日期（回放口径）
"""
from __future__ import annotations

import argparse
import csv
import os

import analyze
import config
import db


def probe(conn, as_of: str | None = None,
          codes: list[tuple[str, str]] | None = None) -> list[dict]:
    """执行探针：计算并写库。返回报告行列表。"""
    epsilon_map_backup = db.get_epsilon_map(conn)
    del epsilon_map_backup  # 探针独立于现有 ε 表，避免读取偏置

    industries = codes if codes is not None else _industries(conn)
    report: list[dict] = []

    for code, name in industries:
        prices = db.get_all_prices(conn, code)
        if as_of:
            prices = [p for p in prices if p['trade_date'] <= as_of]
        closes = [p['close'] for p in prices]
        if not closes:
            continue
        closes = closes[-config.CALC_LOOKBACK_ROWS:]

        for w in config.WINDOWS:
            hist = analyze.rolling_slope_norm(closes, w)
            row = {
                '行业代码': code,
                '行业名称': name,
                '窗口': w,
                '样本数': len(hist),
            }
            for q in config.PROBE_REPORT_QUANTILES:
                v = analyze.quantile(hist, q)
                row[f'q{q}'] = v if v is not None else ''
            if len(hist) < config.MIN_SLOPE_SAMPLES:
                row['选用ε'] = ''
                row['备注'] = f'样本不足(<{config.MIN_SLOPE_SAMPLES})'
                report.append(row)
                continue
            eps = analyze.quantile([abs(x) for x in hist], config.EPSILON_QUANTILE)
            if eps is None:
                row['选用ε'] = ''
                row['备注'] = '分位计算失败'
                report.append(row)
                continue
            db.upsert_epsilon(conn, code, w, round(eps, 6),
                              quantile=config.EPSILON_QUANTILE, source='probe')
            row['选用ε'] = round(eps, 6)
            row['备注'] = ''
            report.append(row)

    return report


def _industries(conn) -> list[tuple[str, str]]:
    rows = conn.execute(
        'SELECT sw_code, sw_name FROM industry '
        'ORDER BY row_ord IS NULL, row_ord, sw_code'
    ).fetchall()
    return [(r['sw_code'], r['sw_name']) for r in rows]


def write_report(report: list[dict]) -> str:
    """把报告行写 CSV。返回路径。"""
    os.makedirs(os.path.dirname(config.EPSILON_PROBE_CSV), exist_ok=True)
    header = (['行业代码', '行业名称', '窗口', '样本数']
              + [f'q{q}' for q in config.PROBE_REPORT_QUANTILES]
              + ['选用ε', '备注'])
    with open(config.EPSILON_PROBE_CSV, 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f)
        wr.writerow(header)
        for r in report:
            wr.writerow([r.get(c, '') for c in header])
    return config.EPSILON_PROBE_CSV


def run(args) -> int:
    conn = db.connect()
    db.init_schema(conn)
    n_industry = len(_industries(conn))
    if n_industry == 0:
        raise SystemExit('industry 维表为空：请先运行 main.py（不带 --skip-fetch）或 refresh.py。')

    print('[probe] 开始 ε 探针：板块 {} · 窗口 {} · 分位 {} · 截至 {}'.format(
        n_industry, config.WINDOWS, config.EPSILON_QUANTILE, args.as_of or '最新'))
    report = probe(conn, as_of=args.as_of)
    conn.close()

    path = write_report(report)
    ok = [r for r in report if r.get('选用ε') != '']
    skipped = len(report) - len(ok)
    print('[probe] 完成：校准 {} 项 · 跳过 {} 项（样本不足）'.format(len(ok), skipped))
    print('[probe] 报告 → {}'.format(path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='ε 阈值探针（写 epsilon 表 + 分位报告 CSV）')
    parser.add_argument('--as-of', default=None,
                        help='截断日期（YYYY-MM-DD），默认用全部历史')
    args = parser.parse_args()
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
