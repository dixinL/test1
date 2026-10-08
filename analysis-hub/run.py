"""
分析主入口 (analysis-hub)。

统一调度仓库内的各个分析工具。默认执行全部已注册工具；
也可指定只跑其中某几个。每个工具在各自的目录下作为独立子进程运行，
用当前的 Python 解释器 (sys.executable)。

执行完基础工具后，会整合两者的最新产物生成一份整合分析报告
(analysis-hub/report/combined_{date}.md)——无论工具本次成败都会尝试生成，
缺失/过期数据在报告中显式说明。可用 --no-combine 跳过。
整合之后是分发环节：生成两份 RSS 订阅源（日报 + 趋势，--no-rss 关闭），
并默认用 WxPusher 推送微信（--no-notify 关闭；--notify 保留兼容，等价于默认行为）。

已注册工具:
  daily  -> 申万二级行业每日量化日报 (sw2-daily-report/daily_run.py)
  trend  -> 申万二级行业趋势分析      (sw2-trend-analysis/main.py)

用法:
  python run.py                     # 先刷新 general-data 基础数据，再依次执行全部工具 + 生成整合报告
  python run.py daily               # 只跑日报
  python run.py trend               # 只跑趋势分析
  python run.py daily trend         # 指定多个 (按给出顺序执行)
  python run.py --no-refresh        # 跳过基础数据刷新（仅用库内现有数据）
  python run.py --no-combine        # 跑工具但不生成整合报告
  python run.py --combine-only      # 不跑工具，仅用现有产物生成整合报告
  python run.py --no-rss            # 跑工具但不生成 RSS
  python run.py --no-notify         # 执行后不推送微信（WxPusher 默认开）
  python run.py --rss-only          # 不跑工具，仅用现有产物生成两份 RSS
  python run.py --notify-only       # 不跑工具，仅推送最新日报
  python run.py --list              # 列出所有已注册工具
  python run.py trend -- --skip-fetch   # '--' 之后的参数透传给该工具

数据刷新 (第 0 步，先于一切工具):
  general-data/refresh.py 统一刷新 industry/price/width/congestion/valuation。
  全部写入幂等：price 只补新日期、其余表主键冲突原样保留，重复运行
  "新增 0 行"即代表已就绪——非必要不重新赋同样的值。
  各工具不再自带抓取逻辑，一律从库读，保证分层清晰。

执行分层 (严格串行，缺一不可):
  第 0 步 基础数据   refresh.py           (price/width/congestion/valuation)
  第 1 步 二级数据   trend --skip-fetch   (由 price 现算 trend/epsilon，
                      默认 --recompute-days 5 逐日重算覆盖滞后行)
  第 2 步 工具执行   daily / recommend 等 (纯读库，排在最后)
  第 3 步 整合报告   combine
  第 4 步 分发       RSS×2 (generate_rss / generate_trend_rss) +
                     WxPusher 微信推送（默认开，--no-notify 关闭）
  trend 具有双重身份：既是第 1 步的二级数据生产者，也可作为工具显式运行
  （显式运行时跳过第 1 步的自动注入，避免重复计算）。--no-trend 跳过第 1 步。

回填 (补齐历史某日起的数据):
  python run.py --backfill-since 2026-08-01
    · 第 0 步统一刷新基础数据（幂等补齐 price/width/congestion，跳过估值）；
    · 日报侧: 常规 daily_run 基于补齐后的库表出报告/热力图（历史单日的
      report/热力图产物不会重建，只补数据表）；
    · 趋势侧: price 已刷新，直接 --skip-fetch 计算，再对 >=since 且 trend 表
      尚缺的每个交易日循环 main.py --calc-date <d> --skip-fetch 逐日回填。

回放 (从历史某日起，逐个交易日"像每日执行一样"生成报告):
  python run.py --replay-since 2026-08-10
    · 依赖数据表已就绪(可先用 --backfill-since 补齐 price/width/congestion)。
    · 以库内 width∩congestion 的交易日为日历，从 since 起到最新，逐日执行:
        daily(--date d, 热力图 html) → trend(--calc-date d --skip-fetch) →
        combine(as_of_date=d)
      产出 action_report_{d}.md / *_heatmap.html / sw_trend_summary_{d}.csv /
      combined_{d}.md，与真实每日执行的产物一致。
    · 可选 --replay-until YYYY-MM-DD 限定回放终点(默认到最新交易日)。

推荐 (analysis-hub 的核心目的：下一交易日买哪些确定性稳、收益高的行业):
  python run.py --recommend                 # 最新交易日, 短档, best/默认过滤
  python run.py --recommend --date 2026-09-01 --tier long
    · 打分口径「稳中求进」(确定性为主, 收益弹性为辅), 产出 0~100 推荐指数与
      可解释分项推导链, 叠加硬性过滤, 精而少(允许空推荐)。
    · 整合报告 combined_*.md 的「一、下一交易日分析与建议」中的重点推荐子节即消费此引擎。

回测 (用历史前向收益给"推荐引擎+过滤组合"择优):
  python run.py --backtest --tier long  --since 2022-01-01 --write-best
  python run.py --backtest --tier short --since 2026-04-29
    · 逐交易日无前视现算特征 -> 各过滤组合推荐 -> D 之后价格算前向收益,
      比较超额/命中率/回撤/样本数, 选冠军过滤组合写 recommend_best.json。
"""

