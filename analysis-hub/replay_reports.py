"""按月/按日期区间重跑整合报告（回放模式）。

与 run.py --replay-since 的差异：除了"有数据"的交易日完整重跑
daily → trend → combine 外，还会为范围内已有 combined_*.md 但当日
无新数据的日期（CI 仅沿用前一交易日数据生成整合报告的日子，如周末/
数据缺口日）重新生成整合报告，保证范围内所有历史报告都带上最新口径。
全部按日期升序执行，保证"连续状态天数"按时间顺序正确累计。

用法:
  python replay_reports.py 2026-10                  # 重跑 2026-10 全部已有报告
  python replay_reports.py 2026-10-08 2026-10-29    # 指定日期区间（含端点）
选项:
  --with-image   日报附带生成热力图（默认跳过以加快重跑，热力图不含判词内容）

依赖: general-data 库表中已有对应日期的 width/congestion 数据
（缺失时可先执行 python run.py --backfill-since <date> 补齐）。
"""

import calendar
import glob
import os
import re
import subprocess
import sys

import combine_report
import run


def _month_bounds(month):
    """'2026-10' -> ('2026-10-01', '2026-10-31')。"""
    y, m = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, m)[1]
    return '{:04d}-{:02d}-01'.format(y, m), '{:04d}-{:02d}-{:02d}'.format(y, m, last)


def _combined_dates(since, until):
    """范围内已有 combined 报告的日期（升序）。范围内没有历史报告时返回 []，
    由调用方回退为数据交易日。"""
    dates = set()
    for p in glob.glob(os.path.join(combine_report.COMBINED_DIR, 'combined_*.md')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
        if m and since <= m.group(1) <= until:
            dates.add(m.group(1))
    return sorted(dates)


def main():
    raw = sys.argv[1:]
    with_image = '--with-image' in raw
    argv = [a for a in raw if a != '--with-image']

    if len(argv) == 1 and len(argv[0]) == 7:
        since, until = _month_bounds(argv[0])
    elif len(argv) == 2:
        since, until = argv
    else:
        print(__doc__)
        return 2

    data_dates = set(run._replay_trading_days(since, until))
    targets = _combined_dates(since, until) or sorted(data_dates)
    if not targets:
        print('[rerun] 范围内既无历史报告也无可用数据交易日: {} ~ {}'.format(since, until))
        return 2

    n_full = len([d for d in targets if d in data_dates])
    print('=================================================================')
    print('[rerun] 重跑 {} ~ {} | 共 {} 天（完整链路 {} 天 + 仅整合 {} 天）'.format(
        since, until, len(targets), n_full, len(targets) - n_full))
    print('=================================================================')

    daily_dir = os.path.join(run.REPO_DIR, run.TOOLS['daily']['dir'])
    trend_dir = os.path.join(run.REPO_DIR, run.TOOLS['trend']['dir'])
    any_failed = False

    for i, d in enumerate(targets, 1):
        print('\n[rerun] [{}/{}] {}'.format(i, len(targets), d))
        sys.stdout.flush()

        tool_results = None
        if d in data_dates:
            daily_cmd = [sys.executable, run.TOOLS['daily']['entry'], '--date', d]
            if with_image:
                daily_cmd += ['--image', '--heatmap-format', 'html']
            rc_daily = subprocess.run(daily_cmd, cwd=daily_dir, env=run._child_env()).returncode

            rc_trend = subprocess.run(
                [sys.executable, run.TOOLS['trend']['entry'],
                 '--calc-date', d, '--skip-fetch', '--no-excel'],
                cwd=trend_dir, env=run._child_env()).returncode

            tool_results = {'daily': rc_daily, 'trend': rc_trend}
            if rc_daily != 0:
                any_failed = True
                print('[rerun]   日报({})返回非零({})，整合仍继续。'.format(d, rc_daily))
            if rc_trend != 0:
                any_failed = True
                print('[rerun]   趋势({})返回非零({})，整合仍继续。'.format(d, rc_trend))
        else:
            print('[rerun]   当日无新数据，仅按最近日报重新生成整合报告。')

        try:
            combine_report.generate(tool_results=tool_results, as_of_date=d)
        except Exception as e:
            any_failed = True
            print('[rerun]   整合报告({})生成失败: {}'.format(d, e))
        sys.stdout.flush()

    print('\n=================================================================')
    print('[rerun] 完成 | 共 {} 天 | {}'.format(len(targets), '有失败项' if any_failed else '全部成功'))
    print('=================================================================')
    return 1 if any_failed else 0


if __name__ == '__main__':
    sys.exit(main())
