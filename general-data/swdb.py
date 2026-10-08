"""general-data SQLite 数据访问层（全仓唯一基础数据库）。

合并自 sw2-daily-report/db.py 与 sw2-trend-analysis/db.py：
- industry  行业维表（daily）
- price     指数收盘全历史（daily + trend 共用，含 sw_name 冗余列）
- width     市场宽度（daily）
- congestion 行业拥挤度（daily）
- trend     趋势计算明细（trend）
- epsilon   死区阈值（trend）
- valuation 估值指标 PE/PB/股息率（申万官方日线，refresh.py 顺手刷新）

约定：
- sw_code 统一为纯数字 6 位（剥掉 .SI）。
- 基础表主键 (sw_code, trade_date)，INSERT OR IGNORE 增量幂等。
- trend/epsilon 每次计算 UPSERT 覆盖，同日重跑幂等。
- 线上 Actions 单进程顺序执行，SQLite 默认回滚日志，写完即落主库，git 直接提交。

各工具通过自身目录下的 db.py 薄壳（re-export 本模块）使用，
对外函数签名保持与合并前一致，下游逻辑零改动。
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'sw.db')

QUOTES_COLUMNS = ['代码', '名称', '一级行业', '成份数', '日期', '收盘价',
                  '涨跌幅(%)', '3日涨跌(%)', '5日涨跌(%)', '10日涨跌(%)',
                  '20日涨跌(%)', '成交量(亿手)', '成交额(亿元)']


def strip_si(code) -> str:
    """剥掉 .SI 后缀，返回纯数字代码。"""
    return str(code).replace('.SI', '').strip()


def to_full_code(code6: str) -> str:
    """纯数字代码 -> 带 .SI 后缀(重建给下游，保持与旧 CSV 一致)。"""
    return '{}.SI'.format(code6)


def is_valid_code(code6: str) -> bool:
    """合法的 6 位数字行业代码(过滤 avg 汇总行等)。"""
    return bool(re.match(r'^\d{6}$', str(code6)))


def calc_n_chg(closes, n):
    """N 日涨跌幅。closes 按时间升序。"""
    if len(closes) <= n:
        return None
    return round(
        (closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100,
        2,
    )


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """打开数据库连接，确保目录存在。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """创建全部表(幂等，可重复调用)。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS industry (
            sw_code      TEXT PRIMARY KEY,
            sw_name      TEXT NOT NULL,
            sw1_name     TEXT,
            member_count INTEGER,
            row_ord      INTEGER,
            updated_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS price (
            sw_code    TEXT NOT NULL,
            sw_name    TEXT,
            trade_date TEXT NOT NULL,
            close      REAL NOT NULL,
            volume     REAL,
            amount     REAL,
            PRIMARY KEY (sw_code, trade_date)
        );

        CREATE TABLE IF NOT EXISTS width (
            sw_code    TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            value20    INTEGER NOT NULL,
            PRIMARY KEY (sw_code, trade_date)
        );

        CREATE TABLE IF NOT EXISTS congestion (
            sw_code                    TEXT NOT NULL,
            trade_date                 TEXT NOT NULL,
            turnover_rate_f_quantile   REAL,
            amount_congestion_quantile REAL,
            PRIMARY KEY (sw_code, trade_date)
        );

        CREATE TABLE IF NOT EXISTS trend (
            sw_code     TEXT NOT NULL,
            sw_name     TEXT,
            calc_date   TEXT NOT NULL,
            window      INTEGER NOT NULL,
            ma          REAL,
            slope       REAL,
            slope_norm  REAL,
            r_squared   REAL,
            price_vs_ma REAL,
            epsilon     REAL,
            direction   TEXT,
            created_at  TEXT,
            PRIMARY KEY (sw_code, calc_date, window)
        );

        CREATE TABLE IF NOT EXISTS epsilon (
            sw_code    TEXT NOT NULL,
            window     INTEGER NOT NULL,
            epsilon    REAL NOT NULL,
            quantile   REAL,
            source     TEXT DEFAULT 'probe',
            updated_at TEXT,
            PRIMARY KEY (sw_code, window)
        );

        CREATE TABLE IF NOT EXISTS valuation (
            sw_code       TEXT NOT NULL,
            trade_date    TEXT NOT NULL,
            pe            REAL,
            pb            REAL,
            dp            REAL,
            close         REAL,
            turnover_rate REAL,
            PRIMARY KEY (sw_code, trade_date)
        );
        """)
    conn.commit()


