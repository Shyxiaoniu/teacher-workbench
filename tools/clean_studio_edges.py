#!/usr/bin/env python3
"""清理创意馆宠物 PNG 的抠图白边。

AI 生成的原图是白底，早期按 alpha 硬切后，轮廓外沿留了一圈 1~2px 的
浅色残留；在深色牧场舞台上放大看就是「粗糙的抠图痕迹」。

本脚本只处理**最外 2px** 的亮且低饱和像素（白/浅灰残留），深色描边和
彩色主体保留。输出保持 1024 原尺寸、透明通道仍是硬边，由浏览器缩放时
再做抗锯齿；不再往素材里引入半透明灰边。

用法：
  python tools/clean_studio_edges.py --out-dir <预览目录>   # 只预览
  python tools/clean_studio_edges.py                       # 原地处理
"""
import argparse
import os
import shutil

from PIL import Image, ImageChops, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDIO = os.path.join(ROOT, "app", "typing", "studio")
TARGET_DIRS = ("pets", "blink")

MIN_CHANNEL = 150     # 三个通道都高于此值才算浅色残留
MAX_SPREAD = 80       # 且颜色不能太「彩」，避免误伤浅蓝/浅绿主体
BAND_PIXELS = 5       # MinFilter 窗口：只取最外 2px


def clean(path, out_path):
    im = Image.open(path).convert("RGBA")
    alpha = im.getchannel("A")
    # 已有抗锯齿的素材不动（眼睛覆盖层等）
    if any(1 <= v < 255 for v in alpha.histogram()[1:255]):
        shutil.copy2(path, out_path)
        return 0
    inner = alpha.filter(ImageFilter.MinFilter(BAND_PIXELS))
    band = ImageChops.subtract(alpha, inner).load()
    px = im.load()
    w, h = im.size
    removed = 0
    for y in range(h):
        for x in range(w):
            if not band[x, y]:
                continue
            r, g, b, a = px[x, y]
            if min(r, g, b) > MIN_CHANNEL and (max(r, g, b) - min(r, g, b)) < MAX_SPREAD:
                px[x, y] = (r, g, b, 0)
                removed += 1
    im.save(out_path)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", help="输出到该目录（保持 pets/blink 子目录），不覆盖原文件")
    ap.add_argument("--backup-dir", help="原地处理前把原图备份到这里")
    args = ap.parse_args()

    jobs = []
    for sub in TARGET_DIRS:
        src_dir = os.path.join(STUDIO, sub)
        for name in sorted(os.listdir(src_dir)):
            if name.lower().endswith(".png"):
                jobs.append((os.path.join(src_dir, name), sub, name))

    for src, sub, name in jobs:
        if args.out_dir:
            dst_dir = os.path.join(args.out_dir, sub)
            os.makedirs(dst_dir, exist_ok=True)
        else:
            dst_dir = os.path.dirname(src)
            if args.backup_dir:
                bdir = os.path.join(args.backup_dir, sub)
                os.makedirs(bdir, exist_ok=True)
                shutil.copy2(src, os.path.join(bdir, name))
        dst = os.path.join(dst_dir, name)
        n = clean(src, dst)
        print("%-24s removed=%d" % (sub + "/" + name, n))


if __name__ == "__main__":
    main()
