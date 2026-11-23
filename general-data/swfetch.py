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
- 大盘指数日线:       东方财富 push2his kline 接口
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timedelta
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


INDEX_KLINE_URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'

# — 东财 push2his 风控应对 —
# 数据中心 IP（CI/Azure 段）会概率性触发风控断连（RemoteDisconnected）；
# 同域名同指纹重试无效，必须换域名 + 浏览器指纹 + 分钟级退避跨过风控窗口。
EM_KLINE_HOSTS = (
    'push2his.eastmoney.com',
    '1.push2his.eastmoney.com',
    '38.push2his.eastmoney.com',
)
EM_RETRY_BACKOFF = (10.0, 30.0, 60.0)
_em_headers = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0.0.0 Safari/537.36'),
    'Accept': '*/*',
    'Referer': 'https://quote.eastmoney.com/',
}


def _em_kline_request(params: dict) -> list:
    """东财 kline 接口请求：每次重试轮换域名 + 分钟级退避。

    返回 klines 字符串列表；全部失败抛最后一次异常（由调用方包装文案）。"""
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        host = EM_KLINE_HOSTS[(attempt - 1) % len(EM_KLINE_HOSTS)]
        try:
            r = requests.get(
                'https://{}/api/qt/stock/kline/get'.format(host),
                params=params, headers=_em_headers, timeout=15)
            r.raise_for_status()
            return (r.json().get('data') or {}).get('klines') or []
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                backoff = EM_RETRY_BACKOFF[min(attempt - 1, len(EM_RETRY_BACKOFF) - 1)]
                time.sleep(backoff)
    raise last_err if last_err else RuntimeError('东财 kline 请求失败')


def fetch_index_daily(symbol: str, beg: str, end: str) -> list:
    """拉取大盘指数日线收盘（东方财富 kline 接口），返回 [(trade_date, close)] 升序。

    symbol: 'sh000001'=上证指数 / 'sz399001'=深证成指；beg/end: 'YYYY-MM-DD'。"""
    secid = ('1.' if symbol.startswith('sh') else '0.') + symbol[2:]
    try:
        klines = _em_kline_request(
            {'secid': secid, 'klt': '101', 'fqt': '1',
             'beg': beg.replace('-', ''), 'end': end.replace('-', ''),
             'fields1': 'f1,f2,f3,f4,f5', 'fields2': 'f51,f53'})
    except Exception as err:
        raise RuntimeError(
            f'拉取指数 {symbol} 失败（重试 {FETCH_MAX_RETRY} 次）: {err}') from err
    out = []
    for k in klines:
        d, v = k.split(',')[:2]
        out.append((d, float(v)))
    return out


def fetch_and_store_index(conn, symbol: str, name: str,
                          days_back: int = 400) -> int:
    """增量拉取大盘指数日线并写库。返回新增行数。

    从库内该指数最新日期的次日起拉取；空表时回看 days_back 个自然日。"""
    row = conn.execute(
        'SELECT MAX(trade_date) FROM index_daily WHERE code = ?',
        (symbol,)).fetchone()
    latest = row[0] if row else None
    end = date.today().strftime('%Y-%m-%d')
    if latest:
        d = datetime.strptime(latest, '%Y-%m-%d') + timedelta(days=1)
        beg = d.strftime('%Y-%m-%d')
    else:
        beg = (date.today() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    if beg > end:
        return 0
    rows = fetch_index_daily(symbol, beg, end)
    return swdb.upsert_index_daily(conn, symbol, name, rows)


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


# — ERP 估值环境（沪深300 PE + 10Y 国债收益率）—

ERP_INDEX_SYMBOL = '沪深300'
ERP_INDEX_CODE = '000300'


def fetch_hs300_pe() -> list:
    """拉取沪深300 滚动市盈率(PE-TTM)全历史（乐咕，2005 起）。

    返回 [(trade_date, pe_ttm)] 升序；akshare 封装自带请求头，无需乐咕 token。
    """
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        try:
            df = ak.stock_index_pe_lg(symbol=ERP_INDEX_SYMBOL)
            if df is None or df.empty:
                raise ValueError('接口返回空数据')
            out = []
            for _, r in df.iterrows():
                d = str(r['日期'])[:10]
                pe = pd.to_numeric(r['滚动市盈率'], errors='coerce')
                if d and pd.notna(pe) and pe > 0:
                    out.append((d, float(pe)))
            return sorted(out)
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_RETRY_BACKOFF * attempt)
    raise RuntimeError(
        '拉取沪深300 PE 失败（重试 {} 次）: {}'.format(FETCH_MAX_RETRY, last_err))


