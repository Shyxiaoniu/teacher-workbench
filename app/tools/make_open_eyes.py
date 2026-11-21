#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""梅花、小狐的「睁眼层」+「闭眼帧」。

这两只原图是笑眯眼（^ ^），牛哥要求默认睁眼、还能自动眨眼，所以要多一层：
  · studio/eyes/<name>.png   睁眼层，常驻显示，盖住原来的 ^ ^
  · studio/blink/<name>.png  闭眼帧，眨眼时从上往下压

2.0.59 重做：以前是「整张原图复制一份、再把圆眼画上去」，^ ^ 的两头一直露在眼角，
像糊了一道痕。现在先用 eye_kit 把 ^ ^ 按列插值补成皮肤（还能顺手把帽子/蝴蝶结
边上的黄斑、白斑一并解决），再画眼睛；闭眼帧与其它宠物同一套「透镜形眼皮」。
画法照现有睁眼宠物（小猫 / 小龙 / 小鹿）的风格：虹膜几乎填满眼眶、上深下浅、
底部反光圈、竖瞳、左上大白高光 + 右下小白点、同色系描边。

用法：python app/tools/make_open_eyes.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter

import eye_kit as K

SS = 4  # 超采样倍数，边缘才不生硬

CFG = {
    'fox': {
        'eyes': [(390, 416, 474, 512), (552, 392, 636, 488)],
        # 补皮肤范围（要盖住 ^ ^ 两头上翘的部分，比眼睛宽不少）
        'cover': [None, None],
        'fur': (250, 247, 228),
        'line': (30, 52, 70),        # 原图轮廓的深藏蓝
        'top': (44, 118, 160),       # 虹膜上缘（深）
        'bottom': (122, 200, 232),   # 虹膜下缘（浅）
        'pupil': (16, 30, 42),
        'rim': (176, 232, 248),
    },
    'meihua': {
        'eyes': [(318, 416, 426, 522), (586, 398, 694, 504)],
        'cover': [None, None],
        'fur': (253, 240, 223),
        'line': (58, 32, 28),
        'top': (86, 46, 40),
        'bottom': (158, 106, 86),
        'pupil': (30, 16, 14),
        'rim': (226, 184, 158),
    },
}

