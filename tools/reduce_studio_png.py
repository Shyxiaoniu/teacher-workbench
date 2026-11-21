#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把学生创意宠物 PNG 减色到 256 色，显著减小离线包体积。

背景：2.1.0 起新增 45 张 1024x1024 RGBA 插画，合计约 23MB，是离线包
从 17MB 涨到 40MB 的主因。插画色块为主，减色到 256 色肉眼几乎无差异，
可把这部分压到约 7MB，且文件名/代码引用全部不变。

分类处理（关键）：
  - 硬边图（alpha 只有 0/255 或没有 alpha）：整图量化到 256 色，压缩率最高。
  - 软边图（alpha 有半透明过渡，如 eyes/fox.png、eyes/meihua.png）：
    只把 RGB 量化到 256 色，alpha 通道原样拼回，否则半透明边缘会被量化
    成硬边、出现锯齿/白边。

校验：尺寸不变、alpha 通道像素级完全一致、色彩模式不变，任一不符即跳过并报错。

用法：
  py -3 tools/reduce_studio_png.py            # 原地处理
  py -3 tools/reduce_studio_png.py --dry-run  # 只统计不写盘

依赖：Pillow（项目内置 Python 已带：D:/WorkBuddy/resources/vendor/python/python.exe）
"""
import os
import sys
import io
import glob
import argparse
from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDIO = os.path.join(ROOT, "app", "typing", "studio")
COLORS = 256


def alpha_values(im):
    """返回 alpha 通道的不同取值个数；无 alpha 返回 0。"""
    if im.mode not in ("RGBA", "LA", "P"):
        return 0
    if im.mode == "P" and "transparency" not in im.info:
        return 0
    a = im.convert("RGBA").getchannel("A")
    return len(set(a.getdata()))


def reduce_one(path, dry=False):
    im = Image.open(path)
    orig_mode = im.mode
    orig_size = im.size

    # 统一策略：只要是带 alpha 的图，一律只量化 RGB、alpha 原样拼回。
    # 不要试图对 RGBA 整体量化——Pillow 量化 RGBA 时连 alpha 通道也会重算，
    # 即使原图是 0/255 硬边也可能被改动，导致透明区域出现杂点。
    # 代价是压缩率略低（约 48% vs 37%），但透明边缘 100% 保真，值得。
    if orig_mode in ("RGBA", "LA") or (orig_mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        r, g, b, a = rgba.split()
        rgb_q = Image.merge("RGB", (r, g, b)).quantize(colors=COLORS, method=2).convert("RGB")
        out = Image.merge("RGBA", (*rgb_q.split(), a))
    else:
        # 无 alpha：整图量化即可
        out = im.convert("RGB").quantize(colors=COLORS, method=2).convert("RGB")

    # 校验：尺寸、模式、alpha 通道像素级一致，任一不符即抛错跳过
    assert out.size == orig_size, "尺寸变化: %s" % path
    assert out.mode == orig_mode, "模式变化: %s (%s->%s)" % (path, orig_mode, out.mode)
    if orig_mode in ("RGBA", "LA") or (orig_mode == "P" and "transparency" in im.info):
        d = ImageChops.difference(im.convert("RGBA").getchannel("A"),
                                  out.convert("RGBA").getchannel("A"))
        assert sum(d.histogram()[1:]) == 0, "alpha 被改变: %s" % path

    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    new_size = buf.tell()
    old_size = os.path.getsize(path)
    if not dry:
        with open(path, "wb") as f:
            f.write(buf.getvalue())
    na = alpha_values(im)
    return old_size, new_size, na


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(STUDIO, "**", "*.png"), recursive=True))
    if not files:
        print("没有找到 PNG:", STUDIO)
        return 1

    tot_old = tot_new = 0
    hard = soft = flat = 0
    errors = []
    for path in files:
        try:
            old, new, na = reduce_one(path, args.dry_run)
        except Exception as e:
            errors.append((os.path.relpath(path, STUDIO), str(e)))
            continue
        tot_old += old
        tot_new += new
        if na > 2:
            soft += 1
        elif na == 2:
            hard += 1
        else:
            flat += 1
        rel = os.path.relpath(path, STUDIO).replace("\\", "/")
        print("%-34s %7.0fKB -> %6.0fKB (%3.0f%%)  alpha=%s" %
              (rel, old / 1024, new / 1024, new / old * 100,
               "软边" if na > 2 else ("硬边" if na == 2 else "无")))

    print()
    print("处理: %d 张 (硬边%d / 软边%d / 无alpha%d)" % (len(files) - len(errors), hard, soft, flat))
    print("体积: %.2f MB -> %.2f MB (省 %.0f%%)" %
          (tot_old / 1024 / 1024, tot_new / 1024 / 1024, (1 - tot_new / tot_old) * 100))
    if args.dry_run:
        print("[dry-run] 未写盘")
    if errors:
        print("!!! 失败 %d 张:" % len(errors))
        for rel, msg in errors:
            print("   ", rel, msg)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
