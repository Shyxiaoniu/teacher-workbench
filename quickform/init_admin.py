#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次启动时建库 + 建默认管理员账号（幂等）。

为什么需要这一步：QuickForm 官方源码只会在「系统初始化」时**重置已有的
user 表**，从不主动创建账号；而按隐私要求分发的镜像里不带 quickform.db，
全新部署就是空库 —— 后台永远登不进去，只报「用户名或密码错误」。

规则：user 表为空才插入；已有任何账号就绝不动它（不覆盖老师改过的密码）。
账号可用环境变量覆盖：QF_ADMIN_USER / QF_ADMIN_PASSWORD。
"""
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.getcwd())

# 导入官方 app 会执行 Base.metadata.create_all()，表结构由它自己建
import app as qfapp   # noqa: F401,E402

DB = os.path.join('data', 'quickform.db')
USER = os.getenv('QF_ADMIN_USER', 'wst')
PWD = os.getenv('QF_ADMIN_PASSWORD', 'quickform')


def main():
    if not os.path.isfile(DB):
        print('[init] 没找到数据库 %s，跳过（应用启动时会自己建表）' % DB, flush=True)
        return 0
    conn = sqlite3.connect(DB)
    try:
        try:
            n = conn.execute('SELECT COUNT(*) FROM "user"').fetchone()[0]
        except sqlite3.OperationalError as e:
            print('[init] user 表还没建好（%s），跳过建号' % e, flush=True)
            return 0
        if n:
            print('[init] 已有 %d 个账号，保持原样（不覆盖密码）' % n, flush=True)
            return 0
        from werkzeug.security import generate_password_hash
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            'INSERT INTO "user" (id, username, email, password, created_at) VALUES (?,?,?,?,?)',
            (1, USER, 'admin@quickform.local', generate_password_hash(PWD), now))
        conn.commit()
        print('[init] 已创建默认账号 %s / %s —— 首次登录后请立即修改密码'
              % (USER, PWD), flush=True)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
