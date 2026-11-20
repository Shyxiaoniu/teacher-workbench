# 校验像素画模板（app/pixel/pixel.js 里的 TEMPLATES）
# 加模板时最容易错的就是某一行多打/少打一个点，肉眼看不出、画出来是歪的。
# 用法：python tools/check_templates.py
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "..", "app", "pixel", "pixel.js")
PAL_CHARS = "0123456789abcdefghijklmnopqrstuv"
TPL_RE = re.compile(
    r"\{\s*id:\s*'([^']+)',\s*name:\s*'([^']+)',\s*"
    r"w:\s*(\d+),\s*h:\s*(\d+),\s*px:\s*\[(.*?)\]\.join\(''\)\s*\}",
    re.S,
)


def main():
    if not os.path.isfile(JS):
        print("找不到 %s" % JS)
        return 2
    src = open(JS, encoding="utf-8").read()
    items = TPL_RE.findall(src)
    if not items:
        print("没解析到任何模板，检查 TEMPLATES 的写法")
        return 2

    bad = 0
    print("%-10s %-8s %-6s %s" % ("id", "名称", "尺寸", "结果"))
    print("-" * 60)
    for tid, name, w, h, body in items:
        w, h = int(w), int(h)
        rows = re.findall(r"'([^']*)'", body)
        errs = []
        if len(rows) != h:
            errs.append("行数 %d ≠ h %d" % (len(rows), h))
        for i, r in enumerate(rows):
            if len(r) != w:
                errs.append("第 %d 行 %d 字符 ≠ w %d（%r）" % (i + 1, len(r), w, r))
        used = set(ch for r in rows for ch in r if ch != ".")
        odd = sorted(c for c in used if c not in PAL_CHARS)
        if odd:
            errs.append("非法字符 %s" % odd)
        # 模板只用前 16 个默认色，超出说明抄错下标
        over = sorted(c for c in used if c in PAL_CHARS and PAL_CHARS.index(c) >= 16)
        if over:
            errs.append("用到了第 16 色以后的调色板下标 %s" % over)
        if errs:
            bad += 1
            print("%-10s %-8s %-6s 失败" % (tid, name, "%dx%d" % (w, h)))
            for e in errs:
                print("    - %s" % e)
        else:
            filled = sum(1 for r in rows for ch in r if ch != ".")
            print("%-10s %-8s %-6s ok（用色 %d 种，画了 %d 格）"
                  % (tid, name, "%dx%d" % (w, h), len(used), filled))
    print("-" * 60)
    print("%d 个模板，%d 个有问题" % (len(items), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
