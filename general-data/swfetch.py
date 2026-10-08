"""general-data 数据获取层（全仓唯一的数据抓取入口）。

职责：只负责调用外部 API 拉取数据并标准化，写库复用 swdb。
分析工具(sw2-daily-report / sw2-trend-analysis)不再自带抓取代码，
一律 import 本模块；refresh.py 统一调度刷新。

数据源：
- 申万二级行业列表:   ak.sw_index_second_info()
- 指数日线收盘:       ak.index_hist_sw()（带重试/校验）
- 市场宽度:           legulegu /api/stockdata/member-ship/ma-market-width
- 行业拥挤度:         legulegu /api/stockdata/sw-congestion
- 估值指标(PE/PB/股息率): ak.index_analysis_daily_sw()（申万宏源官网）
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd

import swdb

try:
    import akshare as ak
    import requests
except ImportError as exc:
    raise SystemExit('缺少依赖 akshare/requests，请先 pip install -r requirements.txt') from exc

HIST_PERIOD = 'day'
FETCH_MAX_RETRY = 3
FETCH_RETRY_BACKOFF = 2.0
FETCH_SLEEP = 0.3
MIN_VALID_CLOSE = 0.0
MAX_DAILY_CHANGE = 0.5


def normalize_code(raw_code: str) -> str:
    """把 `801012.SI` / `801012` 统一成纯数字 6 位字符串。"""
    return str(raw_code).strip().split('.')[0]


def get_second_industries_df() -> pd.DataFrame:
    """取申万二级行业列表原始 DataFrame（含行业代码/名称/上级行业/成份个数）。"""
    return ak.sw_index_second_info()


def get_second_industries() -> list[dict]:
    """取申万二级行业列表，返回 [{'sw_code','sw_name'}, ...]（代码已剥 .SI）。"""
    df = get_second_industries_df()
    code_col = '行业代码' if '行业代码' in df.columns else df.columns[0]
    name_col = '行业名称' if '行业名称' in df.columns else df.columns[1]
    out = []
    for _, row in df.iterrows():
        code = normalize_code(row[code_col])
        name = str(row[name_col]).strip()
        if not code:
            continue
        out.append({'sw_code': code, 'sw_name': name})
    return out


def fetch_hist_raw(sw_code: str) -> pd.DataFrame:
    """调用 index_hist_sw 拉全量历史（原始列），带重试与退避。"""
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        try:
            df = ak.index_hist_sw(symbol=sw_code, period=HIST_PERIOD)
            if df is None or df.empty:
                raise ValueError('接口返回空数据')
            return df
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_RETRY_BACKOFF * attempt)
    raise RuntimeError(
        f'拉取 {sw_code} 失败（重试 {FETCH_MAX_RETRY} 次）: {last_err}'
    )


def clean_hist(df: pd.DataFrame, sw_code: str, sw_name: str) -> pd.DataFrame:
    """把原始返回标准化为 (sw_code, sw_name, trade_date, close) 并做校验。"""
    date_col = '日期' if '日期' in df.columns else 'date'
    close_col = '收盘' if '收盘' in df.columns else 'close'
    out = pd.DataFrame({
        'sw_code': sw_code,
        'sw_name': sw_name,
        'trade_date': pd.to_datetime(df[date_col]).dt.strftime('%Y-%m-%d'),
        'close': pd.to_numeric(df[close_col], errors='coerce'),
    })
    out = out.dropna(subset=['trade_date', 'close'])
    out = out[out['close'] > MIN_VALID_CLOSE]
    out = out.drop_duplicates(subset=['trade_date']).sort_values('trade_date')
    if len(out) > 1:
        pct = out['close'].pct_change().abs()
        keep = pct.isna() | (pct <= MAX_DAILY_CHANGE)
        out = out[keep]
    return out.reset_index(drop=True)


def fetch_and_store_price(conn, sw_code: str, sw_name: str,
                          verbose: bool = True) -> dict:
    """拉取单板块 → 校验 → 增量写 price。返回统计 dict。"""
    max_before = swdb.get_max_trade_date(conn, sw_code)
    try:
        raw = fetch_hist_raw(sw_code)
        cleaned = clean_hist(raw, sw_code, sw_name)
        if max_before:
            cleaned = cleaned[cleaned['trade_date'] > max_before]
        rows = list(cleaned[['sw_code', 'sw_name', 'trade_date', 'close']].itertuples(
            index=False, name=None))
        inserted = swdb.upsert_prices(conn, rows)
        if verbose:
            mode = '首灌' if not max_before else '增量'
            print(f'  [{mode}] {sw_code} {sw_name}: 校验后 {len(cleaned)} 行，新增 {inserted} 行')
        return {
            'sw_code': sw_code, 'sw_name': sw_name,
            'fetched': len(cleaned), 'inserted': inserted,
            'max_date_before': max_before, 'status': 'ok',
        }
    except RuntimeError as err:
        if verbose:
            print(f'  [WARN] {sw_code} {sw_name}: {err}')
        return {
            'sw_code': sw_code, 'sw_name': sw_name,
            'fetched': 0, 'inserted': 0,
            'max_date_before': max_before, 'status': 'fetch_failed',
        }


def _lg_date_str(d=None) -> str:
    """把 date/str/None 统一成 YYYY-MM-DD 字符串（None 默认今天）。"""
    if d is None:
        d = date.today()
    elif isinstance(d, str):
        return d
    return d.strftime('%Y-%m-%d')


def _lg_md5(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _lg_token(d=None) -> str:
    date_str = _lg_date_str(d)
    return _lg_md5(date_str)


# — 乐咕请求基础设施 —

_session = requests.Session()
_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://legulegu.com/',
}


def _lg_request(url: str, params: dict) -> dict:
    if 'token' not in params:
        params['token'] = _lg_token()

    query_parts = ['{}={}'.format(k, v) for k, v in params.items()]
    full_url = '{}?{}'.format(url, '&'.join(query_parts))
    print('[URL] {}'.format(full_url))

    try:
        _session.get('https://legulegu.com/', headers=_headers, timeout=10)
    except Exception:
        pass
    response = _session.get(full_url, headers=_headers, timeout=30)
    if response.status_code == 200 and response.text.strip():
        try:
            return response.json()
        except Exception as e:
            return {'error': str(e)}
    return {'error': 'HTTP {}'.format(response.status_code)}


def fetch_width(days_back: int = 30, level: int = 2,
                ma_type: str = 'value20', verbose: bool = True) -> dict:
    """获取均线市场宽度 API dict（不写库，由调用方决定入库/落 CSV）。"""
    url = 'https://legulegu.com/api/stockdata/member-ship/ma-market-width'
    today = date.today()
    start_date = today - timedelta(days=days_back + 10)
    params = {
        'level': level,
        'startDate': _lg_date_str(start_date),
        'endDate': _lg_date_str(today),
        'previous': False,
        'next': False,
        'maType': ma_type,
        'severalTradeDays': days_back,
    }
    return _lg_request(url, params)


def fetch_congestion(days_back: int = 30, level: int = 2,
                     verbose: bool = True) -> dict:
    """获取行业拥挤度 API dict（不写库，由调用方决定入库/落 CSV）。"""
    url = 'https://legulegu.com/api/stockdata/sw-congestion'
    params = {
        'level': level,
        'severalTradeDays': days_back,
    }
    return _lg_request(url, params)


def fetch_and_store_width(conn, days_back: int = 30, level: int = 2) -> int:
    """拉取市场宽度并写库。返回新增行数。"""
    data = fetch_width(days_back=days_back, level=level)
    if 'error' in data:
        raise RuntimeError('width API 错误: {}'.format(data['error']))
    return swdb.upsert_width_from_api(conn, data)


def fetch_and_store_congestion(conn, days_back: int = 30, level: int = 2) -> int:
    """拉取行业拥挤度并写库。返回新增行数。"""
    data = fetch_congestion(days_back=days_back, level=level)
    if 'error' in data:
        raise RuntimeError('congestion API 错误: {}'.format(data['error']))
    return swdb.upsert_congestion_from_api(conn, data)


VALUATION_SYMBOLS = ('市场表征', '一级行业', '二级行业', '风格指数')


def fetch_valuation(symbol: str = '二级行业', start_date: str = '',
                    end_date: str = '') -> pd.DataFrame:
    """拉取申万指数估值日线。

    symbol ∈ {"市场表征","一级行业","二级行业","风格指数"}；
    start_date/end_date 格式 YYYYMMDD。
    返回列：指数代码/指数名称/发布日期/市盈率/市净率/股息率/收盘指数/换手率。
    """
    if symbol not in VALUATION_SYMBOLS:
        raise ValueError('symbol 须为 {}'.format(VALUATION_SYMBOLS))
    try:
        return ak.index_analysis_daily_sw(
            symbol=symbol, start_date=start_date, end_date=end_date
        )
    except KeyError:
        # akshare 在查询窗口无数据时内部直接抛 KeyError('发布日期')（上游缺陷），
        # 视为空结果，交由上层空表防护处理。
        return pd.DataFrame()


def fetch_and_store_valuation(conn, start_date: str, end_date: str,
                              symbol: str = '二级行业',
                              chunk_years: int = 1) -> int:
    """按年分块拉取估值并写库（避免单次请求页数过多）。返回新增行数。

    建议一次性回填用 (start,end) 跨年区间；每日增量传同一天即可。
    """
    total = 0
    y_start, y_end = int(start_date[:4]), int(end_date[:4])
    for y in range(y_start, y_end + 1):
        cs = max('{}0101'.format(y), start_date)
        ce = min('{}1231'.format(y), end_date)
        df = fetch_valuation(symbol=symbol, start_date=cs, end_date=ce)
        if df is None or df.empty:
            continue
        rows = []
        for _, r in df.iterrows():
            code6 = normalize_code(r['指数代码'])
            if not swdb.is_valid_code(code6):
                continue
            rows.append((
                code6,
                str(r['发布日期'])[:10],
                pd.to_numeric(r['市盈率'], errors='coerce'),
                pd.to_numeric(r['市净率'], errors='coerce'),
                pd.to_numeric(r['股息率'], errors='coerce'),
                pd.to_numeric(r['收盘指数'], errors='coerce'),
                pd.to_numeric(r['换手率'], errors='coerce'),
            ))
        total += swdb.upsert_valuation_rows(conn, rows)
        print('  [估值] {} 年: {} 行 (累计新增 {})'.format(y, len(rows), total))
    return total