import os
import re
import sys
import subprocess
from datetime import datetime

import combine_report


HUB_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(HUB_DIR)
PYCACHE_DIR = os.path.join(REPO_DIR, '.pycache')

TOOLS = {
    'daily': {
        'dir': 'sw2-daily-report',
        'entry': 'daily_run.py',
        'desc': '申万二级行业每日量化日报',
    },
    'trend': {
        'dir': 'sw2-trend-analysis',
        'entry': 'main.py',
        'desc': '申万二级行业趋势分析',
    },
}

DEFAULT_ORDER = ['trend', 'daily']

GENERAL_DATA_DIR = os.path.join(REPO_DIR, 'general-data')
REFRESH_ENTRY = os.path.join(GENERAL_DATA_DIR, 'refresh.py')

# --no-csv 不加：二级数据也要产出 sw_trend_summary_*.csv 供 combine 三章消费
TREND_SECONDARY_ARGS = ['--skip-fetch', '--no-excel', '--recompute-days', '5']

RSS_ENTRY = os.path.join(HUB_DIR, 'generate_rss.py')
TREND_RSS_ENTRY = os.path.join(HUB_DIR, 'generate_trend_rss.py')
NOTIFY_ENTRY = os.path.join(HUB_DIR, 'wxpusher_notify.py')


def refresh_general_data(skip_valuation=False):
    """第一步：刷新 general-data 基础数据（industry/price/width/congestion/valuation）。

    在 general-data 目录下作为子进程运行 refresh.py。全部写入幂等：
    price 只补新日期，width/congestion/valuation 主键冲突原样保留，
    重复运行"新增 0 行"即代表已就绪，不重新赋同样的值。
    返回退出码 (0=就绪)。"""
    if not os.path.isfile(REFRESH_ENTRY):
        print('[hub] [ERROR] 找不到数据刷新入口: {}'.format(REFRESH_ENTRY))
        return 1

    cmd = [sys.executable, REFRESH_ENTRY]
    if skip_valuation:
        cmd.append('--no-valuation')

    print('-----------------------------------------------------------------')
    print('[hub] 第 0 步: 刷新 general-data 基础数据 (refresh.py)')
    print('-----------------------------------------------------------------')
    sys.stdout.flush()

    return subprocess.run(cmd, cwd=GENERAL_DATA_DIR, env=_child_env()).returncode


def list_tools():
    """打印已注册工具列表。"""
    print('已注册的分析工具:')
    for key in DEFAULT_ORDER:
        t = TOOLS[key]
        print('  {:<8} {}  ({}/{})'.format(key, t['desc'], t['dir'], t['entry']))


def run_tool(key, passthrough_args):
    """在工具自身目录下作为子进程运行，返回退出码。"""
    tool = TOOLS[key]
    cwd = os.path.join(REPO_DIR, tool['dir'])
    entry_path = os.path.join(cwd, tool['entry'])

    if not os.path.isfile(entry_path):
        print('  [ERROR] 找不到入口脚本: {}'.format(entry_path))
        return 1

    cmd = [sys.executable, tool['entry']] + passthrough_args

    print('-----------------------------------------------------------------')
    print('[hub] 运行 {} ({})'.format(key, tool['desc']))
    print('[hub] 目录: {}'.format(cwd))
    print('[hub] 命令: {}'.format(' '.join(cmd)))
    print('-----------------------------------------------------------------')
    sys.stdout.flush()

    child_env = dict(os.environ)
    child_env['PYTHONPYCACHEPREFIX'] = PYCACHE_DIR

    result = subprocess.run(cmd, cwd=cwd, env=child_env)
    return result.returncode


