"""修剪各工具的每日产物，每个前缀只保留最新 60 个交易日。

历史产物只增不减会让仓库无限膨胀；按文件名中的日期排序，保留最新 KEEP 个，
其余删除。仅总览性质的 combined_*.md（analysis-hub 整合报告）永久保留，
不在此修剪；RSS 为单文件滚动覆盖，无需处理。

覆盖范围（与 daily.yml git add 路径一致，删除会随 CI 提交）:
  sw2-daily-report/report/env_summary_YYYY-MM-DD.csv        (test2 消费)
  sw2-daily-report/report/action_report_YYYY-MM-DD.md       (日报工具产物，RSS/推送只读最新)
  sw2-trend-analysis/data/exports/sw_trend_summary_YYYY-MM-DD.csv (test2 消费)

快照只在交易日生成，"最新 KEEP 个文件"即最近 KEEP 个开盘日。
可独立运行补救:  python prune_snapshots.py
"""
import glob
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)

KEEP = 60

TARGETS = [
    (os.path.join(REPO_DIR, 'sw2-daily-report', 'report'), 'env_summary_'),
    (os.path.join(REPO_DIR, 'sw2-daily-report', 'report'), 'action_report_'),
    (os.path.join(REPO_DIR, 'sw2-trend-analysis', 'data', 'exports'),
     'sw_trend_summary_'),
]


def prune(directory, prefix, keep=KEEP):
    """删除 directory 下 prefix*.csv / prefix*.md 中除最新 keep 个以外的文件。"""
    files = [p for p in glob.glob(os.path.join(directory, prefix + '*'))
             if os.path.isfile(p)]
    dated = []
    for p in files:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
        if m:
            dated.append((m.group(1), p))
    dated.sort(reverse=True)
    removed = 0
    for _d, p in dated[keep:]:
        try:
            os.remove(p)
            removed += 1
            print('  [prune] 删除 {}'.format(os.path.relpath(p, REPO_DIR)))
        except OSError as e:
            print('  [WARN] 删除失败 {}: {}'.format(p, e))
    return removed


def main():
    total = 0
    for directory, prefix in TARGETS:
        total += prune(directory, prefix)
    print('[prune] 快照修剪完成 | 每个前缀保留最新 {} 个交易日 | 共删除 {} 个文件'.format(
        KEEP, total))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
