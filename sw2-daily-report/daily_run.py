"""
申万二级行业每日统一调度工具

功能:
  1. 生成分析报告 (action_report)
  2. 生成热力图 (draw_heatmap: width + congestion；默认关闭，--image 显式开启)

RSS 订阅与微信推送由总线 analysis-hub 独占负责（generate_rss.py /
wxpusher_notify.py，基于 report/ 下的报告文件生成）；本脚本不产出
任何 RSS/HTML 页面。

用法:
  python daily_run.py              # 默认: 只生成报告
  python daily_run.py --image      # 附加生成热力图
  python daily_run.py --no-report  # 不生成报告

输出:
  report/action_report_YYYY-MM-DD.md   # 分析报告 (RSS 数据源)
"""

import os
import sys
import subprocess
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def run_script(script_name, args=None, label='', timeout=300):
    """
    运行子脚本并返回 (success, stdout, stderr)
    实时流式输出，防止 GitHub Actions 超时看不到日志
    """
    cmd = [sys.executable, os.path.join(BASE_DIR, script_name)]
    if args:
        cmd.extend(args)
    print('\n============================================================')
    print('  [{}] python {}'.format(label or script_name, ' '.join(cmd[2:])))
    print('  [{}] 开始时间: {}'.format(label or script_name,
                                       datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    print('  [{}] 超时设置: {}s'.format(label or script_name, timeout))
    print('============================================================')
    sys.stdout.flush()
    try:
        result = subprocess.run(
            cmd,
            cwd=BASE_DIR,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        print('\n------------------------------------------------------------')
        print('  [{}] 完成时间: {}'.format(label or script_name,
                                           datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print('  [{}] 返回码: {}'.format(label or script_name, result.returncode))
        print('------------------------------------------------------------')
        sys.stdout.flush()
        return (result.returncode == 0, '', '')
    except subprocess.TimeoutExpired:
        print('\n------------------------------------------------------------')
        print('  [{}] TIMEOUT! 超过 {} 秒'.format(label or script_name, timeout))
        print('  [{}] 超时时间: {}'.format(label or script_name,
                                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print('------------------------------------------------------------')
        sys.stdout.flush()
        return (False, '', 'Timeout after {}s'.format(timeout))


def find_latest_file(directory, pattern):
    """查找目录下匹配pattern的最新文件"""
    if not os.path.isdir(directory):
        return None
    files = [f for f in os.listdir(directory) if pattern in f and not f.startswith('.')]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(directory, files[0])


def _parse_opt(argv, name):
    """解析 `--name VALUE` / `--name=VALUE`，无则返回 None。"""
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1].strip()
        elif a.startswith(name + '='):
            return a.split('=', 1)[1].strip()
    return None


def main():
    """
    主流程:
      1. 生成报告 (action_report.py)
      2. 生成热力图 (draw_heatmap.py width + congestion)
      3. 收集产物路径
      4. 整合为 RSS HTML

    历史回放: 传 `--date YYYY-MM-DD` 时，报告与热力图都按该日的数据视图生成
    (透传给 action_report.py / draw_heatmap.py)；不传则取最新数据(行为不变)。
    可用 `--heatmap-format png|html|both` 指定热力图格式(默认 png)。
    """
    start_time = datetime.now()
    print('=================================================================')
    print('   [*] 申万二级行业每日统一调度')
    print('   开始时间: {}'.format(start_time.strftime('%Y-%m-%d %H:%M:%S')))
    print('   本地日期: {} | UTC日期: {}'.format(date.today(), datetime.utcnow().date()))
    print('=================================================================')
    sys.stdout.flush()

    do_image = '--image' in sys.argv
    no_report = '--no-report' in sys.argv
    as_of_date = _parse_opt(sys.argv, '--date')
    heatmap_format = _parse_opt(sys.argv, '--heatmap-format')

    date_args = ['--date', as_of_date] if as_of_date else []

    print('   参数: do_image={}, no_report={}, date={}, heatmap_format={}'.format(
        do_image, no_report,
        as_of_date or '(最新)',
        heatmap_format or 'png'))
    sys.stdout.flush()

    results = {}
    errors = []

    # Step 1: 生成分析报告
    if not no_report:
        print('\n#################################################################')
        print('# Step 1/5: 生成分析报告 (action_report.py)')
        print('# 时间: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print('#################################################################')
        sys.stdout.flush()
        ok, _, _ = run_script('action_report.py', args=date_args, label='分析报告')
        results['report'] = ok
        print('   [Step 1] 分析报告: {} (用时: {:.1f}s)'.format(
            '成功' if ok else '失败',
            (datetime.now() - start_time).total_seconds()))
        sys.stdout.flush()
        if not ok:
            errors.append('action_report 失败')
    else:
        print('   [Step 1] 跳过分析报告 (--no-report)')
        sys.stdout.flush()

    # Step 2: 生成热力图
    step_start = datetime.now()
    if do_image:
        print('\n#################################################################')
        print('# Step 2/5: 生成热力图 (draw_heatmap.py)')
        print('# 时间: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print('#################################################################')
        sys.stdout.flush()

        fmt_args = [heatmap_format] if heatmap_format else []

        ok_w, _, _ = run_script(
            'draw_heatmap.py',
            ['width'] + fmt_args + date_args,
            label='宽度热力图')
        ok_c, _, _ = run_script(
            'draw_heatmap.py',
            ['congestion'] + fmt_args + date_args,
            label='拥挤度热力图')

        results['heatmap'] = ok_w and ok_c
        step_elapsed = (datetime.now() - step_start).total_seconds()
        print('   [Step 2] 宽度热力图: {}, 拥挤度热力图: {} (用时: {:.1f}s)'.format(
            '成功' if ok_w else '失败',
            '成功' if ok_c else '失败',
            step_elapsed))
        sys.stdout.flush()
        if not ok_w:
            errors.append('width heatmap 失败')
        if not ok_c:
            errors.append('congestion heatmap 失败')
    else:
        print('   [Step 2] 跳过热力图 (默认关闭, --image 开启)')
        sys.stdout.flush()

    # Step 3: 收集产物路径
    step_start = datetime.now()
    print('\n#################################################################')
    print('# Step 3/3: 收集产物路径')
    print('# 时间: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    print('#################################################################')
    sys.stdout.flush()

    today_str = date.today().strftime('%Y-%m-%d')
    report_path = find_latest_file(os.path.join(BASE_DIR, 'report'), 'action_report_')
    width_img = find_latest_file(os.path.join(BASE_DIR, 'width'), '_heatmap.png')
    cong_img = find_latest_file(os.path.join(BASE_DIR, 'congestion'), '_heatmap.png')

    print('   [Step 3] 报告路径: {}'.format(report_path or '未找到'))
    print('   [Step 3] 宽度图: {}'.format(width_img or '未找到'))
    print('   [Step 3] 拥挤度图: {}'.format(cong_img or '未找到'))
    sys.stdout.flush()

    data_date = today_str
    if report_path:
        import re
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(report_path))
        if m:
            data_date = m.group(1)

    print('   [Step 3] 数据日期: {}'.format(data_date))
    sys.stdout.flush()
    step_elapsed = (datetime.now() - step_start).total_seconds()
    print('   [Step 3] 完成 (用时: {:.1f}s)'.format(step_elapsed))
    sys.stdout.flush()

    elapsed = (datetime.now() - start_time).total_seconds()
    print('\n=================================================================')
    print('   [*] 完成! 总用时: {:.1f}s'.format(elapsed))
    print('-----------------------------------------------------------------')

    critical_ok = all([
        results.get('report', True),
        results.get('heatmap', True),
    ])
    status_icon = '[OK]' if critical_ok else '[FAIL]'
    heat_disp = '跳过' if 'heatmap' not in results else ('[OK]' if results['heatmap'] else '[FAIL]')
    print('   {} 报告: {} | 热力图: {}'.format(
        status_icon,
        '[OK]' if results.get('report', True) else '[FAIL]',
        heat_disp))
    print('-----------------------------------------------------------------')
    if errors:
        print('   [ERROR] 错误列表: {}'.format(', '.join(errors)))
    print('   [*] 输出文件:')
    if report_path:
        print('       报告: {}'.format(report_path))
    if width_img:
        print('       宽度图: {}'.format(width_img))
    if cong_img:
        print('       拥挤度图: {}'.format(cong_img))
    print('=================================================================')

    return 0 if critical_ok else 1


if __name__ == '__main__':
    sys.exit(main())
