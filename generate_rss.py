# -*- coding: utf-8 -*-
"""
扫描 output/ 目录中的日报 HTML，生成 RSS 2.0 XML 订阅源
"""

import os
import re
import sys
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# ============================================================
# 配置（你的仓库信息）
# ============================================================
GITHUB_USER = "dixinL"
REPO_NAME = "test1"
SITE_BASE = "https://{}.github.io/{}".format(GITHUB_USER.lower(), REPO_NAME)

FEED_TITLE = "申万二级行业量化日报"
FEED_DESC = "每日自动生成申万二级行业分析报告：市场宽度、行业拥挤度、场景判断、综合排名"
FEED_LINK = "{}/output/".format(SITE_BASE)
FEED_IMAGE_URL = ""  # 可选：feed 图标


def extract_html_info(html_path):
    """从 HTML 文件中提取标题和摘要描述"""
    # 从文件名提取日期
    filename = os.path.basename(html_path)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    report_date = m.group(1) if m else "unknown"

    title = "申万二级行业日报 - {}".format(report_date)

    # 读取 HTML 提取关键信息作为摘要
    description = ""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 提取市场状态
        status_m = re.search(r'<div class="status-box">(.*?)</div>', content)
        if status_m:
            description += status_m.group(1).strip() + "\n"

        # 提取关键指标（宽度、拥挤度、信号统计）
        for pattern, label in [
            (r'<div class="label">市场宽度</div><div class="value">(.*?)</div>', "市场宽度"),
            (r'<div class="label">行业拥挤度</div><div class="value">(.*?)</div>', "行业拥挤度"),
            (r'<div class="label">信号统计</div><div class="value">(.*?)</div>', "信号统计"),
            (r'<div class="label">有效行业</div><div class="value">(.*?)</div>', "有效行业"),
        ]:
            m2 = re.search(pattern, content)
            if m2:
                val = m2.group(1).replace("&nbsp;", " ").strip()
                description += "{}: {}\n".format(label, val)

        if not description.strip():
            description = "日报已生成，点击查看完整报告"
    except Exception:
        description = "日报已生成，点击查看完整报告"

    return report_date, title, description


def generate_rss():
    """生成 RSS 2.0 XML"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 扫描所有日报 HTML
    html_files = []
    for f in os.listdir(OUTPUT_DIR):
        if f.startswith("daily_") and f.endswith(".html"):
            html_files.append(f)

    if not html_files:
        print("未找到任何日报 HTML 文件")
        return False

    html_files.sort(reverse=True)  # 最新在前

    # 构建 RSS items
    items_xml = []
    latest_date = None
    for filename in html_files:
        filepath = os.path.join(OUTPUT_DIR, filename)
        report_date, title, desc = extract_html_info(filepath)

        if latest_date is None:
            latest_date = report_date

        url = "{}/output/{}".format(SITE_BASE, filename)
        pub_date = "{}T08:00:00+08:00".format(report_date)  # 北京时间早8点

        item = """    <item>
      <title>{title}</title>
      <link>{url}</link>
      <description><![CDATA[{desc}]]></description>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="true">{url}</guid>
    </item>""".format(
            title=xml_escape(title),
            url=xml_escape(url),
            desc=desc.strip(),
            pub_date=pub_date,
        )
        items_xml.append(item)

    # 构建 RSS XML
    now = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    build_date = latest_date or datetime.now().strftime("%Y-%m-%d")

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{feed_title}</title>
    <link>{feed_link}</link>
    <description>{feed_desc}</description>
    <language>zh-CN</language>
    <lastBuildDate>{now}</lastBuildDate>
    <atom:link href="{feed_link}rss.xml" rel="self" type="application/rss+xml"/>
{items}
  </channel>
</rss>""".format(
        feed_title=xml_escape(FEED_TITLE),
        feed_link=xml_escape(FEED_LINK),
        feed_desc=xml_escape(FEED_DESC),
        now=now,
        items="\n".join(items_xml),
    )

    # 写入文件
    rss_path = os.path.join(OUTPUT_DIR, "rss.xml")
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write(rss_xml)

    print("RSS 已生成: {} ({} 篇日报)".format(rss_path, len(html_files)))
    print("订阅地址: {}/output/rss.xml".format(SITE_BASE))
    return True


if __name__ == "__main__":
    success = generate_rss()
    sys.exit(0 if success else 1)