def run_hub_script(entry, args=None):
    """在 hub 目录下运行本目录内的分发脚本，返回退出码。"""
    cmd = [sys.executable, entry] + (args or [])

    print('[hub] 命令: {}'.format(' '.join(cmd)))
    sys.stdout.flush()

    return subprocess.run(cmd, cwd=HUB_DIR, env=_child_env()).returncode


def run_rss():
    """生成两份 RSS 订阅源（日报 + 趋势）。返回是否全部成功。

    RSS 文件是 CI 的提交物，属关键产物，失败计入主流程失败。"""
    ok = True
    for label, entry in (('日报', RSS_ENTRY), ('趋势分析', TREND_RSS_ENTRY)):
        print('-----------------------------------------------------------------')
        print('[hub] 分发: 生成{} RSS'.format(label))
        sys.stdout.flush()
        if run_hub_script(entry) != 0:
            ok = False
            print('[hub] [WARN] {} RSS 生成失败'.format(label))
    return ok


def run_notify():
    """WxPusher 微信推送最新日报。

    网络类非关键步骤：失败仅告警，不影响主流程退出码。"""
    print('-----------------------------------------------------------------')
    print('[hub] 分发: 微信推送 (WxPusher)')
    sys.stdout.flush()
    if run_hub_script(NOTIFY_ENTRY) != 0:
        print('[hub] [WARN] 微信推送失败（非关键步骤，不影响退出码）')


def _child_env():
    """子进程环境: 集中字节码缓存目录。"""
    env = dict(os.environ)
    env['PYTHONPYCACHEPREFIX'] = PYCACHE_DIR
    return env


def _trend_missing_calc_dates(since):
    """在趋势项目内查询 price 表中 >=since 但 trend 表尚缺的交易日列表(升序)。

    以趋势库 price 表的交易日为权威交易日历，返回需要回填 trend 快照的日期。
    在趋势工具自身目录下用其解释器执行，保持与主入口解耦。"""
    trend_dir = os.path.join(REPO_DIR, TOOLS['trend']['dir'])
    snippet = (
        'import db\n'
        'c = db.connect()\n'
        'ds = [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM price WHERE trade_date>=? ORDER BY trade_date", (%r,)).fetchall()]\n'
        'done = set(r[0] for r in c.execute("SELECT DISTINCT calc_date FROM trend").fetchall())\n'
        'c.close()\n'
        'print(\'\\n\'.join(d for d in ds if d not in done))\n'
    ) % since
    result = subprocess.run(
        [sys.executable, '-c', snippet],
        cwd=trend_dir, env=_child_env(),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    if result.returncode != 0:
        print('[hub] 查询趋势缺失日期失败:\n{}'.format(result.stderr.strip()))
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]


def _run_trend_calc_date(calc_date):
    """回填趋势某个基准日的 trend 快照(跳过联网、不导出，只落库)。"""
    trend = TOOLS['trend']
    cwd = os.path.join(REPO_DIR, trend['dir'])
    cmd = [
        sys.executable, trend['entry'],
        '--calc-date', calc_date,
        '--skip-fetch', '--no-excel', '--no-csv',
    ]
    result = subprocess.run(cmd, cwd=cwd, env=_child_env())
    return result.returncode


