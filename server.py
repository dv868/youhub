#!/usr/bin/env python3
"""HUB 新渠道观察站（开源只读版）：静态文件 + 社区众包测速。

渠道数据（incr-data.json）由作者的 HUB 增量巡检日志定时自动生成。
本开源版只提供「只读展示 + 社区测速」；渠道刷新/更新依赖 HUB 管理员凭证，
属作者私有部署逻辑，未包含在本仓库。

启动：python3 server.py [端口，默认 8765]
环境变量：
  BIND      监听地址（默认 127.0.0.1）
  DATA_DIR  静态文件与数据目录（默认本文件所在目录）

接口：
  GET  /api/readonly              只读状态（恒 true，前端据此隐藏刷新/更新按钮）
  GET  /api/community_tests       社区众包测速聚合结果
  POST /api/report_community_test 匿名上报一次测速（不含 key、不含身份）
  其他路径                        静态文件

安全：用户 API key 只存在用户浏览器 localStorage，前端直连 hub.oaifree.com 测速，
key 绝不经过本服务。本服务只接收匿名结果（model + 速度 + 时间）。
"""
from __future__ import annotations
import datetime as dt
import http.server
import json
import os
import sys
import urllib.parse

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
COMMUNITY_FILE = os.path.join(DATA_DIR, 'community-tests.json')
RECENT_KEEP = 5  # 每模型保留最近 N 条明细


def load_community():
    try:
        with open(COMMUNITY_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_community(data):
    tmp = COMMUNITY_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, COMMUNITY_FILE)


def merge_community(data, entry):
    """合并一次匿名测速结果到社区聚合，返回更新后的模型条目。"""
    model = entry['model']
    item = data.get(model, {'count': 0, 'success': 0, 'ttft_sum': 0.0, 'tps_sum': 0.0,
                            'last_at': None, 'recent': []})
    item['count'] += 1
    if entry.get('success'):
        item['success'] += 1
        if entry.get('ttft_s') is not None:
            item['ttft_sum'] += float(entry['ttft_s'])
        if entry.get('tps') is not None:
            item['tps_sum'] += float(entry['tps'])
    item['last_at'] = entry.get('tested_at') or dt.datetime.now().astimezone().isoformat(timespec='seconds')
    item['recent'].append({
        'success': bool(entry.get('success')),
        'ttft_s': entry.get('ttft_s'),
        'tps': entry.get('tps'),
        'tested_at': entry.get('tested_at'),
    })
    item['recent'] = item['recent'][-RECENT_KEEP:]
    data[model] = item
    return item


def community_public(data):
    """输出给前端的社区聚合视图（不含明细以外的任何用户信息）。"""
    out = {}
    for model, item in data.items():
        sc = item.get('success', 0)
        tc = item.get('count', 0)
        out[model] = {
            'count': tc,
            'success': sc,
            'success_rate': round(sc / tc, 3) if tc else None,
            'ttft_avg': round(item['ttft_sum'] / sc, 2) if sc else None,
            'tps_avg': round(item['tps_sum'] / sc, 1) if sc else None,
            'last_at': item.get('last_at'),
            'recent': item.get('recent', []),
        }
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DATA_DIR, **kw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/readonly':
            return self._json(200, {'readonly': True})
        if parsed.path == '/api/refresh_channel':
            return self._json(403, {'error': '演示站只读，不开放刷新'})
        if parsed.path == '/api/update_all':
            return self._json(403, {'error': '演示站只读，不开放更新'})
        if parsed.path == '/api/community_tests':
            return self._json(200, community_public(load_community()))
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/report_community_test':
            return self._report()
        self.send_error(404)

    def log_message(self, fmt, *args):
        if args and '/api/' in str(args[0]):
            super().log_message(fmt, *args)

    def _report(self):
        """接收匿名测速结果。只收 model + 速度 + 时间，不收 key、不收用户标识。"""
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 4096:
                return self._json(400, {'error': 'body 过大'})
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._json(400, {'error': 'JSON 解析失败'})
        model = str(body.get('model') or '').strip()
        if not model or len(model) > 200:
            return self._json(400, {'error': '缺少 model'})
        if body.get('success') is None:
            return self._json(400, {'error': '缺少 success'})
        entry = {
            'model': model,
            'success': bool(body.get('success')),
            'ttft_s': body.get('ttft_s') if isinstance(body.get('ttft_s'), (int, float)) else None,
            'tps': body.get('tps') if isinstance(body.get('tps'), (int, float)) else None,
            'tested_at': str(body.get('tested_at') or dt.datetime.now().astimezone().isoformat(timespec='seconds')),
        }
        data = load_community()
        merge_community(data, entry)
        save_community(data)
        self._json(200, community_public(data))

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    bind = os.environ.get('BIND', '127.0.0.1')
    srv = http.server.ThreadingHTTPServer((bind, port), Handler)
    print(f'HUB 新渠道观察站[开源只读] 监听 http://{bind}:{port}', flush=True)
    srv.serve_forever()
