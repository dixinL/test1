"""
热力图生成器 (从本地CSV数据生成)

职责: 读取 width/ 或 congestion/ 目录下的CSV，生成 PNG图片 和 HTML交互页面

使用方式:
  python draw_heatmap.py [数据类型] [输出格式]

数据类型:
  width       均线市场宽度 (默认)
  congestion  行业拥挤度

输出格式:
  png   静态图片 (默认)
  html  交互式网页
  both  同时生成两者

示例:
  python draw_heatmap.py                        # 宽度 + 图片
  python draw_heatmap.py width png              # 宽度 + 图片
  python draw_heatmap.py width html             # 宽度 + 网页
  python draw_heatmap.py congestion png         # 拥挤度 + 图片
  python draw_heatmap.py congestion html        # 拥挤度 + 网页
  python draw_heatmap.py congestion both        # 拥挤度 + 两者

前置步骤 (如无数据会自动触发):
  python sw2_market_width_api.py              # 先拉取数据
"""

import os
import sys
from datetime import date

import sw2_market_width_api as api
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WIDTH_DIR = os.path.join(BASE_DIR, 'width')
CONGESTION_DIR = os.path.join(BASE_DIR, 'congestion')
HEATMAP_WINDOW = 30


def _seq_color(val, vmax=100):
    """
    顺序型蓝->黄颜色方案
    val = vmax (100) -> 深蓝 (0, 0, 255)
    val = 50         -> 黄色 (255, 255, 0)
    val = 0         -> 橙色 (255, 128, 0)
    val < 0         -> 浅灰 (空值)
    """
    if val <= 0:
        return (245, 245, 245)
    ratio = val / float(vmax)
    if ratio >= 0.5:
        t = (ratio - 0.5) / 0.5
        r = int(255 * (1 - t))
        g = int(255 * (1 - t))
        b = int(0 + 255 * t)
    else:
        t = ratio / 0.5
        r = 255
        g = int(128 + 127 * t)
        b = 0
    return (r, g, b)


def _get_color_hex(val, vmax):
    r, g, b = _seq_color(val, vmax)
    return 'rgb({},{},{})'.format(r, g, b)


def _get_text_col(val, vmax):
    if val <= 0:
        return '#999'
    if val >= 60:
        return 'white'
    return '#333'


def _rgb_color(val, vmax):
    return _seq_color(val, vmax)


def _text_color(val, vmax):
    if val <= 0:
        return (153, 153, 153)
    if val >= 60:
        return (255, 255, 255)
    return (51, 51, 51)