def _insert_ignore(conn, table, cols, placeholders, rows) -> int:
    """通用 INSERT OR IGNORE 批量写入，返回新增行数。"""
    rows = list(rows)
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        'INSERT OR IGNORE INTO {} {} VALUES {}'.format(table, cols, placeholders),
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def upsert_industry(conn, sw_code, sw_name, sw1_name=None, member_count=None,
                    row_ord=None) -> None:
    """写入/更新行业维表。已有非空字段不会被 None 覆盖(用 COALESCE 保护)。

    不在此处 commit：常在逐行循环中调用，由调用方统一提交。
    """
    now = datetime.now().isoformat(timespec='seconds')
    conn.execute("""
        INSERT INTO industry (sw_code, sw_name, sw1_name, member_count, row_ord, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(sw_code) DO UPDATE SET
            sw_name      = excluded.sw_name,
            sw1_name     = COALESCE(excluded.sw1_name, industry.sw1_name),
            member_count = COALESCE(excluded.member_count, industry.member_count),
            row_ord      = COALESCE(excluded.row_ord, industry.row_ord),
            updated_at   = excluded.updated_at
        """,
        (sw_code, sw_name, sw1_name, member_count, row_ord, now),
    )


def _industry_maps(conn):
    """返回 (name_map, sw1_map, member_map, ord_map)，键为纯数字 code。"""
    rows = conn.execute(
        'SELECT sw_code, sw_name, sw1_name, member_count, row_ord FROM industry'
    ).fetchall()
    name_map, sw1_map, member_map, ord_map = {}, {}, {}, {}
    for r in rows:
        name_map[r['sw_code']] = r['sw_name']
        sw1_map[r['sw_code']] = r['sw1_name']
        member_map[r['sw_code']] = r['member_count']
        ord_map[r['sw_code']] = r['row_ord'] if r['row_ord'] is not None else 999999
    return name_map, sw1_map, member_map, ord_map


def upsert_width_from_api(conn, data: dict) -> int:
    """从乐咕 API dict 写 width(宽转长)。返回新增行数。"""
    dates = data.get('dates', [])
    code_names = data.get('swCodeNames', [])
    ma = data.get('maMarketWidth', {})
    rows = []
    for ordi, item in enumerate(code_names):
        raw_code = item['indexCode']
        code6 = strip_si(raw_code)
        name = item['indexName']
        if not is_valid_code(code6):
            continue
        upsert_industry(conn, code6, name, row_ord=ordi)
        values = ma.get(raw_code, [])
        for i, d in enumerate(dates):
            if not i < len(values):
                continue
            v = values[i]
            val = v.get('value20', 0) if isinstance(v, dict) else v or 0
            rows.append((code6, d, int(val) if val else 0))
    return _insert_ignore(
        conn, 'width',
        '(sw_code, trade_date, value20)', '(?, ?, ?)',
        rows,
    )