def fetch_bond_10y() -> dict:
    """拉取中国 10 年期国债收益率全历史（单位 %），返回 {trade_date: rf}。"""
    df = ak.bond_zh_us_rate()
    out = {}
    for _, r in df.iterrows():
        d = str(r['日期'])[:10]
        rf = pd.to_numeric(r['中国国债收益率10年'], errors='coerce')
        if d and pd.notna(rf):
            out[d] = float(rf)
    return out


def fetch_and_store_erp(conn, index_code: str = ERP_INDEX_CODE) -> int:
    """拉取沪深300 PE 与 10Y 国债收益率，合成 ERP 行并写库。返回新增行数。

    EY = 100/PE_TTM（盈利收益率%），ERP = EY − 10Y 国债收益率。
    无国债数据对应日期的 rf/erp 留空（pe/ey 仍入库）。INSERT OR IGNORE，
    仅保留 ENV_RETAIN_YEARS 窗口内数据（与 purge 同口径），
    重复运行"新增 0 行"即就绪。"""
    cutoff = env_cutoff_date()
    pe_rows = [t for t in fetch_hs300_pe() if t[0] >= cutoff]
    rf_map = {d: v for d, v in fetch_bond_10y().items() if d >= cutoff}
    rows = []
    for d, pe in pe_rows:
        ey = round(100.0 / pe, 4)
        rf = rf_map.get(d)
        erp = round(ey - rf, 4) if rf is not None else None
        rows.append((index_code, d, pe, ey, rf, erp))
    return swdb.upsert_erp_rows(conn, rows)


# — 市场环境指标（辅助层：利率 / 量能 / 广度）—

# 数据保留窗口：环境指标与 ERP 统一跟随 sw.db 的 5 年滚动窗口
# （与 refresh.py 的 RETAIN_YEARS、purge_old_data 同口径），抓取层入库前
# 过滤窗口外旧行，否则全量接口每次刷新都会把 purge 剪掉的历史灌回来。
ENV_RETAIN_YEARS = 5

# 10Y 美债等全球利率取自 bond_zh_us_rate 的美债列（中国 10Y 已入 erp 表 rf 列）
US10Y_COLUMN = '美国国债收益率10年'
DR007_START = '20140101'      # DR007 定盘利率 2014-12 起发布，空表回填起点
# 两市成交额口径：沪市综合指数 + 深市综合指数（综指覆盖全市场，成指只含成分股）
TURNOVER_INDEXES = (('sh000001', '上证指数'), ('sz399106', '深证综指'))


def env_cutoff_date() -> str:
    """环境指标/ERP 的数据窗口下限（今天 - ENV_RETAIN_YEARS，YYYY-MM-DD）。"""
    t = date.today()
    return t.replace(year=t.year - ENV_RETAIN_YEARS).strftime('%Y-%m-%d')


