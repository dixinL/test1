"""
日报 RSS 生成 (analysis-hub 内部分发脚本)：扫描日报报告 (Markdown)，
生成 RSS 2.0 XML 订阅源。

每个 RSS 条目的链接指向 report/ 下对应日期的报告文件 (托管在 GitHub Pages)。
rss.xml 直接写在 report/ 目录下。

analysis-hub 专用：目录与站点信息为本项目固定配置，由 run.py 第 4 步
分发调度，也可在 analysis-hub 目录下直接运行（无命令行参数）:
  python generate_rss.py
"""

import os
import re
import sys
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
import xml.etree.ElementTree as ET

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT_DIR = os.path.join(REPO_DIR, 'sw2-daily-report', 'report')
DEFAULT_RSS_DIR = os.path.join(REPO_DIR, 'sw2-daily-report', 'report')
DEFAULT_SITE_BASE = 'https://dixinl.github.io/test1/sw2-daily-report'
DEFAULT_FEED_TITLE = '申万二级行业量化日报'
DEFAULT_FEED_DESC = '每日自动生成申万二级行业分析报告：市场宽度、行业拥挤度、场景判断、综合排名'
REPORT_PREFIX = 'action_report_'


def extract_report_info(report_path, report_url):
    """从报告文件中提取日期、标题、描述。

    description 里放一个指向该报告文件的链接（报告集中体现在 report/ 文件中）。
    """
    filename = os.path.basename(report_path)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    report_date = m.group(1) if m else 'unknown'
    title = '申万二级行业日报 - {}'.format(report_date)
    hint = ''
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            head = [next(f, '') for _ in range(15)]
        for line in head:
            s = re.sub(r'^[#>\s]+', '', line.strip())
            if '报告日期' in s or '数据截止' in s:
                hint = s[:100]
                break
        if not hint:
            for line in head:
                s = re.sub(r'^[#>\s]+', '', line.strip())
                if '状态' in s:
                    if '**' in s or ':' in s or '：' in s:
                        hint = s.replace('**', '')[:100]
                        break
    except Exception:
        pass
    parts = []
    if hint:
        parts.append('<p>{}</p>'.format(xml_escape(hint)))
    parts.append('<p><a href="{url}">查看完整报告：{name}</a></p>'.format(
        url=xml_escape(report_url), name=xml_escape(filename)))
    description = '\n'.join(parts)
    return report_date, title, description


def generate_rss(report_dir, rss_dir, site_base, feed_title, feed_desc):
    """生成 RSS 2.0 XML"""
    feed_link = '{}/report/'.format(site_base)
    os.makedirs(rss_dir, exist_ok=True)
    report_files = []
    if os.path.isdir(report_dir):
        for f in os.listdir(report_dir):
            if f.startswith(REPORT_PREFIX) and f.endswith('.md'):
                report_files.append(f)
    if not report_files:
        print('未找到任何日报报告文件 (在 {})'.format(report_dir))
        return False
    report_files.sort(reverse=True)
    old_pub_dates = {}
    rss_path = os.path.join(rss_dir, 'rss.xml')
    if os.path.exists(rss_path):
        try:
            tree = ET.parse(rss_path)
            root = tree.getroot()
            channel = root.find('channel')
            if channel is not None:
                for item in channel.findall('item'):
                    link = item.find('link')
                    pub_date = item.find('pubDate')
                    if link is not None and pub_date is not None:
                        old_pub_dates[link.text] = pub_date.text
            print('[INFO] 已读取 {} 条旧的 pubDate'.format(len(old_pub_dates)))
        except Exception:
            pass
    items_xml = []
    is_first = True
    for filename in report_files:
        filepath = os.path.join(report_dir, filename)
        url = '{}/report/{}'.format(site_base, filename)
        report_date, title, desc = extract_report_info(filepath, url)
        if is_first:
            pub_date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')
            is_first = False
        elif url in old_pub_dates:
            pub_date = old_pub_dates[url]
        else:
            mtime = os.path.getmtime(filepath)
            pub_datetime = datetime.fromtimestamp(mtime)
            pub_date = pub_datetime.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        item = '    <item>\n      <title>{title}</title>\n      <link>{url}</link>\n      <description><![CDATA[{desc}]]></description>\n      <pubDate>{pub_date}</pubDate>\n      <guid isPermaLink="true">{url}</guid>\n    </item>'.format(
            title=xml_escape(title), url=xml_escape(url),
            desc=desc.strip(), pub_date=pub_date)
        items_xml.append(item)
    now = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0800')
    rss_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n  <channel>\n    <title>{feed_title}</title>\n    <link>{feed_link}</link>\n    <description>{feed_desc}</description>\n    <language>zh-CN</language>\n    <lastBuildDate>{now}</lastBuildDate>\n    <atom:link href="{feed_link}rss.xml" rel="self" type="application/rss+xml"/>\n{items}\n  </channel>\n</rss>'.format(
        feed_title=xml_escape(feed_title),
        feed_link=xml_escape(feed_link),
        feed_desc=xml_escape(feed_desc),
        now=now,
        items='\n'.join(items_xml))
    with open(rss_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    print('RSS 已生成: {} ({} 篇日报)'.format(rss_path, len(report_files)))
    print('订阅地址: {}/report/rss.xml'.format(site_base))
    return True


def main():
    success = generate_rss(
        report_dir=DEFAULT_REPORT_DIR,
        rss_dir=DEFAULT_RSS_DIR,
        site_base=DEFAULT_SITE_BASE.rstrip('/'),
        feed_title=DEFAULT_FEED_TITLE,
        feed_desc=DEFAULT_FEED_DESC)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
