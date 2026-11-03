#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QuickForm 反向代理适配层 —— 官方源码一行不改。

部署形态
--------
nginx 网关把 `/qf/` 反代到本容器，转发时**剥掉** `/qf` 前缀，并带上
`X-Forwarded-Prefix: /qf`。本文件负责让 QuickForm 生成的每个地址都带回
`/qf`，从而和教师端 / 学生端共用同一个网页入口（同一个 IP:端口）。

做三件事
--------
1. ProxyFix 读取 `X-Forwarded-Prefix`：`url_for()` / `redirect()` 生成的
   地址自动带前缀，静态资源、表单 action、跳转全部正确；
2. `after_request` 补几处模板里硬编码的绝对路径（`/static/…`、`/api/…`、
   `window.location.href = '/task/…'`），这些官方代码里没走 url_for；
3. 注入一段十几行的 fetch / XHR / window.open 补丁，把运行时拼出来的
   `/api/…` 也带上前缀（官方模板里有 11 处这样的写法）。

升级 QuickForm 时
-----------------
直接把 [QuickForm 官方仓库](https://gitee.com/wstlab/quickform) 的源码覆盖到
`quickform/app/` 即可，**本文件不需要改**：前缀适配是外部包装，不侵入业务代码。
"""
import json
import os
import re
import sys

PREFIX = '/' + (os.getenv('QF_PREFIX', 'qf').strip('/') or 'qf')
_SLUG = PREFIX.strip('/')

# 官方源码在当前工作目录（entrypoint 会把只读源码复制到 /run/quickform）
sys.path.insert(0, os.getcwd())

from werkzeug.middleware.proxy_fix import ProxyFix   # noqa: E402
from flask import request, jsonify                    # noqa: E402

import app as qfapp                                  # noqa: E402  （导入即建表）

flask_app = qfapp.app

# ① 认 X-Forwarded-*：反向代理后面，request.host / scheme / script_root 才是对的
flask_app.wsgi_app = ProxyFix(
    flask_app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# 会话 cookie 收在本前缀下，避免和教师端（wb_sid）在同一域名里互相干扰
flask_app.config['SESSION_COOKIE_PATH'] = PREFIX
flask_app.config['APPLICATION_ROOT'] = PREFIX

# ② 硬编码的绝对路径：只在属性值 / location 赋值里补前缀，
#    绝不碰 `split('/')` 这类路径分隔符用法。
_ATTR_RE = re.compile(
    r'(\b(?:href|src|action|formaction)\s*=\s*["\'])/(?!/|' + re.escape(_SLUG) + r'/)')
_LOC_RE = re.compile(r'(window\.location(?:\.href)?\s*=\s*["\'])/(?!/)')

_SHIM = """<script>
/* 班级工作台 · QuickForm 前缀补丁（由 serve.py 注入，不修改官方源码） */
(function () {
  var P = "%s";
  function fix(u) {
    if (typeof u !== 'string' || u.charAt(0) !== '/' || u.charAt(1) === '/') return u;
    if (u === P || u.indexOf(P + '/') === 0) return u;
    return P + u;
  }
  var of = window.fetch;
  if (of) window.fetch = function (i, o) {
    try { if (typeof i === 'string') i = fix(i); } catch (e) {}
    return of.call(this, i, o);
  };
  if (window.XMLHttpRequest) {
    var xo = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (m, u) {
      try { arguments[1] = fix(u); } catch (e) {}
      return xo.apply(this, arguments);
    };
  }
  var ow = window.open;
  if (ow) window.open = function (u) {
    try { arguments[0] = fix(u); } catch (e) {}
    return ow.apply(this, arguments);
  };
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    var h = a.getAttribute('href');
    if (h && h.charAt(0) === '/' && h.charAt(1) !== '/' && h.indexOf(P + '/') !== 0) {
      a.setAttribute('href', P + h);
    }
  }, true);
})();
</script>""" % PREFIX


@flask_app.after_request
def _apply_prefix(resp):
    """只在「经网关进来」的请求上改：直连容器调试时不加前缀，保持可用。"""
    if not request.headers.get('X-Forwarded-Prefix'):
        return resp

    # 跳转：Location: /dashboard → /qf/dashboard
    if 300 <= resp.status_code < 400:
        loc = resp.headers.get('Location')
        if loc and loc.startswith('/') and not loc.startswith('//') \
                and not loc.startswith(PREFIX + '/'):
            resp.headers['Location'] = PREFIX + loc
        return resp

    if (resp.mimetype or '') != 'text/html':
        return resp
    try:
        html = resp.get_data(as_text=True)
    except Exception:
        return resp

    html = _ATTR_RE.sub(lambda m: m.group(1) + PREFIX + '/', html)
    html = _LOC_RE.sub(lambda m: m.group(1) + PREFIX + '/', html)

    # ③ 注入运行时补丁（放 </head> 前，保证在页面脚本之前生效）
    if '</head>' in html:
        html = html.replace('</head>', _SHIM + '\n</head>', 1)
    else:
        html = _SHIM + html
    resp.set_data(html)
    return resp


# ----------------------------------------------------------------------
# 班级工作台内部可信端点（2026-11-02）
# ----------------------------------------------------------------------
# 教师端「数据大屏」的一键清空 / 删除单条记录要在 QuickForm 的数据上操作，
# 但教师并不登录 QuickForm（QuickForm 有自己的账号体系与所有者校验）。
# 这里在适配层暴露两个「仅限本机 / 带共享密钥」的内部端点，绕过登录态与
# 所有者校验，由工作台后端代为调用。官方业务源码 app/app.py 一行不改。
#
# 安全：
#   ① 单机部署（Windows 计划任务）：workbench 与 quickform 同机，内部调用
#      来自 127.0.0.1，直接放行；
#   ② 容器部署：跨容器调用 remote_addr 是网桥地址，必须带 X-WB-Internal 头，
#      值为环境变量 WB_INTERNAL_SECRET（workbench / quickform 两端一致）。
# ----------------------------------------------------------------------
_WB_INTERNAL_SECRET = os.getenv('WB_INTERNAL_SECRET', '')


def _wb_internal_allowed():
    ra = (request.remote_addr or '')
    if ra in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        return True
    secret = _WB_INTERNAL_SECRET
    if secret and (request.headers.get('X-WB-Internal') or '') == secret:
        return True
    return False


@flask_app.route('/_wb_internal/clear/<task_key>', methods=['POST', 'GET'])
def _wb_internal_clear(task_key):
    """清空某个活动的全部提交数据（工作台代教师调用）。

    注意：工作台数据大屏用的是 Task.task_id（11 位自定义字符串），
    不是自增主键 Task.id。这里先按自定义 ID 查，查不到再按数字主键兜底。
    """
    if not _wb_internal_allowed():
        return jsonify({'success': False, 'message': '内部端点拒绝访问'}), 403
    db = qfapp.SessionLocal()
    try:
        task = db.query(qfapp.Task).filter_by(task_id=task_key).first()
        if not task and task_key.isdigit():
            task = db.query(qfapp.Task).get(int(task_key))
        if not task:
            return jsonify({'success': False, 'message': '任务不存在'})
        deleted = db.query(qfapp.Submission).filter_by(task_id=task.id).delete()
        db.commit()
        return jsonify({'success': True, 'deleted': int(deleted or 0),
                        'message': '已清空所有提交数据'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': '清空失败：%s' % e}), 500
    finally:
        db.close()


@flask_app.route('/_wb_internal/list/<task_key>', methods=['GET'])
def _wb_internal_list(task_key):
    """按活动读取全部提交，并附上 _submission_id（工作台数据大屏「删除」用）。

    QuickForm 官方 /api/<task>/all 不带提交主键，删单条记录时无从定位，
    这里在内部端点补上 sub.id，其余字段与官方返回保持一致（还是原始提交 JSON）。
    """
    if not _wb_internal_allowed():
        return jsonify({'ok': False, 'msg': '内部端点拒绝访问'}), 403
    db = qfapp.SessionLocal()
    try:
        task = db.query(qfapp.Task).filter_by(task_id=task_key).first()
        if not task and task_key.isdigit():
            task = db.query(qfapp.Task).get(int(task_key))
        if not task:
            return jsonify({'ok': False, 'msg': '任务不存在'}), 404
        subs = db.query(qfapp.Submission).filter_by(task_id=task.id).all()
        out = []
        for sub in subs:
            try:
                data = json.loads(sub.data)
            except Exception:
                try:
                    import ast
                    data = ast.literal_eval(sub.data)
                except Exception:
                    data = {'raw_data': str(sub.data)}
            ts = sub.submitted_at.strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(data, dict):
                data['submitted_at'] = ts
                data['_submission_id'] = sub.id
                out.append(data)
            elif isinstance(data, list):
                out.append({'items': data, 'submitted_at': ts,
                            '_submission_id': sub.id})
        return jsonify({'submissions': out, 'task_id': task.task_id,
                        'task_title': task.title,
                        'total_submissions': len(out)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': '读取失败：%s' % e}), 500
    finally:
        db.close()


@flask_app.route('/_wb_internal/delete/<int:submission_id>', methods=['POST', 'GET'])
def _wb_internal_delete(submission_id):
    """删除单条提交记录（工作台代教师调用）。"""
    if not _wb_internal_allowed():
        return jsonify({'success': False, 'message': '内部端点拒绝访问'}), 403
    db = qfapp.SessionLocal()
    try:
        submission = db.query(qfapp.Submission).get(submission_id)
        if not submission:
            return jsonify({'success': False, 'message': '提交数据不存在'})
        db.delete(submission)
        db.commit()
        return jsonify({'success': True, 'message': '提交数据已删除'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': '删除失败：%s' % e}), 500
    finally:
        db.close()


if __name__ == '__main__':
    port = int(os.getenv('QUICKFORM_PORT') or os.getenv('PORT') or 5055)
    print('[quickform] 监听 0.0.0.0:%d，对外前缀 %s' % (port, PREFIX), flush=True)
    flask_app.run(debug=False, host='0.0.0.0', port=port, threaded=True)