def fetch_index_daily_full(symbol: str, beg: str, end: str) -> list:
    """拉取指数日线（收盘+成交量+成交额，东财 kline）。

    返回 [(trade_date, close, volume, amount)]；volume 单位手、amount 单位元。"""
    secid = ('1.' if symbol.startswith('sh') else '0.') + symbol[2:]
    try:
        klines = _em_kline_request(
            {'secid': secid, 'klt': '101', 'fqt': '1',
             'beg': beg.replace('-', ''), 'end': end.replace('-', ''),
             'fields1': 'f1,f2,f3,f4,f5', 'fields2': 'f51,f53,f56,f57'})
    except Exception as err:
        raise RuntimeError(
            f'拉取指数(全字段) {symbol} 失败（重试 {FETCH_MAX_RETRY} 次）: {err}') from err
    out = []
    for k in klines:
        parts = k.split(',')
        if len(parts) < 4:
            continue
        out.append((parts[0], float(parts[1]),
                    float(parts[2] or 0), float(parts[3] or 0)))
    return out


def fetch_us10y() -> dict:
    """拉取 10 年期美债收益率全历史（单位 %），返回 {trade_date: value}。"""
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        try:
            df = ak.bond_zh_us_rate()
            if df is None or df.empty:
                raise ValueError('接口返回空数据')
            out = {}
            for _, r in df.iterrows():
                d = str(r['日期'])[:10]
                v = pd.to_numeric(r[US10Y_COLUMN], errors='coerce')
                if d and pd.notna(v):
                    out[d] = float(v)
            if not out:
                raise ValueError('美债收益率列({})无有效数据'.format(US10Y_COLUMN))
            return out
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_RETRY_BACKOFF * attempt)
    raise RuntimeError(
        '拉取 10Y 美债失败（重试 {} 次）: {}'.format(FETCH_MAX_RETRY, last_err))


def _fetch_dr007_range(start_date: str, end_date: str) -> list:
    """单区间拉取 DR007 定盘利率（FDR007，单位 %），返回 [(trade_date, value)]。"""
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        try:
            df = ak.repo_rate_hist(start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return []
            out = []
            for _, r in df.iterrows():
                d = str(r['date'])[:10]
                v = pd.to_numeric(r['FDR007'], errors='coerce')
                if d and pd.notna(v):
                    out.append((d, float(v)))
            return sorted(out)
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_RETRY_BACKOFF * attempt)
    raise RuntimeError(
        '拉取 DR007 失败（重试 {} 次）: {}'.format(FETCH_MAX_RETRY, last_err))


def fetch_dr007(start_date: str, end_date: str) -> list:
    """拉取 DR007 定盘利率（FDR007，中国货币网，单位 %），返回 [(trade_date, value)]。

    start/end: 'YYYYMMDD'。按年分块拉取——接口长区间遇无数据月份时
    akshare 内部解析缺 frValueMap 键直接报错。"""
    y_start, y_end = int(start_date[:4]), int(end_date[:4])
    out = []
    for y in range(y_start, y_end + 1):
        cs = max('{}0101'.format(y), start_date)
        ce = min('{}1231'.format(y), end_date)
        out.extend(_fetch_dr007_range(cs, ce))
        time.sleep(FETCH_RETRY_BACKOFF)
    return sorted(set(out))


def fetch_high_low_stats() -> list:
    """拉取全 A 创新高/新低统计（乐咕，接口自带约 2 年历史）。

    返回 [(trade_date, high20, low20)]；创 20 日新高/新低家数。"""
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        try:
            df = ak.stock_a_high_low_statistics(symbol='all')
            if df is None or df.empty:
                raise ValueError('接口返回空数据')
            out = []
            for _, r in df.iterrows():
                d = str(r['date'])[:10]
                hi = pd.to_numeric(r['high20'], errors='coerce')
                lo = pd.to_numeric(r['low20'], errors='coerce')
                if d and pd.notna(hi) and pd.notna(lo):
                    out.append((d, int(hi), int(lo)))
            return sorted(out)
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_RETRY_BACKOFF * attempt)
    raise RuntimeError(
        '拉取新高/新低统计失败（重试 {} 次）: {}'.format(FETCH_MAX_RETRY, last_err))


