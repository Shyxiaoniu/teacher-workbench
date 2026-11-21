#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""眼睛工具：给创意馆宠物做「睁眼层」和「闭眼帧」共用的一套算法。

牛哥 2026-11-21 的反馈：闭眼后像「眼睛肿了」。原因是旧做法——在眼睛上盖一块
纯毛色的椭圆、再画一条很粗的大弧线；那块椭圆比周围亮、边缘又被羽化，看起来就
像个发亮的鼓包。

现在的做法分两步，都不画「色块」：

1) 修补皮肤：先把眼睛（虹膜/瞳孔/眼白/原本的 ^ ^ 线）找出来，逐列用「眼上缘
   颜色 → 眼下缘颜色」做线性插值填掉。因为填补色来自同一列上下的真实皮肤，
   自带脸部的竖向渐变，不会出现比周围亮的斑。

2) 画眼皮：一条中间粗、两端收尖的浅弧（两条弧线围成的透镜形），弧度浅、宽度
   约等于实测眼宽的 0.9，粗细约等于眼高的 0.13 —— 眼睛小的宠物线就细，眼睛
   大的就粗，不再一律套同一个尺寸。

所有尺寸都来自每只宠物的实测眼睛包围盒，所以不同宠物各自适配。
"""
from __future__ import annotations

import os
from PIL import Image, ImageChops, ImageDraw, ImageFilter

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "typing", "studio")
PETS = os.path.join(BASE, "pets")
EYES_DIR = os.path.join(BASE, "eyes")
BLINK_DIR = os.path.join(BASE, "blink")


# ---------------------------------------------------------------- 基础小工具
def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def _dist2(c1, c2):
    return sum((c1[i] - c2[i]) ** 2 for i in range(3))


def median_rgb(pixels):
    if not pixels:
        return (210, 190, 170)
    s = sorted(pixels, key=lambda p: sum(p))
    return s[len(s) // 2]


def ring_color(im, bbox, pad=None):
    """眼睛外圈一圈浅色像素的中位数，当作「这里本来就是皮肤」的基准色。"""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    pad = pad or max(10, int(min(w, h) * 0.35))
    px = im.load()
    W, H = im.size
    samples = []
    for yy in range(max(0, y1 - pad), min(H, y2 + pad), 3):
        for xx in range(max(0, x1 - pad), min(W, x2 + pad), 3):
            if x1 <= xx < x2 and y1 <= yy < y2:
                continue
            r, g, b, a = px[xx, yy]
            if a < 180 or (r + g + b) / 3 < 110:
                continue
            samples.append((r, g, b))
    return median_rgb(samples)


def _label_components(mask, W, H):
    """4 邻域连通域标记，返回 [(像素数, [索引...]), ...]，索引 = y*W+x。"""
    data = list(mask)
    seen = bytearray(len(data))
    comps = []
    for start in range(len(data)):
        if not data[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        pix = []
        while stack:
            i = stack.pop()
            pix.append(i)
            y, x = divmod(i, W)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < H and 0 <= nx < W:
                    j = ny * W + nx
                    if data[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)
        comps.append((len(pix), pix))
    comps.sort(key=lambda c: -c[0])
    return comps


def detect_eye(im, seed, tol=62, min_ratio=0.10):
    """在 seed 框里找出真实的眼睛像素，返回 (bbox, mask)。

    判据：与「外圈毛色」差得够远（虹膜有颜色、瞳孔很深、眼白很亮），并且
    与 seed 中心连成一片。这样不会把眉毛、脸上的花纹一起圈进来。
    """
    x1, y1, x2, y2 = seed
    w, h = x2 - x1, y2 - y1
    fur = ring_color(im, seed)
    px = im.load()
    W, H = im.size
    gw, gh = x2 - x1, y2 - y1
    raw = bytearray(gw * gh)
    for yy in range(gh):
        for xx in range(gw):
            gx, gy = x1 + xx, y1 + yy
            if not (0 <= gx < W and 0 <= gy < H):
                continue
            r, g, b, a = px[gx, gy]
            if a < 180:
                continue
            if _dist2((r, g, b), fur) > tol * tol:
                raw[yy * gw + xx] = 1
    if not any(raw):
        return seed, Image.new('L', (gw, gh), 0)

    comps = _label_components(raw, gw, gh)
    cx, cy = gw // 2, gh // 2
    best = None
    for size, pix in comps:
        if size < gw * gh * min_ratio:
            continue
        touches_center = any(abs(i % gw - cx) < gw * 0.34 and abs(i // gw - cy) < gh * 0.34
                            for i in pix)
        if touches_center:
            best = (size, pix)
            break
    if not best:
        best = comps[0]

    keep = bytearray(gw * gh)
    for i in best[1]:
        keep[i] = 1
    bx1 = by1 = 10 ** 9
    bx2 = by2 = -1
    for i in best[1]:
        yy, xx = divmod(i, gw)
        bx1, bx2 = min(bx1, xx), max(bx2, xx)
        by1, by2 = min(by1, yy), max(by2, yy)
    bbox = (x1 + bx1, y1 + by1, x1 + bx2 + 1, y1 + by2 + 1)
    # 注意：raw/keep 里存的是 0/1，转成 L 图必须放到 0/255，否则整张遮罩等于空
    return bbox, Image.frombytes('L', (gw, gh), bytes(255 if v else 0 for v in keep))


def dark_line_mask(im, box, ink, tol=54):
    """线稿宠物（原图是 ^ ^）：把深色线找出来，返回 (bbox, mask, 偏移)。"""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    px = im.load()
    W, H = im.size
    gw, gh = w, h
    raw = bytearray(gw * gh)
    for yy in range(gh):
        for xx in range(gw):
            gx, gy = x1 + xx, y1 + yy
            if not (0 <= gx < W and 0 <= gy < H):
                continue
            r, g, b, a = px[gx, gy]
            if a > 110 and _dist2((r, g, b), ink) < tol * tol:
                raw[yy * gw + xx] = 1
    if not any(raw):
        return None, Image.new('L', (gw, gh), 0), (x1, y1)
    comps = _label_components(raw, gw, gh)
    keep = bytearray(gw * gh)
    bx1 = by1 = 10 ** 9
    bx2 = by2 = -1
    for size, pix in comps[:3]:          # 一条 ^ 可能被分成两段
        if size < 40:
            continue
        for i in pix:
            keep[i] = 1
            yy, xx = divmod(i, gw)
            bx1, bx2 = min(bx1, xx), max(bx2, xx)
            by1, by2 = min(by1, yy), max(by2, yy)
    if bx2 < 0:
        return None, Image.frombytes('L', (gw, gh), bytes(255 if v else 0 for v in keep)), (x1, y1)
    bbox = (x1 + bx1, y1 + by1, x1 + bx2 + 1, y1 + by2 + 1)
    return bbox, Image.frombytes('L', (gw, gh), bytes(255 if v else 0 for v in keep)), (x1, y1)


# ---------------------------------------------------------------- 皮肤修补
def cover_ellipse(box, inset=0.0):
    """按眼睛包围盒生成一个内切椭圆遮罩（返回 mask, origin）。

    为什么用椭圆而不是「检测出来的眼睛像素」：有些宠物的眼珠和贴着眼睛的
    绿叶子 / 帽檐是连着的，按连通域会把花纹一起吃掉。椭圆范围是逐只调过的，
    正好圈住眼珠又碰不到旁边的花纹。
    """
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if inset:
        x1, y1 = int(x1 + w * inset), int(y1 + h * inset)
        x2, y2 = int(x2 - w * inset), int(y2 - h * inset)
        w, h = x2 - x1, y2 - y1
    m = Image.new('L', (w, h), 0)
    ImageDraw.Draw(m).ellipse((0, 0, w - 1, h - 1), fill=255)
    return m, (x1, y1)


def band_color(im, y, x1, x2, h=6, bright=110):
    """取一条横带的中间色（优先亮一点的像素），用来当渐变的上/下端点色。"""
    px = im.load()
    W, H = im.size
    out = []
    for yy in range(max(0, y - h), min(H, y + h + 1)):
        for xx in range(max(0, x1), min(W, x2), 2):
            r, g, b, a = px[xx, yy]
            if a < 180:
                continue
            if (r + g + b) / 3 >= bright:
                out.append((r, g, b))
    return median_rgb(out)


def composite_mask(parts, size):
    """把多块 (mask, origin) 合成一张整幅图的遮罩。"""
    full = Image.new('L', size, 0)
    for mask, origin in parts:
        tmp = Image.new('L', size, 0)
        tmp.paste(mask, origin)
        full = ImageChops.lighter(full, tmp)
    return full


def inpaint(im, mask, origin, grow=3, blur=1.0, skin=None, skin_tol=70,
            top_rgb=None, bot_rgb=None):
    """补皮肤：把眼睛区域填成「眼上缘色 → 眼下缘色」的竖向渐变。

    关键点（2026-11-21 踩坑）：
    · 边界色要在整幅图里往上/往下找，不能只在遮罩矩形里找 —— 膨胀后遮罩常常
      顶到矩形上下边，搜索区间是空的，那一列会被整列跳过，结果是竖条纹。
    · 每列各用自己那对边界色也会竖条纹（相邻列有跳变）。所以先用所有列的中位色
      定下「上色 / 下色」，整块统一渐变；个别列的味道差异交给最后的水平平滑。
    · skin 给定时，找边界色会跳过「不像皮肤」的像素（贴着眼睛的绿叶子、帽檐），
      避免把花纹颜色抹进眼窝。
    """
    out = im.copy()
    ox, oy = origin
    mw, mh = mask.size
    m = mask.filter(ImageFilter.MaxFilter(grow * 2 + 1)) if grow else mask
    mp = m.load()
    px = out.load()
    W, H = out.size

    def ok(c):
        return c is not None and (skin is None or _dist2(c[:3], skin) < skin_tol ** 2)

    def edge(gx, gy0, step):
        """从 (gx, gy0) 出发按 step 上下找第一个「像皮肤」的颜色。"""
        fallback = None
        for d in range(1, 90):
            gy = gy0 + step * d
            if not (0 <= gy < H):
                break
            c = px[gx, gy]
            if ok(c):
                return c
            if fallback is None:
                fallback = c
        return fallback

    cols = []
    for xx in range(mw):
        ys = [yy for yy in range(mh) if mp[xx, yy] > 100]
        if not ys:
            continue
        gx = ox + xx
        if not (0 <= gx < W):
            continue
        ca = edge(gx, oy + ys[0], -1)
        cb = edge(gx, oy + ys[-1], +1)
        cols.append((xx, ys[0], ys[-1], ca or cb, cb or ca))

    if not cols:
        return out

    if top_rgb is None or bot_rgb is None:
        ups = [c[3] for c in cols if c[3]]
        downs = [c[4] for c in cols if c[4]]
        top_rgb = top_rgb or (median_rgb([c[:3] for c in ups]) if ups else (200, 200, 200))
        bot_rgb = bot_rgb or (median_rgb([c[:3] for c in downs]) if downs else top_rgb)

    # 整块用同一条竖向渐变（不是每列各算一遍）：遮罩可能是「椭圆 + 弧线」拼出来的
    # 不规则形状，逐列算会让相邻列的起止点不同，又会排出竖条纹。
    gy1 = min(oy + c[1] for c in cols)
    gy2 = max(oy + c[2] for c in cols)
    span = max(1, gy2 - gy1)
    for xx, top, bot, _ca, _cb in cols:
        gx = ox + xx
        for yy in range(top, bot + 1):
            gy = oy + yy
            if not (0 <= gy < H):
                continue
            if mp[xx, yy] < 60:
                continue
            a = px[gx, gy][3]
            px[gx, gy] = _lerp(top_rgb, bot_rgb, (gy - gy1) / span) + (a,)

    if blur:
        # 统一渐变已经很干净，这一步只是把边界缝和残留的竖向细纹再柔一下。
        smooth_h(out, m, ox, oy, radius=max(3, int(mw * 0.06)))

    # 羽化接缝：补出来的肤色和周围毛色不可能完全一样，硬边会显出一块「椭圆补丁」。
    # 在遮罩内侧做一圈 alpha 渐变，把补丁边缘揉回原图。
    feather = max(2, int(mw * 0.045))
    alpha = ImageChops.darker(m, m.filter(ImageFilter.GaussianBlur(feather)))
    full = Image.new('L', out.size, 0)          # alpha 要铺到整幅图才能 composite
    full.paste(alpha, (ox, oy))
    out = Image.composite(out, im, full)
    return out


def smooth_h(im, mask, ox, oy, radius=8):
    """在遮罩范围内做水平滑动平均（只动 x，不动 y），消掉逐列插值的竖条纹。"""
    px = im.load()
    mp = mask.load()
    mw, mh = mask.size
    W, H = im.size
    src = im.copy().load()
    for yy in range(mh):
        row = [xx for xx in range(mw) if mp[xx, yy] > 60]
        if not row:
            continue
        for xx in row:
            gx, gy = ox + xx, oy + yy
            if not (0 <= gx < W and 0 <= gy < H):
                continue
            acc = [0, 0, 0, 0]
            n = 0
            for dx in range(-radius, radius + 1):
                nx = xx + dx
                if 0 <= nx < mw and mp[nx, yy] > 60:
                    c = src[ox + nx, gy]
                    acc[0] += c[0]
                    acc[1] += c[1]
                    acc[2] += c[2]
                    acc[3] += c[3]
                    n += 1
            if n:
                px[gx, gy] = (int(acc[0] / n), int(acc[1] / n),
                              int(acc[2] / n), int(acc[3] / n))


# ---------------------------------------------------------------- 画眼皮
def lid_points(bbox, span_ratio=0.92, dip_ratio=0.16, thickness_ratio=0.15,
               center_ratio=0.52):
    """按实测眼框算出一条「中间粗两端尖」的眼皮透镜形（上弧 + 下弧两个点列）。"""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    cy = y1 + h * center_ratio
    half = w * span_ratio / 2
    dip = h * dip_ratio
    th = max(2.0, h * thickness_ratio)

    def arc(t, off):
        # t: -1..1，横向；r 形浅弧，末端略上挑
        y = cy + dip * (1 - t * t)            # 中间最低
        y -= dip * 0.42 * (t * t) * (t * t)   # 两头再往上收一点，像真实眼皮
        return (cx + t * half, y + off)

    upper, lower, n = [], [], 34
    for i in range(n + 1):
        t = -1 + 2 * i / n
        taper = (1 - t * t) ** 0.55          # 两端收到 0，中间最粗
        upper.append(arc(t, -th * taper / 2))
        lower.append(arc(t, th * taper / 2))
    return upper, lower


def draw_lid(draw, bbox, ink, alpha=255, **kw):
    upper, lower = lid_points(bbox, **kw)
    draw.polygon(upper + lower[::-1], fill=tuple(ink) + (alpha,))
    # 两端补圆点，收尖处不至于太锋利
    for pt in (upper[0], lower[0], upper[-1], lower[-1]):
        r = max(1, int((bbox[3] - bbox[1]) * 0.018))
        draw.ellipse((pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r),
                     fill=tuple(ink) + (alpha,))


def draw_lashes(draw, bbox, ink, side='R'):
    """外眼角两根短睫毛，朝外上方翘。"""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    ex = (x2 if side == 'R' else x1)
    ey = y1 + h * 0.62
    lw = max(2, int(h * 0.075))
    for k, (dx, dy) in enumerate(((w * 0.16, -h * 0.16), (w * 0.10, h * 0.02))):
        s = 1 if side == 'R' else -1
        draw.line([(ex - s * w * 0.04, ey + dy * 0.25), (ex + s * dx, ey + dy)],
                  fill=tuple(ink) + (255,), width=max(1, lw - k))


def save_png(im, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)
    print('  ->', os.path.relpath(path, BASE))
