# make_icon.py — 生成 vpnkit 多尺寸 ICO
# 纯标准库：手写 PNG（zlib + struct）与 ICO 容器，不依赖 PIL / GDI+。
# 用法: python bin\make_icon.py [输出路径]
import math
import struct
import sys
import zlib
from pathlib import Path

OUT = sys.argv[1] if len(sys.argv) > 1 else str(
    Path(__file__).resolve().parent.parent / "vpnkit.ico"
)
SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # 每轴超采样数（4x4 = 16 次/像素，足够干净）

# 配色
C1 = (37, 99, 235)    # #2563EB 深蓝
C2 = (6, 182, 212)    # #06B6D4 青
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return a + (b - a) * t


def in_round_rect(x, y, x0, y0, x1, y1, r):
    """点是否落在圆角矩形内。"""
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def in_shield(x, y, sx, sy, sw, sh):
    """盾牌：上半矩形 + 下半椭圆收尖。"""
    u = (x - sx) / sw
    v = (y - sy) / sh
    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return False
    half = 0.5
    knee = 0.52
    if v > knee:
        t = (v - knee) / (1.0 - knee)
        half = 0.5 * math.sqrt(max(0.0, 1.0 - t * t))
    return abs(u - 0.5) <= half


def seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return math.sqrt(dx * dx + dy * dy)


def render(size):
    """返回 RGBA 字节串（自上而下，每行 size*4）。"""
    pad = size * 0.045
    side = size - 2 * pad
    rad = side * 0.235

    # 盾牌框（位于圆角方形内，略偏上）
    sw = side * 0.60
    sh = side * 0.68
    sx = pad + (side - sw) / 2.0
    sy = pad + side * 0.165

    # 对勾三点
    p1 = (sx + sw * 0.28, sy + sh * 0.47)
    p2 = (sx + sw * 0.45, sy + sh * 0.65)
    p3 = (sx + sw * 0.75, sy + sh * 0.31)
    tick_w = side * 0.078

    rows = []
    inv = 1.0 / SS
    nsub = SS * SS

    for py in range(size):
        row = bytearray()
        for px in range(size):
            base_a = 0.0
            sh_a = 0.0
            tk_a = 0.0
            # 底色渐变取像素中心
            gx = (px + 0.5) / size
            gy = (py + 0.5) / size
            for i in range(SS):
                for j in range(SS):
                    x = px + (i + 0.5) * inv
                    y = py + (j + 0.5) * inv
                    if not in_round_rect(x, y, pad, pad, pad + side, pad + side, rad):
                        continue
                    base_a += 1.0
                    if in_shield(x, y, sx, sy, sw, sh):
                        sh_a += 1.0
                        if size >= 24:
                            d = min(
                                seg_dist(x, y, p1[0], p1[1], p2[0], p2[1]),
                                seg_dist(x, y, p2[0], p2[1], p3[0], p3[1]),
                            )
                            if d <= tick_w * 0.5:
                                tk_a += 1.0
            base_a /= nsub
            sh_a /= nsub
            tk_a /= nsub

            if base_a <= 0.004:
                row += b"\x00\x00\x00\x00"
                continue

            # 渐变底色
            t = max(0.0, min(1.0, (gx * 0.45 + gy * 0.55)))
            r = lerp(C1[0], C2[0], t)
            g = lerp(C1[1], C2[1], t)
            b = lerp(C1[2], C2[2], t)

            # 叠白色盾牌
            if sh_a > 0.0:
                k = sh_a / base_a
                r = lerp(r, WHITE[0], k)
                g = lerp(g, WHITE[1], k)
                b = lerp(b, WHITE[2], k)
            # 叠蓝色对勾
            if tk_a > 0.0:
                k = min(1.0, tk_a / base_a)
                r = lerp(r, C1[0], k)
                g = lerp(g, C1[1], k)
                b = lerp(b, C1[2], k)

            row += bytes((int(r + 0.5), int(g + 0.5), int(b + 0.5), int(base_a * 255 + 0.5)))
        rows.append(bytes(row))
    return b"".join(rows)


def png(size, raw):
    """把裸 RGBA 数据封成 PNG。"""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    stride = size * 4
    # 每行前置 filter byte 0
    filtered = bytearray()
    for y in range(size):
        filtered.append(0)
        filtered += raw[y * stride:(y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(filtered), 9))
        + chunk(b"IEND", b"")
    )


def main():
    payloads = []
    for s in SIZES:
        data = png(s, render(s))
        payloads.append((s, data))
        print(f"  {s:3d}x{s:<3d} {len(data):6d} bytes")

    out = bytearray()
    out += struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    for s, data in payloads:
        dim = 0 if s >= 256 else s
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in payloads:
        out += data

    with open(OUT, "wb") as f:
        f.write(bytes(out))
    print(f"OK -> {OUT}  ({len(out)} bytes, {len(payloads)} sizes)")


if __name__ == "__main__":
    main()
