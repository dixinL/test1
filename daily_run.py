# -*- coding: utf-8 -*-
"""
申万二级行业每日统一调度工具

功能:
  1. 拉取最新数据 (width / congestion / quotes)
  2. 生成分析报告 (action_report)
  3. 生成热力图 (draw_heatmap: width + congestion)
  4. 生成排名数据 (sw2_score_rank)
  5. 输出 RSS 可读的整合 HTML 文件

用法:
  python daily_run.py              # 全量运行
  python daily_run.py --no-image    # 不生成图片(加快速度)
  python daily_run.py --no-rank     # 不生成排名

输出:
  output/daily_YYYY-MM-DD.html     # RSS 可读整合文件
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def run_script(script_name, args=None, label="", timeout=300):
    """
    运行子脚本并返回 (success, stdout, stderr)
    实时流式输出，防止 GitHub Actions 超时看不到日志
    """
    cmd = [sys.executable, os.path.join(BASE_DIR, script_name)]
    if args:
        cmd.extend(args)

    print("\n" + "=" * 60)
    print("  [{}] python {}".format(label or script_name, " ".join(cmd[2:])))
    print("  [{}] 开始时间: {}".format(label or script_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("  [{}] 超时设置: {}s".format(label or script_name, timeout))
    print("=" * 60)
    sys.stdout.flush()

    try:
        result = subprocess.run(
            cmd,
            cwd=BASE_DIR,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout
        )

        print("\n" + "-" * 60)
        print("  [{}] 完成时间: {}".format(label or script_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        print("  [{}] 返回码: {}".format(label or script_name, result.returncode))
        print("-" * 60)
        sys.stdout.flush()

        return result.returncode == 0, "", ""
    except subprocess.TimeoutExpired:
        print("\n" + "-" * 60)
        print("  [{}] TIMEOUT! 超过 {} 秒".format(label or script_name, timeout))
        print("  [{}] 超时时间: {}".format(label or script_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        print("-" * 60)
        sys.stdout.flush()
        return False, "", "Timeout after {}s".format(timeout)


def find_latest_file(directory, pattern):
    """查找目录下匹配pattern的最新文件"""
    if not os.path.isdir(directory):
        return None
    files = [f for f in os.listdir(directory) if pattern in f and not f.startswith(".")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(directory, files[0])


def generate_rss_html(report_path, rank_csv, width_img, congestion_img, output_path, data_date):
    """
    生成 RSS 可读的 HTML 整合文件
    """
    # ---- 读取各部分内容 ----
    report_text = ""
    if report_path and os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            report_text = f.read()

    # 解析排名 CSV
    rank_rows = []
    rank_headers = []
    if rank_csv and os.path.exists(rank_csv):
        import csv as _csv
        with open(rank_csv, "r", encoding="utf-8-sig") as f:
            reader = _csv.reader(f)
            rank_headers = next(reader, [])
            for row in reader:
                if len(row) >= 6:  # 至少有基本列
                    rank_rows.append(row)

    # 取 TOP15
    top15 = rank_rows[:15]
    bottom5 = rank_rows[-5:] if len(rank_rows) > 5 else []

    # 图片使用相对路径（output/ → ../width/  ../congestion/）
    width_img_rel = None
    cong_img_rel = None

    if width_img and os.path.exists(width_img):
        width_img_rel = os.path.relpath(width_img, OUTPUT_DIR).replace("\\", "/")
    if congestion_img and os.path.exists(congestion_img):
        cong_img_rel = os.path.relpath(congestion_img, OUTPUT_DIR).replace("\\", "/")

    # ---- 从报告中提取关键指标 ----
    market_status = ""
    width_avg = ""
    cong_avg = ""
    trend_signal_count = ""

    for line in report_text.split("\n"):
        if "当前市场判断" in line:
            idx = report_text.index(line)
            next_lines = report_text[idx:].split("\n")[:3]
            market_status = " ".join(l.strip() for l in next_lines if l.strip())
        if "均值:" in line and ("状态:" in line or "趋势:" in line):
            width_avg = line.strip()
        if "拥挤度" in line and "均值:" in line:
            cong_avg = line.strip()
        if "趋势信号:" in line:
            trend_signal_count = line.strip()

    # ---- 生成 HTML ----
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>申万二级行业日报 - {data_date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f5f5; color: #333; line-height: 1.6; padding: 16px; max-width: 900px; margin: 0 auto; }}
h1 {{ font-size: 1.4em; text-align: center; color: #1a1a1a; margin-bottom: 4px; }}
.date-line {{ text-align: center; color: #888; font-size: 0.85em; margin-bottom: 20px; }}
.section {{ background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
h2 {{ font-size: 1.1em; color: #1a1a1a; border-left: 4px solid #4a90d9; padding-left: 10px; margin-bottom: 12px; }}
.market-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.market-item {{ background: #fafafa; padding: 10px; border-radius: 6px; font-size: 0.9em; }}
.market-item .label {{ color: #666; font-size: 0.85em; }}
.market-item .value {{ font-weight: bold; color: #1a1a1a; margin-top: 2px; }}
.status-box {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 14px; border-radius: 8px; text-align: center; font-weight: bold; font-size: 1.05em; margin-bottom: 12px; }}
.chart-img {{ max-width: 100%; height: auto; border-radius: 6px; margin-top: 10px; display: block; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
th {{ background: #4a90d9; color: #fff; padding: 8px 6px; text-align: center; white-space: nowrap; }}
td {{ padding: 7px 6px; text-align: center; border-bottom: 1px solid #eee; }}
tr:nth-child(even) {{ background: #fafafa; }}
.rank-top {{ color: #e74c3c; font-weight: bold; }}
.rank-good {{ color: #27ae60; }}
.rank-bad {{ color: #e74c3c; }}
.score-bar {{ background: #eee; height: 6px; border-radius: 3px; overflow: hidden; }}
.score-fill {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
.report-text {{ font-size: 0.88em; white-space: pre-wrap; word-break: break-all; }}
.scene-tag {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; font-weight: bold; color: #fff; }}
.s1 {{ background: #27ae60; }} .s2 {{ background: #f39c12; }} .s3 {{ background: #e74c3c; }} .s4 {{ background: #3498db; }}
.footer {{ text-align: center; color: #aaa; font-size: 0.78em; margin-top: 20px; padding: 10px; }}
@media(max-width:600px) {{
  .market-grid {{ grid-template-columns: 1fr; }}
  table {{ font-size: 0.75em; }}
  th, td {{ padding: 5px 3px; }}
}}
</style>
</head>
<body>

<h1>[*] 申万二级行业量化日报</h1>
<div class="date-line">生成时间: {today} &nbsp;|&nbsp; 数据截止: {data_date}</div>

<!-- 市场概览 -->
<div class="section">
<h2>[*] 市场概览</h2>
<div class="status-box">{market_status}</div>
<div class="market-grid">
  <div class="market-item"><div class="label">市场宽度</div><div class="value">{width_avg}</div></div>
  <div class="market-item"><div class="label">行业拥挤度</div><div class="value">{cong_avg}</div></div>
  <div class="market-item"><div class="label">信号统计</div><div class="value">{trend_signal_count}</div></div>
  <div class="market-item"><div class="label">有效行业</div><div class="value">{total_industries} 个</div></div>
</div>
</div>

<!-- 热力图 -->
{charts_section}

<!-- 综合排名 TOP15 -->
<div class="section">
<h2>[*] 综合得分排名 TOP 15</h2>
<table>
<tr><th>#</th><th>行业</th><th>综合分</th><th>价格</th><th>宽度</th><th>拥挤度</th><th>3天前</th><th>变化</th></tr>
{top15_rows}
</table>
</div>

<!-- 垫底行业 -->
{bottom_section}

<!-- 详细报告 -->
<div class="section">
<h2>[*] 详细分析报告</h2>
<div class="report-text">{report_text_escaped}</div>
</div>

<div class="footer">由 sw2_daily 自动生成 | Powered by legulegu + akshare</div>

<script>
(function() {{
  // 仅当通过网络访问（非本地 file://）时，将图片相对路径替换为绝对路径
  if (location.protocol === 'file:') return;

  var GITHUB_BASE = 'https://dixinl.github.io/test1/';
  var imgs = document.querySelectorAll('img.chart-img');
  for (var i = 0; i < imgs.length; i++) {{
    var src = imgs[i].getAttribute('src');
    if (src && src.indexOf('../') === 0) {{
      imgs[i].src = GITHUB_BASE + src.substring(3);
    }}
  }}
}})();
</script>
</body>
</html>"""

    # ---- 构建各段内容 ----

    # 图表区域
    charts_section = ""
    if width_img_rel or cong_img_rel:
        charts_section = '<div class="section"><h2>[*][*] 市场热力图</h2>'
        if width_img_rel:
            charts_section += '<p style="font-size:0.88em;color:#666;margin:8px 0 4px;">均线市场宽度 (越高越好)</p>'
            charts_section += '<img class="chart-img" src="%s" alt="width heatmap">' % width_img_rel
        if cong_img_rel:
            charts_section += '<p style="font-size:0.88em;color:#666;margin:14px 0 4px;">行业拥挤度 (适中为宜)</p>'
            charts_section += '<img class="chart-img" src="%s" alt="congestion heatmap">' % cong_img_rel
        charts_section += "</div>"

    # TOP15 表格行
    top15_rows_html = ""
    for r in top15:
        try:
            name = r[2] if len(r) > 2 else "-"
            score = float(r[3]) if len(r) > 3 and r[3] else 0
            p_score = float(r[4]) if len(r) > 4 and r[4] else 0
            w_score = float(r[5]) if len(r) > 5 and r[5] else 0
            c_score = float(r[6]) if len(r) > 6 and r[6] else 0
            prev_score = float(r[7]) if len(r) > 7 and r[7] else 0
            delta = score - prev_score
            delta_str = "{:+.1f}".format(delta) if prev_score else "N/A"
            delta_class = "rank-good" if delta > 3 else ("rank-bad" if delta < -3 else "")

            bar_color = "#27ae60" if score >= 70 else ("#f39c12" if score >= 40 else "#e74c3c")
            bar_html = '<div class="score-bar"><div class="score-fill" style="width:%.0f%%;background:%s"></div></div>' % (
                min(score, 100), bar_color)

            top15_rows_html += '<tr><td class="rank-top">%s</td><td>%s</td><td>%.1f<br>%s</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%.1f</td><td class="%s">%s</td></tr>' % (
                r[0], name, score, bar_html, p_score, w_score, c_score, prev_score, delta_class, delta_str)
        except (ValueError, IndexError):
            continue

    # 垫底区域
    bottom_section = ""
    if bottom5:
        bottom_section = '<div class="section"><h2>[*][*] 综合得分垫底 TOP 5</h2><table>'
        bottom_section += '<tr><th>#</th><th>行业</th><th>综合分</th><th>价格</th><th>宽度</th><th>拥挤度</th></tr>'
        for r in bottom5:
            try:
                name = r[2] if len(r) > 2 else "-"
                score = float(r[3]) if len(r) > 3 and r[3] else 0
                p_score = float(r[4]) if len(r) > 4 and r[4] else 0
                w_score = float(r[5]) if len(r) > 5 and r[5] else 0
                c_score = float(r[6]) if len(r) > 6 and r[6] else 0
                bottom_section += '<tr><td>%s</td><td>%s</td><td class="rank-bad">%.1f</td><td>%.0f</td><td>%.0f</td><td>%.0f</td></tr>' % (
                    r[0], name, score, p_score, w_score, c_score)
            except (ValueError, IndexError):
                continue
        bottom_section += "</table></div>"

    # 报告文本（HTML 转义）
    report_escaped = (report_text
                      .replace("&", "&amp;")
                      .replace("<", "&lt;")
                      .replace(">", "&gt;")
                      .replace("\n", "<br>\n"))

    total_industries = len(rank_rows)

    html = html.format(
        data_date=data_date,
        today=today_str,
        market_status=market_status.replace("【", "").replace("】", "")[:50],
        width_avg=width_avg.replace(" ", "&nbsp;") if width_avg else "-",
        cong_avg=cong_avg.replace(" ", "&nbsp;") if cong_avg else "-",
        trend_signal_count=trend_signal_count.strip() if trend_signal_count else "-",
        total_industries=total_industries,
        charts_section=charts_section,
        top15_rows=top15_rows_html,
        bottom_section=bottom_section,
        report_text_escaped=report_escaped,
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(output_path) / 1024
    print("\n  [OK] 已生成: {} ({:.0f}KB)".format(output_path, size_kb))
    return True


def main():
    """
    主流程:
      1. 生成报告 (action_report.py)
      2. 生成热力图 (draw_heatmap.py width + congestion)
      3. 生成排名 (sw2_score_rank.py)
      4. 收集产物路径
      5. 整合为 RSS HTML
    """
    start_time = datetime.now()
    print("=" * 65)
    print("   [*] 申万二级行业每日统一调度")
    print("   开始时间: {}".format(start_time.strftime("%Y-%m-%d %H:%M:%S")))
    print("   本地日期: {} | UTC日期: {}".format(
        date.today(),
        datetime.utcnow().date()
    ))
    print("=" * 65)
    sys.stdout.flush()

    # 解析参数
    no_image = "--no-image" in sys.argv
    no_rank = "--no-rank" in sys.argv
    no_report = "--no-report" in sys.argv
    print("   参数: no_image={}, no_rank={}, no_report={}".format(no_image, no_rank, no_report))
    sys.stdout.flush()

    results = {}
    errors = []

    # ---- Step 1: 生成分析报告 ----
    if not no_report:
        print("\n" + "#" * 65)
        print("# Step 1/5: 生成分析报告 (action_report.py)")
        print("# 时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        print("#" * 65)
        sys.stdout.flush()

        ok, _, _ = run_script("action_report.py", label="分析报告")
        results["report"] = ok
        print("   [Step 1] 分析报告: {} (用时: {:.1f}s)".format(
            "成功" if ok else "失败",
            (datetime.now() - start_time).total_seconds()
        ))
        sys.stdout.flush()
        if not ok:
            errors.append("action_report 失败")
    else:
        print("   [Step 1] 跳过分析报告 (--no-report)")
        sys.stdout.flush()

    # ---- Step 2: 生成热力图 ----
    step_start = datetime.now()
    if not no_image:
        print("\n" + "#" * 65)
        print("# Step 2/5: 生成热力图 (draw_heatmap.py)")
        print("# 时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        print("#" * 65)
        sys.stdout.flush()

        ok_w, _, _ = run_script("draw_heatmap.py", ["width"], label="宽度热力图")
        ok_c, _, _ = run_script("draw_heatmap.py", ["congestion"], label="拥挤度热力图")
        results["heatmap"] = ok_w and ok_c
        step_elapsed = (datetime.now() - step_start).total_seconds()
        print("   [Step 2] 宽度热力图: {}, 拥挤度热力图: {} (用时: {:.1f}s)".format(
            "成功" if ok_w else "失败",
            "成功" if ok_c else "失败",
            step_elapsed
        ))
        sys.stdout.flush()
        if not ok_w:
            errors.append("width heatmap 失败")
        if not ok_c:
            errors.append("congestion heatmap 失败")
    else:
        print("   [Step 2] 跳过热力图 (--no-image)")
        sys.stdout.flush()

    # ---- Step 3: 生成排名数据 ----
    step_start = datetime.now()
    if not no_rank:
        print("\n" + "#" * 65)
        print("# Step 3/5: 生成排名数据 (sw2_score_rank.py)")
        print("# 时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        print("#" * 65)
        sys.stdout.flush()

        ok, _, _ = run_script("sw2_score_rank.py", label="评分排名")
        results["rank"] = ok
        step_elapsed = (datetime.now() - step_start).total_seconds()
        print("   [Step 3] 评分排名: {} (用时: {:.1f}s)".format(
            "成功" if ok else "失败",
            step_elapsed
        ))
        sys.stdout.flush()
        if not ok:
            errors.append("score rank 失败")
    else:
        print("   [Step 3] 跳过排名数据 (--no-rank)")
        sys.stdout.flush()

    # ---- Step 4: 收集产物路径 ----
    step_start = datetime.now()
    print("\n" + "#" * 65)
    print("# Step 4/5: 收集产物路径")
    print("# 时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("#" * 65)
    sys.stdout.flush()

    today_str = date.today().strftime("%Y-%m-%d")

    report_path = find_latest_file(os.path.join(BASE_DIR, "report"), "action_report_")
    rank_csv = find_latest_file(os.path.join(BASE_DIR, "rank"), "sw2_score_rank_")
    width_img = find_latest_file(os.path.join(BASE_DIR, "width"), "_heatmap.png")
    cong_img = find_latest_file(os.path.join(BASE_DIR, "congestion"), "_heatmap.png")

    print("   [Step 4] 报告路径: {}".format(report_path or "未找到"))
    print("   [Step 4] 排名路径: {}".format(rank_csv or "未找到"))
    print("   [Step 4] 宽度图: {}".format(width_img or "未找到"))
    print("   [Step 4] 拥挤度图: {}".format(cong_img or "未找到"))
    sys.stdout.flush()

    data_date = today_str
    if report_path:
        import re
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(report_path))
        if m:
            data_date = m.group(1)
    print("   [Step 4] 数据日期: {}".format(data_date))
    sys.stdout.flush()

    output_path = os.path.join(OUTPUT_DIR, "daily_{}.html".format(data_date))
    step_elapsed = (datetime.now() - step_start).total_seconds()
    print("   [Step 4] 完成 (用时: {:.1f}s)".format(step_elapsed))
    sys.stdout.flush()

    # ---- Step 5: 生成 RSS HTML ----
    step_start = datetime.now()
    print("\n" + "#" * 65)
    print("# Step 5/5: 生成 RSS HTML")
    print("# 时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("#" * 65)
    sys.stdout.flush()

    ok = generate_rss_html(
        report_path=report_path,
        rank_csv=rank_csv,
        width_img=width_img,
        congestion_img=cong_img,
        output_path=output_path,
        data_date=data_date,
    )
    results["rss_html"] = ok
    step_elapsed = (datetime.now() - step_start).total_seconds()
    print("   [Step 5] 生成 RSS HTML: {} (用时: {:.1f}s)".format(
        "成功" if ok else "失败",
        step_elapsed
    ))
    sys.stdout.flush()

    # ---- 总结 ----
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 65)
    print("   [*] 完成! 总用时: {:.1f}s".format(elapsed))
    print("-" * 65)
    status_icon = "[OK]" if all(results.values()) else "[FAIL]"
    print("   {} 报告: {} | 热力图: {} | 排名: {} | 整合: {}".format(
        status_icon,
        "[OK]" if results.get("report", True) else "[FAIL]",
        "[OK]" if results.get("heatmap", True) else "[FAIL]",
        "[OK]" if results.get("rank", True) else "[FAIL]",
        "[OK]" if results.get("rss_html", False) else "[FAIL]",
    ))
    print("-" * 65)
    if errors:
        print("   [ERROR] 错误列表: {}".format(", ".join(errors)))
    print("   [*] 输出文件:")
    if report_path:
        print("       报告: {}".format(report_path))
    if rank_csv:
        print("       排名: {}".format(rank_csv))
    if width_img:
        print("       宽度图: {}".format(width_img))
    if cong_img:
        print("       拥挤度图: {}".format(cong_img))
    print("       整合: {}".format(output_path))
    print("=" * 65)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
