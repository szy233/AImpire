#!/usr/bin/env python3
"""生成 AImpire PWA 图标 — 皇冠 (Crown) 设计"""
import struct, zlib, os

BG     = (10, 10, 15)
ACCENT = (0, 229, 160)


def point_in_polygon(px, py, poly):
    inside = False
    x0, y0 = poly[-1]
    for x1, y1 in poly:
        if ((y1 > py) != (y0 > py)) and (px < (x0 - x1) * (py - y1) / (y0 - y1) + x1):
            inside = not inside
        x0, y0 = x1, y1
    return inside


def create_icon(size):
    # 皇冠多边形（归一化 0..1，与 SVG 路径 "M1,17 L1,12 L4.5,4 L8.5,12 L11,1..." 等比）
    crown = [
        (0.10, 0.88),
        (0.10, 0.60),
        (0.27, 0.22),
        (0.40, 0.60),
        (0.50, 0.12),
        (0.60, 0.60),
        (0.73, 0.22),
        (0.90, 0.60),
        (0.90, 0.88),
    ]

    # 三个尖端宝石
    gems = [
        (0.27, 0.22, 0.062),   # 左尖
        (0.50, 0.12, 0.072),   # 中尖（最高，稍大）
        (0.73, 0.22, 0.062),   # 右尖
    ]

    bg_r = 0.20 * size  # 圆角半径

    def in_rounded_square(x, y):
        cx = cy = size / 2
        hw = size * 0.48
        dx = max(abs(x - cx) - hw + bg_r, 0)
        dy = max(abs(y - cy) - hw + bg_r, 0)
        return dx * dx + dy * dy <= bg_r * bg_r

    pixels = []
    for py in range(size):
        row = []
        for px in range(size):
            nx, ny = px / size, py / size

            if not in_rounded_square(px, py):
                row.extend([0, 0, 0])
                continue

            # 背景渐变（中心暗，四角稍亮）
            dist = ((px - size/2)**2 + (py - size/2)**2) ** 0.5 / (size * 0.65)
            rb = int(BG[0] + 22 * dist)
            gb = int(BG[1] + 18 * dist)
            bb = int(BG[2] + 30 * dist)

            in_gem = any(
                (nx - gx)**2 + (ny - gy)**2 <= gr**2
                for gx, gy, gr in gems
            )
            in_crown = point_in_polygon(nx, ny, crown)

            if in_gem:
                # 宝石：高亮绿
                row.extend([
                    min(255, ACCENT[0] + 30),
                    min(255, ACCENT[1] + 26),
                    min(255, ACCENT[2] + 20),
                ])
            elif in_crown:
                # 皇冠：顶部亮、底部稍暗，竖向渐变
                t = max(0.0, (ny - 0.12) / 0.76)
                r = int(ACCENT[0] * (1 - t * 0.14))
                g = int(ACCENT[1] * (1 - t * 0.08))
                b = int(ACCENT[2] * (1 - t * 0.20))
                row.extend([r, g, b])
            else:
                row.extend([rb, gb, bb])

        pixels.append(bytes(row))

    # 编码为 PNG（纯 Python，无依赖）
    def chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    raw  = b''.join(b'\x00' + row for row in pixels)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )


out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
os.makedirs(out_dir, exist_ok=True)

for size in [192, 512]:
    data = create_icon(size)
    path = os.path.join(out_dir, f'icon-{size}.png')
    with open(path, 'wb') as f:
        f.write(data)
    print(f'✓ {path}  ({len(data):,} bytes)')
