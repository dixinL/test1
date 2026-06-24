# -*- coding: utf-8 -*-
"""
WxPusher 微信推送通知脚本
读取最新报告文件，提取关键章节，推送到微信

用法:
  python wxpusher_notify.py                       # 自动找最新报告
  python wxpusher_notify.py report/xxx.md          # 指定报告文件
  python wxpusher_notify.py --date 2026-06-24       # 指定日期

环境变量(可选):
  WXPUSHER_APP_TOKEN: 覆盖脚本内置的 appToken
  WXPUSHER_UIDS:      覆盖默认接收者(逗号分隔)
"""

import os
import re
import sys
import json as _json
import urllib.request as _urllib_req
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report")

# ====== 默认配置 (可被环境变量覆盖) ======
WXPUSHER_APP_TOKEN = os.environ.get(
    "WXPUSHER_APP_TOKEN",
    "AT_RIaNQJyOk7E1wBw8MaunfCp5wA51cFJa"
)
WXPUSHER_UIDS = os.environ.get(
    "WXPUSHER_UIDS",
    # "UID_T7HyOt7KWWhTtwsanVAJ0UDbG77O,UID_svsRsMKanInF9hfJOHBbSvd4cPho,UID_F8tW6XkunMiPNOSnsn6iVEKmBto6"
    "UID_XLrkn2Or2zHTgGsEzR5gi8RCFkzD"
).split(",")
GITHUB_PAGES_URL = "https://dixinl.github.io/test1/output/daily_{}.html"
MAX_CONTENT_BYTES = 30000  # WxPusher 上限 ~40000 字节, 留余量
# ==========================================


def find_report_by_date(target_date):
    """按日期查找报告文件"""
    path = os.path.join(REPORT_DIR, "action_report_{}.md".format(target_date))
    if os.path.exists(path):
        return path
    return None


def find_latest_report():
    """查找最新的报告文件"""
    if not os.path.isdir(REPORT_DIR):
        print("[WxPusher] ERROR: report/ 目录不存在")
        return None

    md_files = [f for f in os.listdir(REPORT_DIR)
                if f.startswith("action_report_") and f.endswith(".md")]
    if not md_files:
        print("[WxPusher] ERROR: report/ 下无报告文件")
        return None

    md_files.sort(reverse=True)
    return os.path.join(REPORT_DIR, md_files[0])


def extract_date_from_filename(filepath):
    """从文件名提取日期"""
    basename = os.path.basename(filepath)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
    if m:
        return m.group(1)
    return datetime.now().strftime("%Y-%m-%d")


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

    for line in all_lines:
        s = line.strip()
        if ("市场状态:" in s or "当前市场判断" in s) and "拥挤度" not in s:
            market_status = s.replace("【", "").replace("】", "")[:60]

    page_url = GITHUB_PAGES_URL.format(data_date)

    # ---- 构建推送内容 (Markdown) - 三个章节 ----

    # 定位章节边界
    sec1_start = None   # "一、市场总体状态"
    sec1_end = None     # "二、" 开头 (场景分布)
    sec3_start = None   # "七、一级行业场景分布矩阵"
    sec3_end = None     # "八、综合投资建议" (或下一个章节)
    sec2_start = None   # "八、综合投资建议"
    sec2_end = None     # "九、" 开头 (速查表/总结)

    for i, line in enumerate(all_lines):
        s = line.strip()
        # 兼容 Markdown: 去掉 heading 前缀 (## / #)
        s_clean = re.sub(r'^#{1,3}\s*', '', s)
        if s_clean.startswith("一、") and ("市场总体" in s_clean or "数据日期" in s_clean):
            sec1_start = i
        elif s_clean.startswith("二、") and "九场景" in s:
            if sec1_start is not None and sec1_end is None:
                sec1_end = i
        elif "一级行业场景分布矩阵" in s and "趋势统计" in s:
            sec3_start = i
        elif ("八、" in s_clean and "综合投资" in s_clean) or "八、综合投资建议" in s_clean:
            if sec3_start is not None and sec3_end is None:
                sec3_end = i
            sec2_start = i
        elif s_clean.startswith("九、") and "场景矩阵" in s_clean:
            if sec2_start is not None and sec2_end is None:
                sec2_end = i

    # 提取三段内容
    md_lines = []
    md_lines.append("# 申万二级行业日报 {}".format(data_date))
    md_lines.append("")
    if market_status:
        md_lines.append("> **{}**".format(market_status))
        md_lines.append("")

    # 第一段: 市场总体状态
    if sec1_start is not None:
        end = sec1_end if sec1_end else min(sec1_start + 35, len(all_lines))
        md_lines.append("## 市场总体状态")
        md_lines.extend(all_lines[sec1_start:end])
        md_lines.append("")

    # 第二段: 一级行业场景分布矩阵
    if sec3_start is not None:
        end = sec3_end if sec3_end else (sec2_start if sec2_start else len(all_lines))
        md_lines.append("## 一级行业场景分布矩阵")
        md_lines.extend(all_lines[sec3_start:end])
        md_lines.append("")

    # 第三段: 综合投资建议
    if sec2_start is not None:
        end = sec2_end if sec2_end else len(all_lines)
        md_lines.append("## 综合投资建议")
        md_lines.extend(all_lines[sec2_start:end])

    # 尾部链接
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("[查看完整报告(含图表)]({}) | 数据截止: {}".format(page_url, data_date))

    content = "\n".join(md_lines)

    actual_size = len(content.encode("utf-8"))
    print("  [WxPusher] 推送大小: {:.0f}KB / 限制 {:.0f}KB ({:.0f}% 使用)".format(
        actual_size / 1024, MAX_CONTENT_BYTES / 1024,
        actual_size / MAX_CONTENT_BYTES * 100))

    # ---- 调用 WxPusher API ----
    payload = {
        "appToken": WXPUSHER_APP_TOKEN,
        "content": content,
        "summary": "{} | {}".format(
            data_date,
            market_status[:80] if market_status else "行业日报已生成",
        ),
        "contentType": 3,          # 3=Markdown / 2=HTML / 1=text
        "topicIds": [],
        "uids": WXPUSHER_UIDS,
        "url": page_url,
    }

    try:
        req_data = _json.dumps(payload).encode("utf-8")
        req = _urllib_req.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = _urllib_req.urlopen(req, timeout=15)
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
    """主入口: 解析参数 → 找报告 → 推送"""
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