def upsert_congestion_from_api(conn, data: dict) -> int:
    """从乐咕 API dict 写 congestion(宽转长，每日两指标)。返回新增行数。"""
    dates = data.get('dates', [])
    code_names = data.get('swCodeNames', [])
    cong = data.get('congestions', {})
    rows = []
    for ordi, item in enumerate(code_names):
        raw_code = item['indexCode']
        code6 = strip_si(raw_code)
        name = item['indexName']
        if not is_valid_code(code6):
            continue
        upsert_industry(conn, code6, name, row_ord=ordi)
        values = cong.get(raw_code, [])
        for i, d in enumerate(dates):
            if not i < len(values):
                continue
            v = values[i]
            if isinstance(v, dict):
                t = v.get('turnoverRateFQuantile', 0) or 0
                a = v.get('amountCongestionQuantile', 0) or 0
            else:
                t, a = (0, 0)
            rows.append((
                code6, d,
                round(float(t), 1),
                round(float(a), 1),
            ))
    return _insert_ignore(
        conn, 'congestion',
        '(sw_code, trade_date, turnover_rate_f_quantile, amount_congestion_quantile)',
        '(?, ?, ?, ?)',
        rows,
    )


def upsert_price_series(conn, code6: str, series_rows: Iterable[tuple]) -> int:
    """写整段历史收盘序列（daily-report 口径）。

    series_rows 每项 (trade_date, close, volume, amount)。
    sw_name 从 industry 维表补齐（缺失则 NULL）。
    """
    row = conn.execute(
        'SELECT sw_name FROM industry WHERE sw_code = ?',
        (str(code6),),
    ).fetchone()
    sw_name = row['sw_name'] if row else None
    rows = [(str(code6), sw_name, d, c, v, a) for d, c, v, a in series_rows]
    return _insert_ignore(
        conn, 'price',
        '(sw_code, sw_name, trade_date, close, volume, amount)',
        '(?, ?, ?, ?, ?, ?)',
        rows,
    )


