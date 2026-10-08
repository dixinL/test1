"""
趋势分析 RSS 生成 (analysis-hub 内部分发脚本)：扫描 sw2-trend-analysis 的
导出目录 (data/exports)，为每个基准日生成一个 RSS 条目，条目里同时给出
该日的 Excel 与 CSV 导出链接。

每个 RSS 条目的链接指向 data/exports/ 下对应日期的导出文件 (托管在 GitHub Pages)。
rss.xml 直接写在 data/exports/ 目录下。

与 generate_rss.py（日报）相互独立、各自自包含：日报链到 Markdown 报告，
趋势分析链到 xlsx + csv 导出，二者数据形态不同，故分成两个脚本。

analysis-hub 专用：目录与站点信息为本项目固定配置，由 run.py 第 4 步
分发调度，也可在 analysis-hub 目录下直接运行（无命令行参数）:
  python generate_trend_rss.py
"""

import os
import re
import sys
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
import xml.etree.ElementTree as ET

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXPORTS_DIR = os.path.join(REPO_DIR, 'sw2-trend-analysis', 'data', 'exports')
DEFAULT_RSS_DIR = os.path.join(REPO_DIR, 'sw2-trend-analysis', 'data', 'exports')
DEFAULT_SITE_BASE = 'https://dixinl.github.io/test1/sw2-trend-analysis'
DEFAULT_FEED_TITLE = '申万二级行业趋势分析'
DEFAULT_FEED_DESC = '每日申万二级行业趋势分析导出：UP/DOWN 关注榜、多窗口趋势（Excel + CSV）'
SITE_EXPORT_SUBPATH = 'data/exports'
XLSX_TEMPLATE = 'sw_trend_{}.xlsx'
CSV_TEMPLATE = 'sw_trend_summary_{}.csv'
CSV_PREFIX = 'sw_trend_summary_'


def collect_dates(exports_dir):
    """扫描导出目录，返回所有基准日（YYYY-MM-DD），最新在前。"""
    dates = set()
    if os.path.isdir(exports_dir):
        for f in os.listdir(exports_dir):
            if f.startswith(CSV_PREFIX) and f.endswith('.csv'):
                m = re.search(r'(\d{4}-\d{2}-\d{2})', f)
                if m:
                    dates.add(m.group(1))
    return sorted(dates, reverse=True)


def build_description(date, xlsx_url, xlsx_exists, csv_url, csv_exists):
    """description：日期 + Excel/CSV 两个导出链接（存在才列出）。"""
    parts = ['<p>基准日: {}</p>'.format(xml_escape(date))]
    if xlsx_exists:
        parts.append('<p><a href="{url}">Excel 快照：{name}</a></p>'.format(
            url=xml_escape(xlsx_url),
            name=xml_escape(os.path.basename(xlsx_url))))
    if csv_exists:
        parts.append('<p><a href="{url}">CSV 汇总榜单：{name}</a></p>'.format(
            url=xml_escape(csv_url),
            name=xml_escape(os.path.basename(csv_url))))
    return '\n'.join(parts)


def generate_rss(exports_dir, rss_dir, site_base, feed_title, feed_desc):
    """生成 RSS 2.0 XML"""
    feed_link = '{}/{}/'.format(site_base, SITE_EXPORT_SUBPATH)
    os.makedirs(rss_dir, exist_ok=True)
    dates = collect_dates(exports_dir)
    if not dates:
        print('未找到任何趋势导出文件 (在 {})'.format(exports_dir))
        return False
    old_pub_dates = {}
    rss_path = os.path.join(rss_dir, 'rss.xml')
    if os.path.exists(rss_path):
        try:
            tree = ET.parse(rss_path)
            root = tree.getroot()
            channel = root.find('channel')
            if channel is not None:
                for item in channel.findall('item'):
                    guid = item.find('guid')
                    pub_date = item.find('pubDate')
                    if guid is not None and pub_date is not None:
                        old_pub_dates[guid.text] = pub_date.text
            print('[INFO] 已读取 {} 条旧的 pubDate'.format(len(old_pub_dates)))
        except Exception:
            pass
    items_xml = []
    is_first = True
    for date in dates:
        xlsx_name = XLSX_TEMPLATE.format(date)
        csv_name = CSV_TEMPLATE.format(date)
        xlsx_path = os.path.join(exports_dir, xlsx_name)
        csv_path = os.path.join(exports_dir, csv_name)
        xlsx_exists = os.path.isfile(xlsx_path)
        csv_exists = os.path.isfile(csv_path)
        xlsx_url = '{}/{}/{}'.format(site_base, SITE_EXPORT_SUBPATH, xlsx_name)
        csv_url = '{}/{}/{}'.format(site_base, SITE_EXPORT_SUBPATH, csv_name)
        guid = '{}/{}/sw_trend_{}'.format(site_base, SITE_EXPORT_SUBPATH, date)
        link = xlsx_url if xlsx_exists else csv_url
        title = '申万二级行业趋势分析 - {}'.format(date)
        desc = build_description(date, xlsx_url, xlsx_exists, csv_url, csv_exists)
        if is_first:
            pub_date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')
            is_first = False
        elif guid in old_pub_dates:
            pub_date = old_pub_dates[guid]
        else:
            anchor = csv_path if csv_exists else xlsx_path
            mtime = os.path.getmtime(anchor)
            pub_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%dT%H:%M:%S+08:00')
        item = '    <item>\n      <title>{title}</title>\n      <link>{link}</link>\n      <description><![CDATA[{desc}]]></description>\n      <pubDate>{pub_date}</pubDate>\n      <guid isPermaLink="false">{guid}</guid>\n    </item>'.format(
            title=xml_escape(title), link=xml_escape(link),
            desc=desc.strip(), pub_date=pub_date, guid=xml_escape(guid))
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
    print('RSS 已生成: {} ({} 个基准日)'.format(rss_path, len(dates)))
    print('订阅地址: {}/{}/rss.xml'.format(site_base, SITE_EXPORT_SUBPATH))
    return True


def main():
    success = generate_rss(
        exports_dir=DEFAULT_EXPORTS_DIR,
        rss_dir=DEFAULT_RSS_DIR,
        site_base=DEFAULT_SITE_BASE.rstrip('/'),
        feed_title=DEFAULT_FEED_TITLE,
        feed_desc=DEFAULT_FEED_DESC)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
