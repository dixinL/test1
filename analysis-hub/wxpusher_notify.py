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
import html as _html
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
MAX_CONTENT_BYTES = 36000  # WxPusher 硬上限 40000 字节, 留余量
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


def _trim_report(md, max_bytes):
    """全文超上限时的降级：按最小标题从后往前整段删除，直到不超限。

    先删最小级标题的段落（如 #### 淘汰表），该级删完仍超则逐级放大（###、##）；
    每轮都从最后一个该级段落开始删（后删的总是价值最低的尾部内容）。
    H1 报告名永不删；极端兜底按 UTF-8 字节硬截断。"""
    lines = md.rstrip().splitlines()

    def blen(ls):
        return len("\n".join(ls).encode("utf-8"))

    for level in (4, 3, 2):
        while blen(lines) > max_bytes:
            starts = [i for i, ln in enumerate(lines)
                      if re.match(r'^' + '#' * level + r'\s', ln)]
            if not starts:
                break
            s = starts[-1]
            # 该段终点 = 下一个级别 <= level 的标题行或文件尾
            e = len(lines)
            for j in range(s + 1, len(lines)):
                m = re.match(r'^(#{1,6})\s', lines[j])
                if m and len(m.group(1)) <= level:
                    e = j
                    break
            del lines[s:e]
            while lines and not lines[-1].strip():
                lines.pop()
        if blen(lines) <= max_bytes:
            break

    out = "\n".join(lines)
    if len(out.encode("utf-8")) > max_bytes:
        out = out.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return out


def _inline_html(s):
    """行内 Markdown（加粗/链接）转 HTML，先转义防注入。"""
    s = _html.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', s)
    return s


_TD_STYLE = 'border:1px solid #ccc;font-size:12px;'


def _build_html_table(rows):
    """表格块 -> HTML <table>（首行作表头，带边框与底色）。"""
    if not rows:
        return ''
    parts = ['<table style="border-collapse:collapse;margin:4px 0;">']
    for ri, cells in enumerate(rows):
        tag = 'th' if ri == 0 else 'td'
        extra = 'background:#eef2f7;' if ri == 0 else ''
        parts.append('<tr>')
        for c in cells:
            parts.append('<{0} style="{1}{2}">{3}</{0}>'.format(
                tag, _TD_STYLE, extra, _inline_html(c)))
        parts.append('</tr>')
    parts.append('</table>')
    return ''.join(parts)


def _md_to_html(md):
    """Markdown 报告 -> WxPusher HTML（contentType=2）。

    颜色方案：H1 红 / H2 蓝灰 / H3 蓝，标题带底边线；
    趋势榜「强度=+」红、「强度=-」绿（A股惯例涨红跌绿）；引用灰斜体；
    表格转真实 <table>（边框+表头底色）。"""
    out = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        # 表格块 -> 真实 <table>（跳过 |---| 分隔行）
        if t.startswith('|') and t.endswith('|'):
            rows = []
            while i < len(lines):
                t2 = lines[i].strip()
                if not (t2.startswith('|') and t2.endswith('|')):
                    break
                cells = [c.strip() for c in t2.strip('|').split('|')]
                if not all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
                    rows.append(cells)
                i += 1
            out.append(_build_html_table(rows))
            continue
        if not t:
            out.append('<br/>')
            i += 1
            continue
        if t == '---':
            out.append('<hr/>')
            i += 1
            continue
        # 标题
        hm = re.match(r'^(#{1,6})\s+(.*)$', t)
        if hm:
            level = len(hm.group(1))
            color = {1: '#c0392b', 2: '#34495e'}.get(level, '#1a5276')
            size = {1: 20, 2: 17}.get(level, 15)
            lv = min(level + 1, 6)
            out.append('<h{0} style="color:{1};font-size:{2}px;'
                       'border-bottom:1px solid #ddd;padding-bottom:2px;margin:10px 0 4px;">{3}</h{0}>'.format(
                           lv, color, size, _inline_html(hm.group(2))))
            i += 1
            continue
        # 引用
        if t.startswith('>'):
            out.append('<p style="color:#888;margin:2px 0;"><i>' + _inline_html(t.lstrip('> ')) + '</i></p>')
            i += 1
            continue
        # 列表行：UP/DOWN 榜按强度正负着色
        body = t[2:] if t.startswith('- ') else t
        bullet = '• ' if t.startswith('- ') else ''
        im = re.search(r'强度=([+-])', body)
        color = ''
        if im:
            color = 'color:#c0392b;' if im.group(1) == '+' else 'color:#1e8449;'
        out.append('<p style="margin:2px 0;{}">{}{}</p>'.format(color, bullet, _inline_html(body)))
        i += 1
    return ''.join(out)


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

    # ---- 构建推送内容：HTML 渲染（标题/涨跌带颜色），超限按最小标题从后往前删段 ----
    tail_link = '<hr/><p><a href="{0}">查看完整报告(含图表)</a> | 数据截止: {1}</p>'.format(
        SITE_URL, data_date)
    content = _md_to_html(full_report.rstrip()) + tail_link
    if len(content.encode("utf-8")) <= MAX_CONTENT_BYTES:
        print("  [WxPusher] 模式: 整篇推送全文 (HTML {:.0f}KB <= 上限 {:.0f}KB)".format(
            len(content.encode("utf-8")) / 1024, MAX_CONTENT_BYTES / 1024))
    else:
        # 降级：不重组内容，只从尾部整段删除（先删最小级标题段，逐级放大）
        budget = MAX_CONTENT_BYTES - len(tail_link.encode("utf-8")) - 500
        while True:
            trimmed_md = _trim_report(full_report.rstrip(), budget)
            content = _md_to_html(trimmed_md) + tail_link
            if len(content.encode("utf-8")) <= MAX_CONTENT_BYTES or budget < 1000:
                break
            budget = int(budget * 0.9)  # HTML 标签膨胀超预期时收紧预算重试
        print("  [WxPusher] 模式: 逐段删减 (全文 {:.0f}KB -> 删减后 {:.0f}KB, HTML {:.0f}KB)".format(
            file_size / 1024, len(trimmed_md.encode("utf-8")) / 1024,
            len(content.encode("utf-8")) / 1024))

    actual_size = len(content.encode("utf-8"))
    first_text = re.sub(r'<[^>]+>', '', content.splitlines()[0]) if content else "(空)"
    print("  [WxPusher] 推送首行: {}".format(first_text))
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
        "contentType": 2,          # 2=HTML（带内联颜色） / 3=Markdown / 1=text
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