def generate_html(matrix, row_names, dates, col_labels, vmax, title, output_path):
    n_rows = len(row_names)
    n_cols = len(col_labels)

    lines = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
        '<title>{}</title>'.format(title),
        '<style>\n*{margin:0;padding:0;box-sizing:border-box}\n'
        'body{font-family:"Microsoft YaHei",SimHei,Arial,sans-serif;background:#fafafa;padding:20px}\n'
        '.container{max-width:1800px;margin:0 auto;background:#fff;padding:30px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.1)}\n'
        'h1{text-align:center;color:#333;margin-bottom:10px;font-size:22px}\n'
        '.subtitle{text-align:center;color:#666;margin-bottom:25px;font-size:14px}\n'
        '.table-wrapper{overflow-x:auto}\n'
        'table{border-collapse:collapse;width:max-content;margin:0 auto;font-size:12px}\n'
        'th,td{border:1px solid #e0e0e0;padding:4px 6px;text-align:center;min-width:38px;height:28px;white-space:nowrap}\n'
        'th{background:#f0f0f0;font-weight:600;color:#555;position:sticky;top:0;z-index:2}\n'
        'th.date-header{font-size:11px}\n'
        '.row-label{position:sticky;left:0;background:#fff;z-index:1;font-weight:500;text-align:left;padding-left:10px;min-width:110px}\n'
        '.row-label-right{text-align:left;padding-left:10px;min-width:110px;background:#fafafa}\n'
        '.cell-value{font-weight:500;font-size:11px}\n'
        'tr:hover .row-label{background:#e8f5e9}\n'
        '.legend{display:flex;align-items:center;justify-content:center;gap:15px;margin-top:20px;flex-wrap:wrap}\n'
        '.legend-bar{width:200px;height:16px;border-radius:3px;background:linear-gradient(to right,#ff8000,#ffff00,#0000ff)}\n'
        '.stats{text-align:center;color:#888;margin-top:15px;font-size:13px}\n'
        '.highlight-row{background-color:#fffde7!important}\n'
        '</style>',
        '</head><body><div class="container">',
        '<h1>{}</h1>'.format(title.replace('\n', '<br>')),
        '<div class="subtitle">{} ~ {} | 共 {} 个交易日 | {} 个行业</div>'.format(
            dates[0] if dates else '',
            dates[-1] if dates else '',
            n_cols, n_rows),
        '<div class="table-wrapper"><table>',
        '<thead><tr><th>行业名称</th>',
    ]

    for dl in col_labels:
        lines.append('<th class="date-header">{}</th>'.format(dl))
    lines.append('<th>行业名称</th></tr></thead><tbody>')

    for i in range(n_rows):
        name = row_names[i]
        hl = ' class="highlight-row"' if name == '行业平均' else ''
        lines.append('<tr{}>'.format(hl))
        lines.append('<td class="row-label">{}</td>'.format(name))

        row = matrix[i] if i < len(matrix) else []
        for j in range(n_cols):
            val = row[j] if j < len(row) else 0
            bg = _get_color_hex(val, vmax)
            tc = _get_text_col(val, vmax)
            if val != 0:
                lines.append(
                    '<td style="background:{};color:{}" class="cell-value">{}</td>'.format(
                        bg, tc, val))
            else:
                lines.append('<td style="background:#f9f9f9"></td>')

        lines.append('<td class="row-label-right">{}</td>'.format(name))
        lines.append('</tr>')

    lines.extend([
        '</tbody></table></div>',
        '<div class="legend">',
        '<span>偏低 (黄)</span><div class="legend-bar"></div><span>偏高 (蓝)</span>',
        '</div>',
        '<div class="stats">数据来源: legulegu.com | 更新时间: {} | 自动生成</div>'.format(
            dates[-1] if dates else ''),
        '</div></body></html>',
    ])

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print('[HTML] {} ({}x{})'.format(output_path, n_rows, n_cols))


_HAS_PIL = False
try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    pass


def _load_font(size, fallback=None):
    fonts_to_try = ['msyh.ttc', 'simhei.ttf']
    for fname in fonts_to_try:
        try:
            return ImageFont.truetype(fname, size)
        except Exception:
            continue
    return fallback or ImageFont.load_default()


