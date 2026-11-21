#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成学生创意馆宠物的「闭眼帧」PNG（app/typing/studio/blink/<name>.png）。

2.0.59 重做：旧版是「盖一块纯毛色椭圆 + 一条大弧线」，牛哥看着像眼睛肿了。
现在改成两步，都不铺色块：

1) 按逐只调好的椭圆范围，用「眼上缘颜色 → 眼下缘颜色」逐列插值补皮肤，
   补出来自带脸部竖向渐变，不会比周围亮，也就没有鼓包；
2) 画一条按实测眼宽/眼高缩放的透镜形眼皮线（中间粗、两端尖、弧度浅）。

尺寸全部来自每只宠物自己的眼睛范围，所以眼小的线细、眼大的线粗。

用法：python app/tools/make_blink_overlays.py
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

import eye_kit as K

# 眼睛包围盒（x1, y1, x2, y2）：2026-11-21 手绘目测 + 逐只微调，确保盖住眼珠。
# 本来就是眯眼 / wink 的不列（hongxin 左眼是爱心、tanuki 左眼在 wink，只眨另一只）。
EYES = {
    "bird":     [(345, 235, 475, 375), (515, 235, 645, 375)],
    "cat":      [(360, 300, 480, 420), (545, 300, 665, 420)],
    "dawang":   [(370, 250, 505, 385), (535, 250, 670, 385)],
    "deer":     [(370, 280, 500, 410), (535, 280, 665, 410)],
    # 小兔子：原来那对框整整高了 110px，压在帽檐上，补出来的眼窝是帽子的黄色
    "fangkuai": [(348, 438, 448, 556), (552, 434, 664, 558)],
    "fish":     [(255, 275, 345, 390)],
    "heitao":   [(360, 320, 495, 450), (540, 320, 675, 450)],
    "hongxin":  [(560, 285, 690, 415)],
    "jelly":    [(385, 255, 485, 380), (545, 255, 645, 380)],
    "tanuki":   [(560, 300, 690, 430)],
    "xiaowang": [(345, 385, 480, 535), (545, 385, 680, 535)],
    "zilong":   [(375, 230, 500, 365), (525, 230, 650, 365)],
}

# 逐只微调「补皮肤范围」：(横向倍数, 纵向倍数, 下移像素)。
# 这个范围必须比眼珠大一圈（否则插值时上下边界取到的还是虹膜本身，会补出
# 一条条竖纹），但又不能大到吃进贴着眼睛的花纹。
COVER = {
    "bird":     (1.16, 1.20, 4),
    "cat":      (1.24, 1.56, 35),
    "dawang":   (1.24, 1.52, 30),
    "deer":     (1.24, 1.52, 30),
    "fangkuai": (1.18, 1.18, 0),
    "fish":     (1.16, 1.24, 0),
    "heitao":   (1.28, 1.66, 40),
    "hongxin":  (1.16, 1.28, 10),
    "jelly":    (1.16, 1.28, 10),
    "tanuki":   (1.16, 1.28, 10),
    "xiaowang": (1.24, 1.48, 25),
    "zilong":   (1.28, 1.56, 35),
}


def cover_box(box, name):
    sx, sy, dy = COVER.get(name, (1.10, 1.18, 8))
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + dy
    hw, hh = (x2 - x1) / 2 * sx, (y2 - y1) / 2 * sy
    return (int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh))


def ink_color(im, bbox):
    """取眼区最暗的一小撮像素，当眼皮线颜色（跟这只宠物的描边同色系）。"""
    x1, y1, x2, y2 = bbox
    px = im.load()
    dark = []
    for yy in range(max(0, y1), y2, 2):
        for xx in range(max(0, x1), x2, 2):
            r, g, b, a = px[xx, yy]
            if a > 180:
                dark.append((r + g + b, (r, g, b)))
    if not dark:
        return (70, 50, 40)
    dark.sort()
    return dark[max(0, len(dark) // 25)][1]


def build_layer(src, eyes, name):
    """返回闭眼层（纯内存，方便反复调参）。

    eyes 里每一项是「眼睛」本身的框：眼皮线按它的宽高定尺寸，所以每只宠物
    的闭眼粗细、长度都不一样；补皮肤则用外扩一圈的椭圆，边界才落在毛色上。
    """
    clean = src.copy()
    spots = []
    for eye in eyes:
        box = cover_box(eye, name)
        skin = K.ring_color(src, box)
        mask, origin = K.cover_ellipse(box, inset=0.0)
        clean = K.inpaint(clean, mask, origin, grow=3, skin=skin)
        ink = ink_color(src, box)
        spots.append((eye, box, ink))

    layer = Image.new('RGBA', src.size, (0, 0, 0, 0))
    for eye, box, ink in spots:
        x1, y1, x2, y2 = box
        m = int(max(x2 - x1, y2 - y1) * 0.10)
        px1, py1 = max(0, x1 - m), max(0, y1 - m)
        px2, py2 = min(src.width, x2 + m), min(src.height, y2 + m)
        layer.paste(clean.crop((px1, py1, px2, py2)), (px1, py1))

    d = ImageDraw.Draw(layer)
    for eye, box, ink in spots:
        # 眼皮画在「眼睛」框上：宽约 0.9 眼宽，粗约 0.15 眼高，弧很浅
        K.draw_lid(d, eye, ink, span_ratio=0.90, dip_ratio=0.13,
                   thickness_ratio=0.15, center_ratio=0.50)
    return layer


if __name__ == '__main__':
    for n, eyes in EYES.items():
        print(n)
        src = Image.open(os.path.join(K.PETS, n + '.png')).convert('RGBA')
        layer = build_layer(src, eyes, n)
        K.save_png(layer, os.path.join(K.BLINK_DIR, n + '.png'))
    print('done')
