# -*- coding: utf-8 -*-
"""WxPusher 微信推送通知（analysis-hub 版）。

推送最新的整合报告（hub/report/combined_YYYY-MM-DD.md）；
不存在时回退推送日报（sw2-daily-report/report/action_report_YYYY-MM-DD.md）。

用法（供 run.py 的 run_notify 调度，也可手动运行）:
  python wxpusher_notify.py                     # 自动找最新报告
  python wxpusher_notify.py --date 2026-09-20   # 指定日期
  python wxpusher_notify.py report/xxx.md       # 指定报告文件

环境变量(必填，代码不再内置明文):
  WXPUSHER_APP_TOKEN: WxPusher appToken
  WXPUSHER_UIDS:      接收者 UID(逗号分隔)
  未配置时跳过推送并以非零码退出（run.py 中仅告警，不影响主流程）。
"""

import os
import re
import sys
import json as _json
import urllib.request as _urllib_req
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
HUB_REPORT_DIR = os.path.join(BASE_DIR, "report")
DAILY_REPORT_DIR = os.path.join(REPO_DIR, "sw2-daily-report", "report")

# ====== 配置：只读环境变量，明文不入代码（CI 用 secrets 注入）======
WXPUSHER_APP_TOKEN = os.environ.get("WXPUSHER_APP_TOKEN", "")
WXPUSHER_UIDS = [u.strip() for u in os.environ.get("WXPUSHER_UIDS", "").split(",") if u.strip()]
SITE_URL = "https://dixinl.github.io/test1/sw2-daily-report/"
MAX_CONTENT_BYTES = 30000  # WxPusher 上限 ~40000 字节, 留余量
# ==========================================


def _latest(prefix, report_dir):
    """目录下按文件名倒序取最新一个 prefix*.md，无则返回 None。"""
    if not os.path.isdir(report_dir):
        return None
    md_files = [f for f in os.listdir(report_dir)
                if f.startswith(prefix) and f.endswith(".md")]
    if not md_files:
        return None
    md_files.sort(reverse=True)
    return os.path.join(report_dir, md_files[0])


def find_report_by_date(target_date):
    """按日期查找报告文件（优先整合报告，回退日报）。"""
    for d in (HUB_REPORT_DIR, DAILY_REPORT_DIR):
        for prefix in ("combined_{}".format(target_date),
                       "action_report_{}".format(target_date)):
            path = os.path.join(d, prefix + ".md")
            if os.path.exists(path):
                return path
    return None


def find_latest_report():
    """查找最新的报告文件（优先整合报告，回退日报）。"""
    path = _latest("combined_", HUB_REPORT_DIR)
    if path:
        return path
    path = _latest("action_report_", DAILY_REPORT_DIR)
    if path:
        return path
    print("[WxPusher] ERROR: hub/report 与 sw2-daily-report/report 下均无报告文件")
    return None


def extract_date_from_filename(filepath):
    """从文件名提取日期"""
    basename = os.path.basename(filepath)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
    if m:
        return m.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def _section_lines(all_lines, start_key, stop_keys, max_lines=40):
    """提取 start_key 命中的章节标题起，到任一 stop_keys 前的内容。"""
    start = None
    for i, line in enumerate(all_lines):
        s_clean = re.sub(r'^#{1,3}\s*', '', line.strip())
        if start is None and start_key in s_clean:
            start = i
        elif start is not None and any(k in s_clean for k in stop_keys):
            return all_lines[start:i]
    if start is None:
        return []
    return all_lines[start:start + max_lines]


def _build_digest(all_lines, data_date, market_status):
    """降级用章节摘要（全文超字节上限时才调用）：
    提取「分析与建议 / 重点推荐 / 市场总体状态」三段拼接。"""
    md_lines = []
    md_lines.append("# 申万二级行业整合报告 {}".format(data_date))
    md_lines.append("")

    if market_status:
        md_lines.append("> **{}**".format(market_status))
        md_lines.append("")

    # 第一段: 下一交易日分析与建议（整合报告「一、」的市场环境判断部分）
    advice = _section_lines(all_lines, "一、", ["重点推荐（达标标的）"])
    if advice:
        md_lines.append("## 分析与建议")
        md_lines.extend(advice[:40])
        md_lines.append("")

    # 第二段: 重点推荐 + 离达标最近（整合报告「一、」的推荐子节）
    rec = _section_lines(all_lines, "重点推荐（达标标的）", ["二、"])
    if rec:
        md_lines.append("## 重点推荐")
        md_lines.extend(rec[:40])
        md_lines.append("")

    # 第三段: 市场总体状态
    state = _section_lines(all_lines, "二、", ["三、"], max_lines=30)
    if state:
        md_lines.append("## 市场总体状态")
        md_lines.extend(state[:30])

    if len(md_lines) <= 3:
        # 结构识别失败时兜底：直接取报告前若干行
        md_lines.extend(all_lines[:40])

    return "\n".join(md_lines)


