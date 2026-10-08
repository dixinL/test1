# -*- coding: utf-8 -*-
"""趋势计算核心。

第一层：对每个窗口做一元线性回归，得 MA / 原始斜率 / 归一化斜率 / R² /
        price_vs_ma，结合每板块每窗口独立 ε 判 UP/DOWN/FLAT/N/A。
第二层：把每个板块 6 个窗口方向整理成一行，统计一致性，出 UP/DOWN 双榜。

`rolling_slope_norm` 供 probe_epsilon.py 复用，避免两处口径不一致。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence

import config


# ── 基础回归 ────────────────────────────────────────────────────────────
def _linreg(y: Sequence[float]) -> tuple[float, float]:
    """对 y 关于 t=0..N-1 做一元线性回归，返回 (slope, r_squared)。

    纯 Python 实现，避免引入 numpy。N<2 时返回 (0.0, 0.0)。
    """
    n = len(y)
    if n < 2:
        return 0.0, 0.0
    # t = 0,1,...,n-1，均值为 (n-1)/2
    t_mean = (n - 1) / 2.0
    y_mean = sum(y) / n
    s_tt = 0.0
    s_ty = 0.0
    for i, yi in enumerate(y):
        dt = i - t_mean
        s_tt += dt * dt
        s_ty += dt * (yi - y_mean)
    if s_tt == 0:
        return 0.0, 0.0
    slope = s_ty / s_tt
    # R² = 1 - SSE/SST
    sst = sum((yi - y_mean) ** 2 for yi in y)
    if sst == 0:
        # 完全水平线：斜率 0，拟合完美但无变化，R² 记 0（无趋势信息）。
        return 0.0, 0.0
    intercept = y_mean - slope * t_mean
    sse = 0.0
    for i, yi in enumerate(y):
        pred = slope * i + intercept
        sse += (yi - pred) ** 2
    r_squared = 1.0 - sse / sst
    # 数值误差兜底
    r_squared = max(0.0, min(1.0, r_squared))
    return slope, r_squared


def window_metrics(closes: Sequence[float]) -> Optional[dict]:
    """对一个已截好的窗口（长度=W，按时间升序）算全部指标。

    返回 {ma, slope, slope_norm, r_squared, price_vs_ma}；样本不足返回 None。
    price_vs_ma 用窗口最后一个点位（最新交易日）与该窗口 MA 比较。
    """
    n = len(closes)
    if n < 2:
        return None
    ma = sum(closes) / n
    slope, r_squared = _linreg(closes)
    slope_norm = slope / ma if ma != 0 else 0.0
    latest = closes[-1]
    price_vs_ma = (latest - ma) / ma if ma != 0 else 0.0
    return {
        "ma": ma,
        "slope": slope,
        "slope_norm": slope_norm,
        "r_squared": r_squared,
        "price_vs_ma": price_vs_ma,
    }


def decide_direction(slope_norm: float, price_vs_ma: float, epsilon: float) -> str:
    """叠加判定：两信号同向才算趋势；ε 为对称双边阈值。"""
    if slope_norm > epsilon and price_vs_ma > 0:
        return "UP"
    if slope_norm < -epsilon and price_vs_ma < 0:
        return "DOWN"
    return "FLAT"


# ── 第一层：单板块全窗口计算 ────────────────────────────────────────────
def analyze_one(
    sw_code: str,
    sw_name: str,
    closes_asc: Sequence[float],
    epsilon_map: dict[tuple[str, int], float],
    calc_date: str,
    created_at: str,
) -> list[dict]:
    """对一个板块的升序收盘序列，算 6 个窗口的 trend 明细行。

    每个窗口取序列最后 W 个点位；不足 W 记 direction='N/A'。
    epsilon 缺失（探针未覆盖该板块该窗口）同样降级为 'N/A'，避免用错阈值。
    """
    rows: list[dict] = []
    total = len(closes_asc)
    for w in config.WINDOWS:
        base = {
            "sw_code": sw_code,
            "sw_name": sw_name,
            "calc_date": calc_date,
            "window": w,
            "ma": None,
            "slope": None,
            "slope_norm": None,
            "r_squared": None,
            "price_vs_ma": None,
            "epsilon": None,
            "direction": "N/A",
            "created_at": created_at,
        }
        if total < w:
            rows.append(base)
            continue
        m = window_metrics(closes_asc[-w:])
        if m is None:
            rows.append(base)
            continue
        eps = epsilon_map.get((sw_code, w))
        base.update(m)
        base["epsilon"] = eps
        if eps is None:
            base["direction"] = "N/A"  # 无阈值不硬判
        else:
            base["direction"] = decide_direction(
                m["slope_norm"], m["price_vs_ma"], eps
            )
        rows.append(base)
    return rows


# ── 榜单打分：加权趋势分 + 自身历史分位 ─────────────────────────────────
def window_strength(
    slope_norm: Optional[float],
    r_squared: Optional[float],
    price_vs_ma: Optional[float],
) -> Optional[float]:
    """单窗口连续强度：slope_norm × r²（置信度）× 背离折扣。

    - slope_norm 提供大小与正负；缺失（None）返回 None（该窗口不计入）。
    - r² 缺失时按 0 处理（无置信度 → 强度 0，不误当强趋势）。
    - price_vs_ma 与 slope_norm 反号（背离）时乘 config.DIVERGENCE_PENALTY；
      同号、任一为 0、或 price_vs_ma 缺失时不折扣（penalty=1）。
    """
    if slope_norm is None:
        return None
    r2 = r_squared if r_squared is not None else 0.0
    strength = slope_norm * r2
    if price_vs_ma is not None and slope_norm * price_vs_ma < 0:
        strength *= config.DIVERGENCE_PENALTY
    return strength


def weighted_score(strength_by_window: dict[int, Optional[float]]) -> Optional[float]:
    """按 config.WINDOW_WEIGHTS 对各窗口强度加权求和（偏向短窗口）。

    缺失窗口（None）跳过，并按剩余窗口权重重新归一化，保证占比不被拉偏。
    全部缺失返回 None。
    """
    num = 0.0
    wsum = 0.0
    for w, weight in config.WINDOW_WEIGHTS.items():
        v = strength_by_window.get(w)
        if v is None:
            continue
        num += weight * v
        wsum += weight
    if wsum == 0:
        return None
    return num / wsum


def rank_in_history(value: float, history: Sequence[float]) -> Optional[float]:
    """返回 value 在 history 分布中的百分位（0~1，含并列的中点法）。

    用于 B：当前 slope_norm 相对该板块自身历史处于高位还是低位。
    history 为空返回 None。
    """
    n = len(history)
    if n == 0:
        return None
    below = sum(1 for x in history if x < value)
    equal = sum(1 for x in history if x == value)
    return (below + 0.5 * equal) / n


def self_quantile_score(
    closes_asc: Sequence[float],
    slopes_by_window: dict[int, Optional[float]],
) -> Optional[float]:
    """B：各窗口「当前 slope_norm 在自身历史分位」按同一短窗口权重加权。

    对每个窗口，用 rolling_slope_norm 得历史分布，求当前值的历史分位，
    再按 config.WINDOW_WEIGHTS 加权（缺失窗口跳过并重新归一化）。
    """
    num = 0.0
    wsum = 0.0
    for w, weight in config.WINDOW_WEIGHTS.items():
        cur = slopes_by_window.get(w)
        if cur is None:
            continue
        hist = rolling_slope_norm(closes_asc, w)
        pct = rank_in_history(cur, hist)
        if pct is None:
            continue
        num += weight * pct
        wsum += weight
    if wsum == 0:
        return None
    return num / wsum


# ── 第二层：跨周期一致性整理 + 双榜 ─────────────────────────────────────
def build_summary(
    trend_rows: Sequence[dict],
    closes_map: Optional[dict[str, Sequence[float]]] = None,
) -> list[dict]:
    """把 trend 明细（多板块 × 多窗口）整理成每板块一行的汇总。

    每行含 6 窗口方向平铺、up_count/down_count、连续性标记、slope_norm 均值、
    加权趋势分 score（偏向短窗口，榜单排序/进榜依据）。
    传入 closes_map={sw_code: 升序收盘序列} 且 config.ENABLE_SELF_QUANTILE 时，
    额外算 self_quantile（B：当前 slope_norm 相对自身历史的加权百分位）。
    """
    by_code: dict[str, dict] = {}
    for r in trend_rows:
        code = r["sw_code"]
        entry = by_code.setdefault(
            code,
            {
                "sw_code": code,
                "sw_name": r["sw_name"],
                "dirs": {},
                "slopes": {},
                "r2s": {},
                "pvms": {},
            },
        )
        entry["dirs"][r["window"]] = r["direction"]
        entry["slopes"][r["window"]] = r["slope_norm"]
        entry["r2s"][r["window"]] = r["r_squared"]
        entry["pvms"][r["window"]] = r["price_vs_ma"]

    summary: list[dict] = []
    for code, e in by_code.items():
        dirs = e["dirs"]
        dir_list = [dirs.get(w, "N/A") for w in config.WINDOWS]
        up_count = dir_list.count("UP")
        down_count = dir_list.count("DOWN")
        valid_slopes = [
            e["slopes"].get(w)
            for w in config.WINDOWS
            if e["slopes"].get(w) is not None
        ]
        slope_mean = sum(valid_slopes) / len(valid_slopes) if valid_slopes else 0.0

        # 每窗口连续强度（slope_norm × r² × 背离折扣），再按短窗口加权得 score。
        strengths = {
            w: window_strength(
                e["slopes"].get(w), e["r2s"].get(w), e["pvms"].get(w)
            )
            for w in config.WINDOWS
        }
        score = weighted_score(strengths)

        row = {
            "sw_code": code,
            "sw_name": e["sw_name"],
            "up_count": up_count,
            "down_count": down_count,
            "consistency": _consistency_label(dir_list),
            "slope_norm_mean": slope_mean,
            "score": score if score is not None else 0.0,
        }

        # B：自身历史分位（可选）。
        if closes_map is not None and config.ENABLE_SELF_QUANTILE:
            closes = closes_map.get(code)
            sq = (
                self_quantile_score(closes, e["slopes"])
                if closes is not None else None
            )
            row["self_quantile"] = sq
        else:
            row["self_quantile"] = None

        # 六窗口方向平铺列：dir_10 ... dir_360（纯标签，供上色/内部判断）。
        # 另出 dirv_10 ... dirv_360：标签 + (slope_norm | w_strength) 数值，
        # 供导出显示，如 "UP(+0.0066 | +0.0029)"；缺该窗口则为 "N/A"。
        # str_10 ... str_360 保留 w_strength 原始值（None 为缺失），供内部/明细用。
        for w in config.WINDOWS:
            label = dirs.get(w, "N/A")
            row[f"dir_{w}"] = label
            sn = e["slopes"].get(w)
            st = strengths.get(w)
            if sn is None:
                row[f"dirv_{w}"] = label
            else:
                st_txt = f"{st:+.4f}" if st is not None else "NA"
                row[f"dirv_{w}"] = f"{label}({sn:+.4f} | {st_txt})"
            row[f"str_{w}"] = st
        summary.append(row)
    return summary


def _consistency_label(dir_list: Sequence[str]) -> str:
    """按从长到短是否方向连贯给出一致性标签。

    dir_list 顺序对应 config.WINDOWS（10→360）；此处按 360→10 检视连贯性。
    """
    ordered = list(reversed(dir_list))  # 长周期在前
    trend_dirs = [d for d in ordered if d in ("UP", "DOWN")]
    if not trend_dirs:
        return "无趋势"
    if all(d == trend_dirs[0] for d in trend_dirs):
        # 方向全一致；再看中间是否夹 FLAT/N/A
        if "FLAT" in ordered or "N/A" in ordered:
            return f"一致偏{_cn(trend_dirs[0])}(有间断)"
        return f"连续{_cn(trend_dirs[0])}"
    return "方向打架"


def _cn(direction: str) -> str:
    return {"UP": "上行", "DOWN": "下行"}.get(direction, direction)


def split_watchlists(summary: Sequence[dict]) -> tuple[list[dict], list[dict]]:
    """按加权趋势分 score 的正负拆成 UP/DOWN 双榜，各按强弱排序。

    - score > 0 → UP 榜，按 score 降序（越强越靠前）。
    - score < 0 → DOWN 榜，按 score 升序（越弱/越负越靠前）。
    - score == 0（含全窗口无效）不进任何榜。
    不再按离散计数或固定分位截断——上行即入 UP、下行即入 DOWN，全部排出。
    """
    up = [s for s in summary if s.get("score", 0.0) > 0]
    down = [s for s in summary if s.get("score", 0.0) < 0]
    up.sort(key=lambda s: -s["score"])
    down.sort(key=lambda s: s["score"])
    return up, down


# ── 探针复用：滚动 slope_norm 序列 ──────────────────────────────────────
def rolling_slope_norm(closes_asc: Sequence[float], window: int) -> list[float]:
    """对升序收盘序列，按 window 滚动计算历史每一天的 slope_norm。

    与主流程 window_metrics 的 slope_norm 口径完全一致（斜率/窗口均价）。
    返回长度 = max(0, len-window+1)。
    """
    n = len(closes_asc)
    if n < window:
        return []
    out: list[float] = []
    for end in range(window, n + 1):
        seg = closes_asc[end - window : end]
        ma = sum(seg) / window
        if ma == 0:
            continue
        slope, _ = _linreg(seg)
        out.append(slope / ma)
    return out


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """线性插值分位数（等价 numpy 默认 'linear'）；空序列返回 None。"""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