def generate_png(matrix, row_names, dates, col_labels, vmax, title, output_path):
    if not _HAS_PIL:
        print('[Skip] 未安装 Pillow。安装: pip install pillow')
        return None

    n_rows = len(row_names)
    n_cols = len(col_labels)
    cell_w, cell_h = 36, 24
    label_w = right_label_w = 115
    header_h, title_h, subtitle_h, legend_h, padding = 40, 50, 28, 40, 20

    img_w = padding + label_w + n_cols * cell_w + right_label_w + padding
    img_h = padding + title_h + subtitle_h + header_h + n_rows * cell_h + legend_h + padding

    img = Image.new('RGB', (img_w, img_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(18)
    font_subtitle = _load_font(11)
    font_header = _load_font(9)
    font_cell = _load_font(9)
    font_label = _load_font(9)

    # 标题
    for idx, line in enumerate(title.split('\n')):
        f = font_title if idx == 0 else font_subtitle
        bbox = draw.textbbox((0, 0), line, font=f)
        tw = bbox[2] - bbox[0]
        draw.text(((img_w - tw) // 2, padding + idx * 25), line,
                  fill=(51, 51, 51), font=f)

    header_y = padding + title_h + subtitle_h
    x_start = padding + label_w

    # 表头背景
    draw.rectangle([x_start - label_w, header_y, x_start, header_y + header_h],
                   fill=(240, 240, 240))

    # 列头
    for j in range(n_cols):
        cx = x_start + j * cell_w
        draw.rectangle([cx, header_y, cx + cell_w, header_y + header_h],
                       fill=(240, 240, 240), outline=(220, 220, 220))
        tb = draw.textbbox((0, 0), col_labels[j], font=font_header)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
        draw.text((cx + (cell_w - tw) // 2,
                   header_y + (header_h - th) // 2),
                  col_labels[j], fill=(85, 85, 85), font=font_header)

    rx = x_start + n_cols * cell_w
    draw.rectangle([rx, header_y, rx + right_label_w, header_y + header_h],
                   fill=(240, 240, 240), outline=(220, 220, 220))

    data_y = header_y + header_h

    # 数据行
    for i in range(n_rows):
        name = row_names[i]
        row_y = data_y + i * cell_h
        is_avg = name == '行业平均'
        hc = (255, 253, 231) if is_avg else None
        dn = name[:7] if len(name) > 7 else name
        lx = padding

        if hc:
            draw.rectangle([lx, row_y, lx + label_w, row_y + cell_h], fill=hc)
        draw.rectangle([lx, row_y, lx + label_w, row_y + cell_h],
                       outline=(230, 230, 230))
        draw.text((lx + 5, row_y + 4), dn, fill=(60, 60, 60), font=font_label)
        draw.line([lx + label_w, row_y, lx + label_w, row_y + cell_h],
                  fill=(220, 220, 220))

        row = matrix[i] if i < len(matrix) else []
        for j in range(n_cols):
            cx = x_start + j * cell_w
            val = row[j] if j < len(row) else 0

            bg = _rgb_color(val, vmax) if not (hc and val == 0) else hc

            draw.rectangle([cx, row_y, cx + cell_w, row_y + cell_h],
                           fill=bg, outline=(210, 210, 210))

            if val != 0:
                tc = _text_color(val, vmax)
                tb2 = draw.textbbox((0, 0), str(val), font=font_cell)
                tw2 = tb2[2] - tb2[0]
                th2 = tb2[3] - tb2[1]
                draw.text((cx + (cell_w - tw2) // 2,
                           row_y + (cell_h - th2) // 2),
                          str(val), fill=tc, font=font_cell)

        rrx = x_start + n_cols * cell_w
        if hc:
            draw.rectangle([rrx, row_y, rrx + right_label_w, row_y + cell_h], fill=hc)
        draw.text((rrx + 5, row_y + 4), dn, fill=(100, 100, 100), font=font_label)

    # 底部图例
    bottom_y = data_y + n_rows * cell_h
    draw.line([padding, bottom_y, img_w - padding, bottom_y], fill=(200, 200, 200))
    ly = bottom_y + 15
    low_text = '偏低 (黄)'
    draw.text((x_start, ly + 5), low_text, fill=(128, 128, 128),
              font=_load_font(9, font_label))

    bar_x = x_start + 70
    bar_w = 180
    bar_h = 16
    for px in range(bar_w):
        t = px / float(bar_w)
        if t <= 0.5:
            r = 255
            g = int(255 * (t / 0.5))
            b = 0
        else:
            r = int(255 * (1 - (t - 0.5) / 0.5))
            g = int(255 * (1 - (t - 0.5) / 0.5))
            b = int(255 * ((t - 0.5) / 0.5))
        draw.line([bar_x + px, ly, bar_x + px, ly + bar_h], fill=(r, g, b))
    draw.rectangle([bar_x, ly, bar_x + bar_w, ly + bar_h], outline=(180, 180, 180))
    high_text = '偏高 (蓝)'
    draw.text((bar_x + bar_w + 10, ly + 5), high_text, fill=(128, 128, 128),
              font=_load_font(9, font_label))

    src_text = '数据来源: legulegu.com | 更新时间: {}'.format(dates[-1] if dates else '')
    sb = draw.textbbox((0, 0), src_text, font=_load_font(9, font_label))
    draw.text(((img_w - (sb[2] - sb[0])) // 2, ly + 26), src_text,
              fill=(170, 170, 170), font=_load_font(9, font_label))

    img.save(output_path, 'PNG')
    print('[PNG]  {} ({}x{})'.format(output_path, img_w, img_h))


def _find_latest_csv(directory, prefix):
    if not os.path.isdir(directory):
        return None
    csvs = sorted(
        [f for f in os.listdir(directory) if f.startswith(prefix) and f.endswith('.csv')],
        reverse=True)
    return os.path.join(directory, csvs[0]) if csvs else None


def _find_csv_by_date(directory, prefix, target_date):
    """在目录中查找指定日期的CSV文件"""
    target_name = '{}{}.csv'.format(prefix, target_date)
    path = os.path.join(directory, target_name)
    return path if os.path.exists(path) else None


def _slice_window(data, n):
    """把 build_width_dict/build_congestion_dict 的全历史 dict 截到最近 n 个交易日。

    dates 取尾部 n 个；每个行业的值序列同步取对应尾部，保持列与日期对齐。
    """
    dates = data.get('dates', [])
    if n <= 0 or len(dates) <= n:
        return data
    start = len(dates) - n
    out = {
        'dates': dates[start:],
        'swCodeNames': data.get('swCodeNames', []),
    }
    for field in ('maMarketWidth', 'congestions'):
        if field in data:
            out[field] = {
                code: vals[start:] for code, vals in data[field].items()
            }
    return out


def load_data(data_type='width', as_of_date=None):
    """加载数据：从 SQLite 重建近端窗口矩阵(不再读磁盘 CSV)。

    as_of_date=None 时取最新;否则先截到 <= 该日 再取近端窗口(历史回放各日热力图)。

    返回: (matrix, row_names, dates, col_labels, vmax, data_date)
          data_date 为矩阵最后一列的交易日 (YYYY-MM-DD)。
    """
    if data_type == 'width':
        data = db.build_width_dict(as_of_date=as_of_date)
        value_key = 'value20'
    else:
        data = db.build_congestion_dict(as_of_date=as_of_date)
        value_key = 'turnoverRateFQuantile'

    if not data.get('dates'):
        print('[ERROR] 库内无 {} 数据!'.format(data_type))
        return None

    data = _slice_window(data, HEATMAP_WINDOW)
    matrix, row_names, dates, col_labels, vmax = api.extract_matrix(data, value_key)
    data_date = dates[-1] if dates else date.today().strftime('%Y-%m-%d')

    print('[DB] {}: {} 行业 x {} 天 (至 {})'.format(
        data_type, len(row_names), len(dates), data_date))

    return (matrix, row_names, dates, col_labels, vmax, data_date)


def _extract_date_from_filename(filename):
    """从 CSV 文件名中提取日期: width_2026-06-10.csv -> 2026-06-10"""
    import re
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if m:
        return m.group(1)
    return date.today().strftime('%Y-%m-%d')


def get_output_paths(data_type, ext, data_date=None):
    """返回完整输出路径: 对应文件夹 + 数据日期命名（非今天）"""
    if data_date is None:
        data_date = date.today().strftime('%Y-%m-%d')

    names = {
        'width': 'width_{}'.format(data_date),
        'congestion': 'congestion_{}'.format(data_date),
    }
    base_name = '{}_heatmap'.format(names.get(data_type, data_type))

    target_dir = WIDTH_DIR if data_type == 'width' else CONGESTION_DIR
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, '{}.{}'.format(base_name, ext))


def run(data_type='width', output_format='png', as_of_date=None):
    """从 SQLite 读取数据并生成图表。as_of_date 用于历史回放各日热力图。"""
    print('============================================================')
    type_cn = {
        'width': '均线市场宽度',
        'congestion': '行业拥挤度',
    }[data_type]
    fmt_cn = output_format.upper()
    print('{} | 输出: {}'.format(type_cn, fmt_cn))
    print('============================================================')

    print('\n[1/2] 加载数据...')
    result = load_data(data_type, as_of_date=as_of_date)
    if result is None:
        return None

    matrix, row_names, dates, col_labels, vmax, data_date = result
    print('  尺寸: {}行 x {}列 | 范围: 0~{} | 数据日期: {}'.format(
        len(row_names), len(col_labels), int(vmax), data_date))

    print('\n[2/2] 生成图表...')
    title = api.get_chart_title(data_type, dates)

    outputs = []
    if output_format in ('png', 'both'):
        fn = get_output_paths(data_type, 'png', data_date=data_date)
        try:
            generate_png(matrix, row_names, dates, col_labels, vmax, title, fn)
            outputs.append((fn, 'PNG'))
        except Exception as e:
            print('[Error] PNG生成失败: {}'.format(e))

    if output_format in ('html', 'both'):
        fn = get_output_paths(data_type, 'html', data_date=data_date)
        generate_html(matrix, row_names, dates, col_labels, vmax, title, fn)
        outputs.append((fn, 'HTML'))

    print('\n============================================================')
    print('完成! 输出文件:')
    for fname, desc in outputs:
        print('  - {} ({})'.format(fname, desc))

    return None


def parse_args(argv):
    args = argv[1:] if argv else []
    types = []
    fmt = 'png'
    as_of_date = None

    i = 0
    while i < len(args):
        a = args[i]
        al = a.lower()
        if a == '--date' and i + 1 < len(args):
            as_of_date = args[i + 1].strip()
            i += 2
            continue
        elif a.startswith('--date='):
            as_of_date = a.split('=', 1)[1].strip()
        elif al in ('width', 'congestion'):
            types.append(al)
        elif al in ('png', 'html', 'both'):
            fmt = al
        elif al in ('-h', '--help'):
            return (None, None, None)
        i += 1

    if not types:
        types = ['width', 'congestion']

    return (types, fmt, as_of_date)


def print_usage():
    print("""
热力图生成器

用法:
  python draw_heatmap.py [数据类型...] [输出格式]

数据类型 (默认: width congestion 全部):
  width       均线市场宽度
  congestion  行业拥挤度
  可同时指定多个: python draw_heatmap.py width congestion

输出格式 (默认: png):
  png   静态图片
  html  交互式网页
  both  同时生成两者

示例:
  python draw_heatmap.py                        # 宽度+拥挤度 + 图片
  python draw_heatmap.py width                  # 仅宽度 + 图片
  python draw_heatmap.py congestion             # 仅拥挤度 + 图片
  python draw_heatmap.py width congestion html   # 宽度+拥挤度 + 网页
  python draw_heatmap.py width congestion both    # 宽度+拥挤度 + 图片+网页
""")


if __name__ == '__main__':
    api_types, fmt, as_of_date = parse_args(sys.argv)
    if api_types is None:
        print_usage()
        sys.exit(0)

    print('============================================================')
    fmt_cn = {'png': 'PNG图片', 'html': 'HTML网页', 'both': '图片+网页'}[fmt]
    print('  热力图生成 | 数据: {} | 输出: {}{}'.format(
        ', '.join(api_types), fmt_cn,
        ' | 截至: {}'.format(as_of_date) if as_of_date else ''))
    print('============================================================')

    for t in api_types:
        run(data_type=t, output_format=fmt, as_of_date=as_of_date)

    print('\n============================================================')
    print('  全部完成!')