def send_wxpusher(report_path, data_date):
    """通过 WxPusher 推送微信消息"""

    if not report_path or not os.path.exists(report_path):
        print("  [WxPusher] 跳过: 报告文件不存在")
        return False

    # ---- 读取报告全文 ----
    with open(report_path, "r", encoding="utf-8") as f:
        full_report = f.read()

    all_lines = full_report.split("\n")
    file_size = len(full_report.encode("utf-8"))

    print("  [WxPusher] 报告大小: {:.0f}KB, {} 行".format(file_size / 1024, len(all_lines)))

    # ---- 提取关键指标 (用于 summary) ----
    market_status = ""
    _head_idx = None
    for i, line in enumerate(all_lines):
        s = line.strip()
        if s.startswith("#") and "当前市场判断" in s:
            _head_idx = i  # 标题行不作摘要，改为向后取正文首行
            break
        if ("当前市场判断" in s or "市场状态:" in s) and "拥挤度" not in s:
            market_status = s.replace("【", "").replace("】", "").replace("**", "").lstrip("-* ")[:60]
            break
    if not market_status and _head_idx is not None:
        for line in all_lines[_head_idx + 1:]:
            t = line.strip()
            if not t:
                continue
            if t.startswith("#"):
                break
            market_status = t.replace("【", "").replace("】", "").replace("**", "").lstrip("-* ")[:60]
            break

    # ---- 提取标题要素：市场判断 + 仓位综合判断 ----
    position = ""
    pm = re.search(r'仓位[=:：]\s*([^\s；;，。]+)', full_report)
    if pm:
        position = "仓位={}".format(pm.group(1))

    # ---- 构建推送内容：不做任何 Markdown 处理，直接按纯文本(contentType=1)发送 ----
    tail_link = "\n\n---\n[查看完整报告(含图表)]({}) | 数据截止: {}".format(SITE_URL, data_date)
    if file_size <= MAX_CONTENT_BYTES:
        content = full_report.rstrip() + tail_link
        print("  [WxPusher] 模式: 整篇推送全文 {} 行 ({:.0f}KB <= 上限 {:.0f}KB)".format(
            len(all_lines), file_size / 1024, MAX_CONTENT_BYTES / 1024))
    else:
        content = _build_digest(all_lines, data_date, market_status) + tail_link
        print("  [WxPusher] 模式: 章节摘要（全文 {:.0f}KB 超上限 {:.0f}KB）".format(
            file_size / 1024, MAX_CONTENT_BYTES / 1024))

    actual_size = len(content.encode("utf-8"))
    print("  [WxPusher] 推送首行: {}".format(content.splitlines()[0] if content else "(空)"))
    print("  [WxPusher] 推送大小: {:.0f}KB / 限制 {:.0f}KB ({:.0f}% 使用)".format(
        actual_size / 1024, MAX_CONTENT_BYTES / 1024,
        actual_size / MAX_CONTENT_BYTES * 100))

    # ---- 调用 WxPusher API ----
    summary = "{} | {}".format(data_date, market_status) if market_status else data_date
    if position:
        summary += " | " + position
    payload = {
        "appToken": WXPUSHER_APP_TOKEN,
        "content": content,
        "summary": summary[:99],
        "contentType": 1,          # 1=text / 2=HTML / 3=Markdown（正文已转纯文本）
        "topicIds": [],
        "uids": WXPUSHER_UIDS,
        "url": SITE_URL,
    }

    try:
        req_data = _json.dumps(payload).encode("utf-8")
        req = _urllib_req.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Windows 下系统证书链可能缺失导致 CERTIFICATE_VERIFY_FAILED，
        # 优先用 certifi 证书包（与 requests/akshare 同源），失败回退默认。
        ssl_ctx = None
        try:
            import ssl
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        resp = _urllib_req.urlopen(req, timeout=15, context=ssl_ctx)
        raw = resp.read().decode("utf-8")
        result = _json.loads(raw)

        if isinstance(result, list):
            code = result[0].get("code") if result else -1
            msg = result[0].get("msg", "unknown") if result else "empty"
        elif isinstance(result, dict):
            code = result.get("code", -1)
            msg = result.get("msg", "unknown")
        else:
            code = -1
            msg = str(type(result))

        if code == 1000:
            count = len(WXPUSHER_UIDS)
            print("  [WxPusher] OK -> {} 人, 内容 {:.0f}KB".format(count, actual_size / 1024))
            return True
        else:
            print("  [WxPusher] FAIL -> code={}, msg={}".format(code, msg))
            return False
    except Exception as e:
        print("  [WxPusher] ERROR -> {}".format(e))
        return False


def main():
    """主入口: 校验环境变量 → 解析参数 → 找报告 → 推送"""
    if not WXPUSHER_APP_TOKEN or not WXPUSHER_UIDS:
        print("[WxPusher] 未配置 WXPUSHER_APP_TOKEN / WXPUSHER_UIDS 环境变量，跳过推送")
        sys.exit(1)

    report_path = None
    data_date = None

    # 解析命令行参数
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--date" and i + 1 < len(sys.argv):
            data_date = sys.argv[i + 1]
            i += 2
        elif arg.endswith(".md") or arg.endswith(".txt"):
            report_path = arg
            i += 1
        else:
            i += 1

    # 确定报告路径
    if report_path is None:
        if data_date:
            report_path = find_report_by_date(data_date)
        else:
            report_path = find_latest_report()

    if report_path is None:
        print("[WxPusher] ERROR: 找不到报告文件")
        sys.exit(1)

    # 确定日期
    if data_date is None:
        data_date = extract_date_from_filename(report_path)

    print("[WxPusher] 报告文件: {}".format(report_path))
    print("[WxPusher] 数据日期: {}".format(data_date))

    ok = send_wxpusher(report_path, data_date)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
