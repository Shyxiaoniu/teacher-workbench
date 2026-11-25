#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学生端「专属入口」签名令牌（账号隔离，2026-11-25）。

老师端首页「学生入口」生成的地址带一张令牌 e，令牌用落盘密钥对教师账号
签名。学生端登录页、名单接口、登录接口都必须带这张令牌，服务端只认令牌
里的账号 —— 学生改地址里的账号/班级前缀都会因签名不符被拒，也看不到别的
老师的班级。令牌不设过期（贴教室的二维码长期可用），密钥重启不变。

密钥文件 entry_secret.key 只写在数据目录，不进代码库、不随资料包导出。
"""
import os
import hmac
import hashlib
import base64
import secrets

_DOMAIN = b"wb-entry-v1:"          # 域分隔，防止和其它签名场景混用
_KEY_NAME = "entry_secret.key"


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(str(s) + "=" * (-len(str(s)) % 4))


class EntryToken:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, _KEY_NAME)
        if not os.path.isfile(self.path):
            with open(self.path, "wb") as f:
                f.write(secrets.token_bytes(32))
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        with open(self.path, "rb") as f:
            self.secret = f.read()[:32] or secrets.token_bytes(32)

    def issue(self, account):
        """签发绑定账号的令牌：<base64(账号)>.<HMAC 签名>。"""
        acc = str(account or "default")
        payload = _b64e(acc.encode("utf-8"))
        sig = hmac.new(self.secret, _DOMAIN + payload.encode("ascii"),
                       hashlib.sha256).digest()[:16]
        return payload + "." + _b64e(sig)

    def verify(self, token):
        """校验令牌，成功返回账号 id；无效/篡改返回 None。"""
        try:
            s = str(token or "").strip()
            payload, dot, sig = s.partition(".")
            if not dot or not payload or not sig:
                return None
            expect = hmac.new(self.secret, _DOMAIN + payload.encode("ascii"),
                              hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(expect, _b64d(sig)):
                return None
            acc = _b64d(payload).decode("utf-8")
            return acc or None
        except Exception:
            return None