def backfill(since):
    """补齐 since 起两个工具的数据。返回退出码(0=全部成功)。"""
    print('=================================================================')
    print('[hub] 回填模式 | since={}'.format(since))
    print('=================================================================')

    any_failed = False

    # 0. 基础数据刷新（跳过估值，因为估值拉取最耗时且回填主要用于补齐库表）
    if refresh_general_data(skip_valuation=True) != 0:
        any_failed = True
        print('[hub] 基础数据刷新返回非零，库表可能未补全，仍继续后续步骤。')

    # 1/3. 日报常规运行
    print('-----------------------------------------------------------------')
    print('[hub] 1/3 日报: 常规运行(报告/热力图)')
    print('-----------------------------------------------------------------')
    sys.stdout.flush()
    if run_tool('daily', []) != 0:
        any_failed = True
        print('[hub] 日报运行返回非零，继续趋势回填(库表可能已部分补齐)。')

    # 2/3. 趋势计算（跳过联网、不导出）
    print('-----------------------------------------------------------------')
    print('[hub] 2/3 趋势: 计算(跳过联网拉取)')
    print('-----------------------------------------------------------------')
    sys.stdout.flush()
    trend = TOOLS['trend']
    if subprocess.run(
        [sys.executable, trend['entry'], '--skip-fetch', '--no-excel', '--no-csv'],
        cwd=os.path.join(REPO_DIR, trend['dir']), env=_child_env(),
    ).returncode != 0:
        any_failed = True
        print('[hub] 趋势计算返回非零，仍尝试按库内 price 回填缺失快照。')

    # 3/3. 查询并逐个回填缺失的 trend 快照
    missing = _trend_missing_calc_dates(since)

    print('-----------------------------------------------------------------')
    print('[hub] 3/3 趋势: 需回填 trend 快照 {} 个交易日'.format(len(missing)))
    if missing:
        print('[hub] 日期: {} ... {}'.format(missing[0], missing[-1]))
    print('-----------------------------------------------------------------')
    sys.stdout.flush()

    for i, d in enumerate(missing, 1):
        print('[hub]   [{}/{}] calc-date {}'.format(i, len(missing), d))
        sys.stdout.flush()
        if _run_trend_calc_date(d) != 0:
            any_failed = True
            print('[hub]   {} 回填失败'.format(d))

    print('=================================================================')
    print('[hub] 回填完成 | 趋势补齐 {} 个交易日 | {}'.format(
        len(missing), '有失败项' if any_failed else '全部成功'))
    print('=================================================================')

    return 1 if any_failed else 0


def _replay_trading_days(since, until=None):
    """返回回放用的交易日历(升序)：库内 width∩congestion 且 since<=d<=until 的日期。

    在日报项目自身目录下用其解释器查询 SQLite，避免跨目录 import。
    以 width 与 congestion 都存在的交易日为准(场景判断需要两者齐备)。"""
    daily_dir = os.path.join(REPO_DIR, TOOLS['daily']['dir'])
    until_expr = until if until else '9999-99-99'
    snippet = (
        'import db\n'
        'c = db.connect()\n'
        f'w = set(r[0] for r in c.execute("SELECT DISTINCT trade_date FROM width WHERE trade_date>=? AND trade_date<=?", ({since!r}, {until_expr!r})).fetchall())\n'
        f'g = set(r[0] for r in c.execute("SELECT DISTINCT trade_date FROM congestion WHERE trade_date>=? AND trade_date<=?", ({since!r}, {until_expr!r})).fetchall())\n'
        'c.close()\n'
        "print('\\n'.join(sorted(w & g)))\n"
    )
    result = subprocess.run(
        [sys.executable, '-c', snippet],
        cwd=daily_dir, env=_child_env(),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    if result.returncode != 0:
        print('[hub] 查询回放交易日失败:\n{}'.format(result.stderr.strip()))
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]


def replay(since, until=None):
    """从 since 起逐个交易日回放，模拟每日执行，生成各日报告。返回退出码。"""
    print('=================================================================')
    print('[hub] 回放模式 | since={}{}'.format(since, (' until={}'.format(until)) if until else ''))
    print('=================================================================')

    days = _replay_trading_days(since, until)
    if not days:
        print('[hub] 未找到可回放的交易日(检查数据表是否已回填 width/congestion)。')
        return 2

    print('[hub] 待回放交易日 {} 个: {} ... {}'.format(len(days), days[0], days[-1]))
    sys.stdout.flush()

    daily_dir = os.path.join(REPO_DIR, TOOLS['daily']['dir'])
    trend_dir = os.path.join(REPO_DIR, TOOLS['trend']['dir'])
    any_failed = False

    for i, d in enumerate(days, 1):
        print('\n#################################################################')
        print('[hub] 回放 [{}/{}] 交易日 {}'.format(i, len(days), d))
        print('#################################################################')
        sys.stdout.flush()

        daily_cmd = [sys.executable, TOOLS['daily']['entry'],
                     '--date', d, '--image', '--heatmap-format', 'html']
        rc_daily = subprocess.run(daily_cmd, cwd=daily_dir, env=_child_env()).returncode
        if rc_daily != 0:
            any_failed = True
            print('[hub]   日报回放返回非零({})，继续趋势/整合。'.format(rc_daily))

        trend_cmd = [sys.executable, TOOLS['trend']['entry'],
                     '--calc-date', d, '--skip-fetch', '--no-excel']
        rc_trend = subprocess.run(trend_cmd, cwd=trend_dir, env=_child_env()).returncode
        if rc_trend != 0:
            any_failed = True
            print('[hub]   趋势回放返回非零({})，仍尝试整合。'.format(rc_trend))

        try:
            combine_report.generate(
                tool_results={'daily': rc_daily, 'trend': rc_trend},
                as_of_date=d,
            )
        except Exception as e:
            any_failed = True
            print('[hub]   整合报告({})生成失败: {}'.format(d, e))

        sys.stdout.flush()

    print('\n=================================================================')
    print('[hub] 回放完成 | 共 {} 个交易日 | {}'.format(
        len(days), '有失败项' if any_failed else '全部成功'))
    print('=================================================================')

    return 1 if any_failed else 0