def upsert_prices(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    """批量写入点位（trend 口径）。

    rows 每项 (sw_code, sw_name, trade_date, close)。返回新增行数。
    """
    rows = list(rows)
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        'INSERT OR IGNORE INTO price (sw_code, sw_name, trade_date, close) VALUES (?, ?, ?, ?)',
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def upsert_valuation_rows(conn, rows: Iterable[tuple]) -> int:
    """批量写估值指标。rows 每项 (sw_code, trade_date, pe, pb, dp, close, turnover_rate)。"""
    return _insert_ignore(
        conn, 'valuation',
        '(sw_code, trade_date, pe, pb, dp, close, turnover_rate)',
        '(?, ?, ?, ?, ?, ?, ?)',
        rows,
    )


def get_max_trade_date(conn: sqlite3.Connection, sw_code: str) -> Optional[str]:
    """返回某板块在 price 中的最新交易日；无则 None（用于逐板块首灌判断）。"""
    row = conn.execute(
        'SELECT MAX(trade_date) AS d FROM price WHERE sw_code = ?',
        (sw_code,),
    ).fetchone()
    return row['d'] if row and row['d'] else None


def get_recent_trade_dates(
    conn: sqlite3.Connection, limit: int, as_of_date: str | None = None,
) -> list[str]:
    """返回 price 全表最近 limit 个不同交易日，升序（全市场共享同一交易日历）。

    as_of_date=None 时取到最新；否则只取 trade_date <= as_of_date。
    用于二级数据（trend）逐日重算时确定基准日列表。
    """
    if as_of_date:
        rows = conn.execute(
            'SELECT DISTINCT trade_date FROM price WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?',
            (as_of_date, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT DISTINCT trade_date FROM price ORDER BY trade_date DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [r['trade_date'] for r in reversed(rows)]


def get_recent_prices(
    conn: sqlite3.Connection, sw_code: str, limit: int,
    as_of_date: str | None = None,
) -> list[sqlite3.Row]:
    """取某板块最近 limit 个交易日的点位，按 trade_date 升序返回（便于回归）。

    as_of_date=None 时取到最新交易日；否则只取 trade_date <= as_of 的最近 limit 天。
    """
    if as_of_date:
        rows = conn.execute(
            'SELECT sw_code, sw_name, trade_date, close FROM price '
            'WHERE sw_code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?',
            (sw_code, as_of_date, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT sw_code, sw_name, trade_date, close FROM price '
            'WHERE sw_code = ? ORDER BY trade_date DESC LIMIT ?',
            (sw_code, limit),
        ).fetchall()
    return list(reversed(rows))


def get_all_prices(conn: sqlite3.Connection, sw_code: str) -> list[sqlite3.Row]:
    """取某板块全部历史点位，按 trade_date 升序（探针用）。"""
    return conn.execute(
        'SELECT sw_code, sw_name, trade_date, close FROM price WHERE sw_code = ? ORDER BY trade_date ASC',
        (sw_code,),
    ).fetchall()


def get_latest_snapshot(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """取每个板块最新交易日的点位快照（Excel 点位 sheet 用）。"""
    return conn.execute("""
        SELECT p.sw_code, p.sw_name, p.trade_date, p.close
        FROM price p
        JOIN (
            SELECT sw_code, MAX(trade_date) AS md FROM price GROUP BY sw_code
        ) m ON p.sw_code = m.sw_code AND p.trade_date = m.md
        ORDER BY p.sw_code
        """).fetchall()


def upsert_trends(conn: sqlite3.Connection, rows: Iterable[dict]) -> None:
    """UPSERT 计算明细，主键 (sw_code, calc_date, window) 冲突则覆盖（幂等）。"""
    rows = list(rows)
    if not rows:
        return None
    conn.executemany("""
        INSERT INTO trend (sw_code, sw_name, calc_date, window, ma, slope,
                           slope_norm, r_squared, price_vs_ma, epsilon,
                           direction, created_at)
        VALUES (:sw_code, :sw_name, :calc_date, :window, :ma, :slope,
                :slope_norm, :r_squared, :price_vs_ma, :epsilon,
                :direction, :created_at)
        ON CONFLICT(sw_code, calc_date, window) DO UPDATE SET
            sw_name     = excluded.sw_name,
            ma          = excluded.ma,
            slope       = excluded.slope,
            slope_norm  = excluded.slope_norm,
            r_squared   = excluded.r_squared,
            price_vs_ma = excluded.price_vs_ma,
            epsilon     = excluded.epsilon,
            direction   = excluded.direction,
            created_at  = excluded.created_at
        """, rows)
    conn.commit()


def get_trends_by_date(conn: sqlite3.Connection, calc_date: str) -> list[sqlite3.Row]:
    """取某计算基准日的全部明细（Excel 导出用）。"""
    return conn.execute(
        'SELECT * FROM trend WHERE calc_date = ? ORDER BY window, sw_code',
        (calc_date,),
    ).fetchall()


def upsert_epsilon(
    conn: sqlite3.Connection,
    sw_code: str,
    window: int,
    epsilon: float,
    quantile: float,
    source: str = 'probe',
    protect_manual: bool = True,
) -> None:
    """写入/更新单个 (板块,窗口) 的 ε。protect_manual=True 时探针不覆盖 manual 行。"""
    now = datetime.now().isoformat(timespec='seconds')
    if protect_manual and source == 'probe':
        existing = conn.execute(
            'SELECT source FROM epsilon WHERE sw_code = ? AND window = ?',
            (sw_code, window),
        ).fetchone()
        if existing and existing['source'] == 'manual':
            return None
    conn.execute("""
        INSERT INTO epsilon (sw_code, window, epsilon, quantile, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(sw_code, window) DO UPDATE SET
            epsilon    = excluded.epsilon,
            quantile   = excluded.quantile,
            source     = excluded.source,
            updated_at = excluded.updated_at
        """,
        (sw_code, window, epsilon, quantile, source, now),
    )
    conn.commit()


def get_epsilon_map(conn: sqlite3.Connection) -> dict[tuple[str, int], float]:
    """取全部 ε，返回 {(sw_code, window): epsilon} 便于主流程 O(1) 查表。"""
    rows = conn.execute(
        'SELECT sw_code, window, epsilon FROM epsilon'
    ).fetchall()
    return {(r['sw_code'], r['window']): r['epsilon'] for r in rows}


def get_valuation_series(conn, sw_code: str, as_of_date: Optional[str] = None,
                         lookback_years: Optional[int] = None) -> list[sqlite3.Row]:
    """取某板块估值序列（升序）。可按 as_of 截断、按回看年数限窗。"""
    q = 'SELECT trade_date, pe, pb, dp, close, turnover_rate FROM valuation WHERE sw_code = ?'
    args = [str(sw_code)]
    if as_of_date:
        q += ' AND trade_date <= ?'
        args.append(as_of_date)
    if lookback_years:
        if as_of_date:
            start_year = int(as_of_date[:4]) - int(lookback_years)
        else:
            start_year = datetime.now().year - int(lookback_years)
        q += ' AND trade_date >= ?'
        args.append('{}-01-01'.format(start_year))
    q += ' ORDER BY trade_date ASC'
    return conn.execute(q, args).fetchall()


def _price_trading_dates(conn, as_of_date=None):
    """price 全表交易日集合（权威交易日历）。

    width/congestion 是逐交易日指标，乐咕在非交易日/盘前会预填当日行
    （无真实行情），凡不在 price 日历内的日期一律视为无效。
    price 为空时返回 None（无法校验，保持原样）。
    """
    try:
        sql = 'SELECT DISTINCT trade_date FROM price'
        args = ()
        if as_of_date:
            sql += ' WHERE trade_date <= ?'
            args = (as_of_date,)
        dates = {r['trade_date'] for r in conn.execute(sql, args).fetchall()}
        return dates or None
    except Exception:
        return None


def build_width_dict(as_of_date: Optional[str] = None, conn=None) -> dict:
    """返回与 action_report._parse_width_df 完全一致的结构。

    as_of_date=None 时取全历史；否则只保留 trade_date <= as_of 的交易日。
    """
    own = conn is None
    if own:
        conn = connect()

    try:
        if as_of_date:
            dates = [r['trade_date'] for r in conn.execute(
                'SELECT DISTINCT trade_date FROM width WHERE trade_date <= ? ORDER BY trade_date ASC',
                (as_of_date,),
            )]
        else:
            dates = [r['trade_date'] for r in conn.execute(
                'SELECT DISTINCT trade_date FROM width ORDER BY trade_date ASC',
            )]
        cal = _price_trading_dates(conn, as_of_date)
        if cal:
            dates = [d for d in dates if d in cal]

        name_map, _, _, ord_map = _industry_maps(conn)

        codes = [r['sw_code'] for r in conn.execute('SELECT DISTINCT sw_code FROM width')]
        codes.sort(key=lambda c: (ord_map.get(c, 999999), c))

        date_set = set(dates)
        val = {}
        for r in conn.execute('SELECT sw_code, trade_date, value20 FROM width'):
            if r['trade_date'] in date_set:
                val.setdefault(r['sw_code'], {})[r['trade_date']] = r['value20']

        data = {
            'dates': list(dates),
            'swCodeNames': [],
            'maMarketWidth': {},
        }
        for code6 in codes:
            full = to_full_code(code6)
            name = name_map.get(code6, '')
            data['swCodeNames'].append({'indexCode': full, 'indexName': name})
            per = val.get(code6, {})
            data['maMarketWidth'][full] = [{'value20': int(per.get(d, 0))} for d in dates]

        return data
    finally:
        if own:
            conn.close()


def build_congestion_dict(as_of_date: Optional[str] = None, conn=None) -> dict:
    """返回与 action_report._parse_congestion_df 完全一致的结构。"""
    own = conn is None
    if own:
        conn = connect()

    try:
        if as_of_date:
            dates = [r['trade_date'] for r in conn.execute(
                'SELECT DISTINCT trade_date FROM congestion WHERE trade_date <= ? ORDER BY trade_date ASC',
                (as_of_date,),
            )]
        else:
            dates = [r['trade_date'] for r in conn.execute(
                'SELECT DISTINCT trade_date FROM congestion ORDER BY trade_date ASC',
            )]
        cal = _price_trading_dates(conn, as_of_date)
        if cal:
            dates = [d for d in dates if d in cal]

        name_map, _, _, ord_map = _industry_maps(conn)

        codes = [r['sw_code'] for r in conn.execute('SELECT DISTINCT sw_code FROM congestion')]
        codes.sort(key=lambda c: (ord_map.get(c, 999999), c))

        date_set = set(dates)
        val = {}
        for r in conn.execute(
            'SELECT sw_code, trade_date, turnover_rate_f_quantile, amount_congestion_quantile FROM congestion'
        ):
            if r['trade_date'] in date_set:
                val.setdefault(r['sw_code'], {})[r['trade_date']] = (
                    r['turnover_rate_f_quantile'], r['amount_congestion_quantile'],
                )

        data = {
            'dates': list(dates),
            'swCodeNames': [],
            'congestions': {},
        }
        for code6 in codes:
            full = to_full_code(code6)
            name = name_map.get(code6, '')
            data['swCodeNames'].append({'indexCode': full, 'indexName': name})
            per = val.get(code6, {})
            seq = []
            for d in dates:
                t, a = per.get(d, (0, 0))
                seq.append({
                    'turnoverRateFQuantile': float(t) if t is not None else 0,
                    'amountCongestionQuantile': float(a) if a is not None else 0,
                })
            data['congestions'][full] = seq

        return data
    finally:
        if own:
            conn.close()


def build_quotes_df(as_of_date: Optional[str] = None, conn=None) -> pd.DataFrame:
    """返回与旧 quotes CSV 逐列一致的 DataFrame，涨跌幅用收盘序列现算。

    as_of_date=None 时取每个 code 的最新交易日；否则取 <= as_of 的最大交易日。
    """
    own = conn is None
    if own:
        conn = connect()

    try:
        name_map, sw1_map, member_map, _ = _industry_maps(conn)
        series = {}
        q = 'SELECT sw_code, trade_date, close, volume, amount FROM price {} ORDER BY sw_code, trade_date ASC'

        if as_of_date:
            cur = conn.execute(q.format('WHERE trade_date <= ?'), (as_of_date,))
        else:
            cur = conn.execute(q.format(''))

        for r in cur:
            series.setdefault(r['sw_code'], []).append((
                r['trade_date'], r['close'], r['volume'], r['amount'],
            ))

        rows = []
        for code6, seq in series.items():
            if not seq:
                continue
            closes = [s[1] for s in seq]
            last_date, last_close, last_vol, last_amt = seq[-1]

            def _chg(n):
                v = calc_n_chg(closes, n)
                return v if v is not None else '-'

            rows.append({
                '代码': to_full_code(code6),
                '名称': name_map.get(code6, ''),
                '一级行业': sw1_map.get(code6, ''),
                '成份数': member_map.get(code6, ''),
                '日期': last_date,
                '收盘价': round(last_close, 2) if last_close is not None else '-',
                '涨跌幅(%)': _chg(1),
                '3日涨跌(%)': _chg(3),
                '5日涨跌(%)': _chg(5),
                '10日涨跌(%)': _chg(10),
                '20日涨跌(%)': _chg(20),
                '成交量(亿手)': round(last_vol, 2) if last_vol is not None else '-',
                '成交额(亿元)': round(last_amt, 2) if last_amt is not None else '-',
            })

        if not rows:
            return pd.DataFrame(columns=QUOTES_COLUMNS)

        df = pd.DataFrame(rows)[QUOTES_COLUMNS]
        return df.reset_index(drop=True)
    finally:
        if own:
            conn.close()


def latest_trade_date(table: str, conn=None) -> Optional[str]:
    """某表的最新交易日(width/congestion/price/valuation)。"""
    own = conn is None
    if own:
        conn = connect()
    try:
        row = conn.execute(
            'SELECT MAX(trade_date) AS d FROM {}'.format(table),
        ).fetchone()
        return row['d'] if row and row['d'] else None
    finally:
        if own:
            conn.close()
