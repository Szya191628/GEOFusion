"""One-click local service lifecycle; verifies process ownership before stopping."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import psutil

ROOT = Path(__file__).resolve().parent

def load_env():
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        if sep: os.environ[key] = value

def owned(process):
    try:
        cwd = Path(process.cwd()).resolve()
        command = ' '.join(process.cmdline())
        return cwd.is_relative_to(ROOT) and any(x in command for x in ('uvicorn', 'http.server', 'redirect.py'))
    except (OSError, psutil.Error):
        return False

def stop():
    for conn in psutil.net_connections(kind='inet'):
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port in (3009, 8090):
            process = psutil.Process(conn.pid)
            if not owned(process):
                raise RuntimeError(f'端口 {conn.laddr.port} 由其他项目占用，未停止该进程')
            process.terminate()
            try: process.wait(10)
            except psutil.TimeoutExpired: raise RuntimeError('服务尚未停止，请稍后重试')

def start():
    load_env()
    for port in (8090, 3009):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'端口 {port} 已占用。如需重新启动，请使用 restart.cmd')
    for name, args in (('backend', ['-m', 'uvicorn', 'fusion:app', '--host', '127.0.0.1', '--port', '8090']),
                       ('frontend', ['redirect.py'])):
        with (ROOT / f'logs/{name}.log').open('a', encoding='utf8') as log:
            p = subprocess.Popen([sys.executable, '-X', 'utf8', *args], cwd=ROOT, stdout=log, stderr=log,
                env=os.environ, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        (ROOT / f'logs/{name}.pid').write_text(str(p.pid))
    for attempt in range(60):
        try:
            response = httpx.get('http://127.0.0.1:8090/health', timeout=2)
            response.raise_for_status()
            print('GEO Fusion 已启动：http://127.0.0.1:8090/ （3009 自动转到统一工作台）')
            return
        except httpx.HTTPError:
            time.sleep(.25)
    raise RuntimeError('服务启动失败，请查看 logs/backend.log')

if __name__ == '__main__':
    try:
        action = sys.argv[1] if len(sys.argv)>1 else 'start'
        if action in ('stop','restart'): stop()
        if action in ('start','restart'): start()
        if action=='stop': print('服务已停止')
    except Exception as exc:
        print(str(exc))
        sys.exit(1)