# 补皮肤范围：这两只的笑眯眼弧线比眼珠宽很多（内侧那头一直伸到鼻子附近），
# 只按眼珠大小去补，弧线两头会留两道黑痕。这里显式给每只眼睛一个更大的框，
# 但只往内、上、下扩，尽量不碰外侧的帽子 / 蝴蝶结。
COVER = {'fox': (1.20, 1.24, 2), 'meihua': (1.22, 1.26, 2)}
COVER_BOX = {
    'fox': [(352, 404, 502, 524), (524, 380, 674, 500)],
    'meihua': [(282, 402, 480, 536), (532, 384, 736, 518)],
}


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_eye(box, c):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    W, H = w * SS, h * SS
    pad = 3 * SS
    canvas = Image.new('RGBA', (W + pad * 2, H + pad * 2), (0, 0, 0, 0))

    # 1) 虹膜渐变（上深下浅）—— 逐行铺色，再用椭圆做遮罩
    grad = Image.new('RGB', (W, H))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=lerp(c['top'], c['bottom'], (y / max(1, H - 1)) ** 0.9))
    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, W - 1, H - 1], fill=255)
    canvas.paste(grad, (pad, pad), mask)

    d = ImageDraw.Draw(canvas)
    # 2) 底部反光圈（贴着下缘的一弯浅色）
    rim = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(rim).ellipse([int(W * .10), int(H * .58), int(W * .90), int(H * .98)],
                                fill=c['rim'] + (70,))
    rim = rim.filter(ImageFilter.GaussianBlur(2.2 * SS))
    rim.putalpha(Image.composite(rim.getchannel('A'), Image.new('L', (W, H), 0), mask))
    canvas.alpha_composite(rim, (pad, pad))

    # 3) 竖瞳：居中略偏上
    pw, ph = int(W * .40), int(H * .62)
    px = pad + (W - pw) // 2
    py = pad + int(H * .17)
    d.ellipse([px, py, px + pw, py + ph], fill=c['pupil'] + (255,))
    d.ellipse([px + pw * .20, py + ph * .08, px + pw * .80, py + ph * .30],
              fill=c['pupil'] + (255,))

    # 4) 高光：左上大点 + 右下小点
    hr = int(W * .15)
    hx = pad + int(W * .27)
    hy = pad + int(H * .30)
    d.ellipse([hx, hy, hx + hr, hy + hr], fill=(255, 255, 255, 255))
    hr2 = int(W * .065)
    hx2 = pad + int(W * .62)
    hy2 = pad + int(H * .66)
    d.ellipse([hx2, hy2, hx2 + hr2, hy2 + hr2], fill=(255, 255, 255, 210))

    # 5) 描边最后画，压住渐变的硬边
    lw = max(2 * SS, int(W * .038))
    d.ellipse([pad + lw // 2, pad + lw // 2, pad + W - lw // 2, pad + H - lw // 2],
              outline=c['line'] + (255,), width=lw)
    return canvas.resize((w + pad * 2 // SS, h + pad * 2 // SS), Image.LANCZOS)


def cover_box(box, name):
    sx, sy, dy = COVER.get(name, (1.20, 1.24, 2))
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + dy
    hw, hh = (x2 - x1) / 2 * sx, (y2 - y1) / 2 * sy
    return (int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh))


def build(name, cfg):
    src = Image.open(os.path.join(K.PETS, name + '.png')).convert('RGBA')
    clean = src.copy()
    spots = []
    for idx, eye in enumerate(cfg['eyes']):
        covers = COVER_BOX.get(name) or []
        box = covers[idx] if idx < len(covers) and covers[idx] else cover_box(eye, name)
        # 遮罩 = 眼珠椭圆 + 精确检出的 ^ ^ 深色像素（弧线比椭圆宽，只靠椭圆会漏掉两头）
        ell, o_ell = K.cover_ellipse(box)
        parts = [(ell, o_ell)]
        # 检出 ^ ^ 时往内放宽得多、往外只放一点：这两只的弧线在靠鼻子那头有一撇
        # 上翘的「眼尾」，只按眼睛框检会漏掉，睁眼/闭眼时都留两道黑尖。
        w, h = eye[2] - eye[0], eye[3] - eye[1]
        inward = 1 if (eye[0] + eye[2]) / 2 < src.width / 2 else -1
        left_pad = int(w * (.10 if inward > 0 else .85))
        right_pad = int(w * (.85 if inward > 0 else .10))
        wide = (eye[0] - left_pad, eye[1] - int(h * .10),
                eye[2] + right_pad, eye[3] + int(h * .12))
        bb, dm, do = K.dark_line_mask(src, wide, cfg['line'])
        if bb:
            parts.append((dm.filter(ImageFilter.MaxFilter(13)), do))
        full = K.composite_mask(parts, src.size)
        pbox = full.getbbox()
        if not pbox:
            print('  !! empty mask', name, eye)
            continue
        top = K.band_color(src, pbox[1] - 4, pbox[0], pbox[2])
        bot = K.band_color(src, pbox[3] + 4, pbox[0], pbox[2])
        clean = K.inpaint(clean, full.crop(pbox), (pbox[0], pbox[1]),
                          grow=0, skin=None, top_rgb=top, bot_rgb=bot)
        spots.append((eye, pbox, cfg['line']))
        print('  eye %s box=%s patch=%s top=%s bot=%s' % (name, eye, pbox, top, bot))

    def patch(img):
        """只把补过的眼区抠出来，其余保持透明。"""
        layer = Image.new('RGBA', src.size, (0, 0, 0, 0))
        for eye, pbox, ink in spots:
            x1, y1, x2, y2 = pbox
            m = int(max(x2 - x1, y2 - y1) * 0.10)
            px1, py1 = max(0, x1 - m), max(0, y1 - m)
            px2, py2 = min(src.width, x2 + m), min(src.height, y2 + m)
            layer.paste(img.crop((px1, py1, px2, py2)), (px1, py1))
        return layer

    # 睁眼层：补好的皮肤 + 新画的圆眼
    eyes_layer = patch(clean)
    for eye, pbox, ink in spots:
        eyes_layer.alpha_composite(draw_eye(eye, cfg), (eye[0] - 3, eye[1] - 3))
    K.save_png(eyes_layer, os.path.join(K.EYES_DIR, name + '.png'))

    # 闭眼帧：同一块补好的皮肤 + 与其它宠物一致的透镜形眼皮
    blink_layer = patch(clean)
    d = ImageDraw.Draw(blink_layer)
    for eye, pbox, ink in spots:
        K.draw_lid(d, eye, ink, span_ratio=0.88, dip_ratio=0.13,
                   thickness_ratio=0.15, center_ratio=0.50)
    K.save_png(blink_layer, os.path.join(K.BLINK_DIR, name + '.png'))


if __name__ == '__main__':
    for nm, cfg in CFG.items():
        build(nm, cfg)