def fetch_and_store_env(conn) -> dict:
    """抓取并入库市场环境指标。返回 {indicator: 新增行数}。

    - us10y          10Y 美债收益率%（幂等）
    - dr007          DR007 定盘利率%（30 自然日滚动窗口 upsert，防接口漏日；
                     空表从 DR007_START 回填）
    - high20 / low20 全 A 创 20 日新高/新低家数（接口自带约 2 年）
    - turnover       两市成交额亿元（沪深综指 kline 成交额加总；同步回填
      index_daily 的 volume/amount 列，扩列后旧行 NULL 一并补齐）
    全部仅保留 ENV_RETAIN_YEARS 窗口内数据（与 purge 同口径）；
    INSERT OR IGNORE，重复运行"新增 0 行"即就绪。"""
    stats = {}
    today = date.today()
    cutoff = env_cutoff_date()

    # 1) 10Y 美债
    us = {d: v for d, v in fetch_us10y().items() if d >= cutoff}
    stats['us10y'] = swdb.upsert_env_rows(conn, 'us10y', sorted(us.items()))
    time.sleep(FETCH_RETRY_BACKOFF)

    # 2) DR007：近 30 自然日窗口 + 空表全量（窗口过滤后再入库）
    row = conn.execute(
        'SELECT MAX(trade_date) FROM env_daily WHERE indicator = ?',
        ('dr007',)).fetchone()
    if not row[0]:
        start = DR007_START
    else:
        start = (datetime.strptime(row[0], '%Y-%m-%d') - timedelta(days=30)).strftime('%Y%m%d')
    dr = [t for t in fetch_dr007(start, today.strftime('%Y%m%d')) if t[0] >= cutoff]
    stats['dr007'] = swdb.upsert_env_rows(conn, 'dr007', dr)
    time.sleep(FETCH_RETRY_BACKOFF)

    # 3) 创 20 日新高/新低家数
    hl = [t for t in fetch_high_low_stats() if t[0] >= cutoff]
    stats['high20'] = swdb.upsert_env_rows(conn, 'high20', [(d, h) for d, h, _l in hl])
    stats['low20'] = swdb.upsert_env_rows(conn, 'low20', [(d, l) for d, _h, l in hl])
    time.sleep(FETCH_RETRY_BACKOFF)

    # 4) 指数量额：历史不完整则从窗口下限全量回填（UPSERT 幂等），
    #    扩列后旧行 amount 为 NULL 时从最早 NULL 日期补齐，否则日常增量
    for symbol, name in TURNOVER_INDEXES:
        earliest, latest = conn.execute(
            'SELECT MIN(trade_date), MAX(trade_date) FROM index_daily WHERE code = ?',
            (symbol,)).fetchone()
        missing = conn.execute(
            'SELECT MIN(trade_date) FROM index_daily WHERE code = ? AND amount IS NULL',
            (symbol,)).fetchone()[0]
        end = today.strftime('%Y-%m-%d')
        if earliest is None or earliest > cutoff:
            beg = cutoff
        elif missing:
            beg = max(missing, cutoff)
        elif latest:
            beg = (datetime.strptime(latest, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            continue
        if beg > end:
            stats.setdefault('_{}_skipped'.format(symbol), 0)
            continue
        rows = fetch_index_daily_full(symbol, beg, end)
        stats['index_{}'.format(symbol)] = swdb.upsert_index_daily(conn, symbol, name, rows)
        time.sleep(FETCH_RETRY_BACKOFF)

    # 5) 两市成交额（亿元）：沪深综指同日 amount 加总，SQL 合成，幂等
    cur = conn.execute("""
        INSERT OR IGNORE INTO env_daily (indicator, trade_date, value)
        SELECT 'turnover', a.trade_date, ROUND((a.amount + b.amount) / 1e8, 2)
        FROM index_daily a
        JOIN index_daily b ON b.code = ? AND b.trade_date = a.trade_date
        WHERE a.code = ? AND a.amount IS NOT NULL AND b.amount IS NOT NULL
        """, (TURNOVER_INDEXES[1][0], TURNOVER_INDEXES[0][0]))
    conn.commit()
    stats['turnover'] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return stats