def parse_args(argv):
    """
    返回 (selected_keys, passthrough_args)。
    '--' 之后的所有参数透传给被选中的工具 (仅在选中单个工具时有意义)。
    """
    if '--' in argv:
        idx = argv.index('--')
        selectors = argv[:idx]
        passthrough = argv[idx + 1:]
    else:
        selectors = argv
        passthrough = []

    if not selectors:
        selected = list(DEFAULT_ORDER)
    else:
        selected = []
        for s in selectors:
            if s not in TOOLS:
                print('[hub] 未知工具: {}'.format(s))
                print('[hub] 可用: {}'.format(', '.join(DEFAULT_ORDER)))
                sys.exit(2)
            selected.append(s)

    if passthrough and len(selected) > 1:
        print("[hub] 警告: '--' 透传参数只应配合单个工具使用，当前选中 {} 个，参数将被传给每一个。".format(len(selected)))

    return selected, passthrough


def main():
    argv = sys.argv[1:]

    # --list / -l / list
    if argv and argv[0] in ('--list', '-l', 'list'):
        list_tools()
        return 0

    # --help / -h / help
    if argv and argv[0] in ('--help', '-h', 'help'):
        print(__doc__)
        return 0

    # --backfill-since DATE
    if '--backfill-since' in argv:
        idx = argv.index('--backfill-since')
        if idx + 1 >= len(argv):
            print('[hub] --backfill-since 需要一个日期参数 (YYYY-MM-DD)')
            return 2
        since = argv[idx + 1]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', since):
            print('[hub] 日期格式应为 YYYY-MM-DD，收到: {}'.format(since))
            return 2
        return backfill(since)

    # --replay-since DATE [--replay-until DATE]
    if '--replay-since' in argv:
        idx = argv.index('--replay-since')
        if idx + 1 >= len(argv):
            print('[hub] --replay-since 需要一个日期参数 (YYYY-MM-DD)')
            return 2
        since = argv[idx + 1]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', since):
            print('[hub] 日期格式应为 YYYY-MM-DD，收到: {}'.format(since))
            return 2
        until = None
        if '--replay-until' in argv:
            uidx = argv.index('--replay-until')
            if uidx + 1 >= len(argv):
                print('[hub] --replay-until 需要一个日期参数 (YYYY-MM-DD)')
                return 2
            until = argv[uidx + 1]
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', until):
                print('[hub] 日期格式应为 YYYY-MM-DD，收到: {}'.format(until))
                return 2
        return replay(since, until)

    # --backtest [args...]
    if '--backtest' in argv:
        bt_args = [a for a in argv if a != '--backtest']
        cmd = [sys.executable, os.path.join(HUB_DIR, 'backtest.py')] + bt_args
        print('[hub] 回测: {}'.format(' '.join(cmd)))
        sys.stdout.flush()
        return subprocess.run(cmd, cwd=HUB_DIR, env=_child_env()).returncode

    # --recommend [--no-refresh] [--no-trend]
    if '--recommend' in argv:
        rc_skip_refresh = '--no-refresh' in argv
        rc_skip_trend = '--no-trend' in argv
        rc_args = [a for a in argv
                   if a not in ('--recommend', '--no-refresh', '--no-trend')]

        if not rc_skip_refresh:
            if refresh_general_data(skip_valuation=True) != 0:
                print('[hub] [WARN] 基础数据刷新有失败项，推荐基于库内现有数据继续。')
        else:
            print('[hub] 跳过基础数据刷新 (--no-refresh)')

        if not rc_skip_trend:
            run_tool('trend', TREND_SECONDARY_ARGS)

        cmd = [sys.executable, os.path.join(HUB_DIR, 'recommend.py')] + rc_args
        print('[hub] 推荐: {}'.format(' '.join(cmd)))
        sys.stdout.flush()
        return subprocess.run(cmd, cwd=HUB_DIR, env=_child_env()).returncode

    # 正常流程 / combine / 分发
    no_combine = '--no-combine' in argv
    combine_only = '--combine-only' in argv
    argv = [a for a in argv if a not in ('--no-combine', '--combine-only')]

    # combine-only 模式：只跑 combine_report
    if combine_only:
        print('=================================================================')
        print('[hub] 仅生成整合报告（不运行基础工具）')
        print('=================================================================')
        combine_report.generate(tool_results=None)
        return 0

    rss_only = '--rss-only' in argv
    notify_only = '--notify-only' in argv

    # rss-only / notify-only 分发模式
    if rss_only or notify_only:
        print('=================================================================')
        print('[hub] 仅分发（RSS{}，不运行数据刷新/工具/整合）'.format('+微信推送' if notify_only else ''))
        print('=================================================================')
        any_failed = False
        if rss_only and not run_rss():
            any_failed = True
        if notify_only:
            run_notify()
        return 1 if any_failed else 0

    no_refresh = '--no-refresh' in argv
    argv = [a for a in argv if a != '--no-refresh']

    no_trend = '--no-trend' in argv
    argv = [a for a in argv if a != '--no-trend']

    no_rss = '--no-rss' in argv
    no_notify = '--no-notify' in argv
    argv = [a for a in argv if a not in ('--no-rss', '--notify', '--no-notify')]

    selected, passthrough = parse_args(argv)
    start = datetime.now()
    print('=================================================================')
    print('[hub] 分析主入口启动 | {}'.format(start.strftime('%Y-%m-%d %H:%M:%S')))
    print('[hub] 本次执行: {}'.format(' -> '.join(selected)))
    print('=================================================================')

    # 0. 刷新基础数据
    refresh_ok = True
    if not no_refresh:
        if refresh_general_data() != 0:
            refresh_ok = False
            print('[hub] [WARN] 基础数据刷新有失败项，后续步骤基于库内现有数据继续。')
    else:
        print('[hub] 跳过基础数据刷新 (--no-refresh)')

    results = {}

    # 0.5. 二级数据
    if no_trend:
        print('[hub] 跳过二级数据阶段 (--no-trend)')
    elif 'trend' in selected:
        print('[hub] 已显式选中 trend 工具，跳过第 1 步自动注入。')
    else:
        print('[hub] 第 1 步: 重算二级数据 trend/epsilon (--recompute-days 5)')
        sys.stdout.flush()
        results['trend(二级)'] = run_tool('trend', TREND_SECONDARY_ARGS)

    # 1. 运行选中的工具
    for key in selected:
        code = run_tool(key, passthrough)
        results[key] = code

    # 摘要
    print('=================================================================')
    print('[hub] 执行摘要')
    print('=================================================================')
    any_failed = not refresh_ok
    if not no_refresh:
        print('  {:<8} {}'.format('refresh', 'OK' if refresh_ok else 'FAIL'))
    for key, code in results.items():
        desc = TOOLS[key]['desc'] if key in TOOLS else '二级数据 trend/epsilon'
        status = 'OK' if code == 0 else 'FAIL (exit={})'.format(code)
        if code != 0:
            any_failed = True
        print('  {:<12} {:<28} {}'.format(key, desc, status))

    elapsed = (datetime.now() - start).total_seconds()
    print('[hub] 总耗时 {:.1f}s'.format(elapsed))

    # 2. 合并报告
    if not no_combine:
        print('-----------------------------------------------------------------')
        try:
            combine_report.generate(tool_results=results)
        except Exception as e:
            print('[hub] 整合报告生成失败: {}'.format(e))

    # 3. 分发
    if not no_rss:
        if not run_rss():
            any_failed = True
    else:
        print('[hub] 跳过 RSS 生成 (--no-rss)')

    if not no_notify:
        run_notify()

    return 1 if any_failed else 0


if __name__ == '__main__':
    sys.exit(main())
