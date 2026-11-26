"""Unified local workbench, persistent jobs and immutable review artifacts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import zipfile

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app import (app, authorize, ArtifactRequest, artifacts, Snapshot, audit, GenerationRequest,
                 generate_draft, FixRequest, build_fix_preview, draft)
from auth_local import session_value
from network_audit import live_audit
from vendor.georank.keyword_expansion import expand_keywords_with_status

ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get('FUSION_ENV_FILE', str(ROOT / '.env')))
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        if sep and key in ('MODEL_BASE_URL','MODEL_API_KEY','MODEL_NAME','FUSION_REPO_ROOT'):
            os.environ[key] = value
DB = Path(os.environ.get('FUSION_DATABASE', str(ROOT / 'data/fusion.sqlite3')))
DB.parent.mkdir(parents=True, exist_ok=True)
POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='fusion')
CONFIG_LOCK = threading.Lock()
OPERATIONS = {'audit', 'snapshot', 'keywords', 'plan', 'content', 'artifacts', 'fix-preview'}
LABELS = {'audit': '网站技术审计', 'snapshot': '网页快照审计', 'keywords': '关键词规划', 'plan': '优化计划',
          'content': '内容草稿', 'artifacts': '机器文件', 'fix-preview': '修复 Diff'}

def connect():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def initialize():
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, operation TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT, error TEXT, review TEXT NOT NULL DEFAULT 'draft',
            created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL,
            decision TEXT NOT NULL, note TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);''')
        conn.execute("UPDATE jobs SET status='failed', error='服务重启中断，请重新运行', updated=? WHERE status IN ('queued','running')", (time.time(),))

initialize()
app.title = 'GEO Fusion · 统一优化工作台'
app.version = '1.0.0'
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
app.mount('/assets', StaticFiles(directory=ROOT / 'frontend'), name='assets')

@app.get('/', include_in_schema=False)
def frontend():
    return FileResponse(ROOT / 'frontend/index.html')

@app.post('/api/session')
def browser_session(request: Request, response: Response):
    import ipaddress
    local = request.client.host in ('127.0.0.1', '::1', 'testclient')
    if not local and os.environ.get('FUSION_CONTAINER_LOCAL') == '1':
        try: local = ipaddress.ip_address(request.client.host).is_private
        except ValueError: local = False
    if not local or request.headers.get('x-fusion-client') != 'workbench':
        raise HTTPException(403, '本机工作台专用入口')
    # Custom header + no CORS + strict cookie prevents remote-page session creation.
    response.set_cookie('fusion_session', session_value(), httponly=True, samesite='strict', max_age=28800)
    return {'status': 'ready'}

def encode_row(row, detail=True):
    data = dict(row)
    if detail:
        data['payload'] = json.loads(data['payload'])
        data['result'] = json.loads(data['result']) if data['result'] else None
    else:
        data.pop('payload', None)
        data.pop('result', None)
    return data

def read_job(job_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, '任务不存在')
    return encode_row(row)

def execute_job(job_id):
    with connect() as conn:
        if conn.execute("UPDATE jobs SET status='running',updated=? WHERE id=? AND status='queued'", (time.time(), job_id)).rowcount != 1:
            return
    try:
        job = read_job(job_id)
        p, op = job['payload'], job['operation']
        if op == 'audit':
            result = live_audit(p['site_url'], p.get('sample_size', 10))
        elif op == 'snapshot':
            result = audit(Snapshot(**p))
        elif op == 'artifacts':
            result = artifacts(ArtifactRequest(**p))
        elif op in ('content', 'plan'):
            result = asyncio.run(generate_draft(op, GenerationRequest(**p)))
        elif op == 'keywords':
            expanded, success = asyncio.run(expand_keywords_with_status(p['seeds']))
            result = draft('keywords', expanded, provider_status='succeeded' if success else 'template-fallback',
                upstream_commit='424a0cf92b37ad63c94ae9dc6f39745189ab7c94',
                warnings=[] if success else ['当前为 GEORank 规则模板拓词，未由模型生成；评分不代表真实搜索量。'])
        elif op == 'fix-preview':
            parent = read_job(p['audit_id'])
            if parent['status'] != 'completed' or parent['operation'] not in ('audit', 'snapshot'):
                raise ValueError('请选择已完成的审计结果')
            findings = parent['result']['result']['findings']
            selected = next((f for f in findings if f['id'] == p['finding_id'] and f['url'] == p['url']), None)
            if selected is None:
                raise ValueError('该问题不属于指定审计记录')
            from omnirank.report import Finding
            report_findings = [Finding(**{k:f[k] for k in ('id','severity','layer','url','gate','observed','expected','fix')}) for f in findings]
            result = build_fix_preview(FixRequest(url=selected['url'], finding_id=selected['id'],
                observed=selected['observed'], expected=selected['expected']), report_findings)
            result['audit_id'] = parent['id']
            result['audit_sha256'] = parent['result']['sha256']
            relative = result['result'].get('path')
            if relative:
                repo = Path(os.environ['FUSION_REPO_ROOT']).resolve()
                source = (repo / relative).resolve()
                if not source.is_relative_to(repo):
                    raise ValueError('源码路径越界')
                result['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        else:
            raise ValueError('不支持的操作')
        with connect() as conn:
            conn.execute("UPDATE jobs SET status='completed',result=?,updated=? WHERE id=? AND status='running'",
                         (json.dumps(result, ensure_ascii=False), time.time(), job_id))
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else (
            str(exc) if isinstance(exc, ValueError) else '运行失败，请检查配置、模型或目标网站；详情不会包含密钥')
        with connect() as conn:
            conn.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=? AND status='running'",
                         (message[:1000], time.time(), job_id))

class JobRequest(BaseModel):
    operation: str
    title: str = Field(default='', max_length=200)
    payload: dict

def validate_payload(op, payload):
    if len(json.dumps(payload)) > 6_000_000:
        raise HTTPException(413, '输入超过大小限制')
    try:
        if op == 'snapshot': Snapshot(**payload)
        elif op == 'artifacts': ArtifactRequest(**payload)
        elif op in ('plan', 'content'): GenerationRequest(**payload)
        elif op == 'keywords':
            seeds = payload['seeds']
            if not isinstance(seeds, list) or not 1 <= len(seeds) <= 8 or any(not isinstance(s, str) or not s.strip() or len(s) > 300 for s in seeds):
                raise ValueError('种子词应为 1–8 个非空短语')
        elif op == 'audit':
            from app import origin
            origin(payload['site_url'])
            if not isinstance(payload.get('sample_size', 10), int) or not 1 <= payload.get('sample_size', 10) <= 20:
                raise ValueError('审计页数应为 1–20')
        elif op == 'fix-preview':
            FixRequest(url=payload['url'], finding_id=payload['finding_id'])
            read_job(payload['audit_id'])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)[:1000]) from None

@app.post('/api/jobs', dependencies=[Depends(authorize)], status_code=202)
def create_job(data: JobRequest):
    if data.operation not in OPERATIONS:
        raise HTTPException(422, '操作不存在')
    validate_payload(data.operation, data.payload)
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 12:
            raise HTTPException(429, '队列已满，请稍后重试')
        job_id = uuid.uuid4().hex
        now = time.time()
        conn.execute('INSERT INTO jobs(id,operation,title,payload,status,created,updated) VALUES(?,?,?,?,?,?,?)',
            (job_id, data.operation, data.title or LABELS[data.operation], json.dumps(data.payload, ensure_ascii=False), 'queued', now, now))
    POOL.submit(execute_job, job_id)
    return read_job(job_id)

@app.get('/api/jobs', dependencies=[Depends(authorize)])
def jobs():
    with connect() as conn:
        rows = conn.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 150').fetchall()
    return [encode_row(r, False) for r in rows]

@app.get('/api/jobs/{job_id}', dependencies=[Depends(authorize)])
def job_detail(job_id: str):
    data = read_job(job_id)
    with connect() as conn:
        data['reviews'] = [dict(r) for r in conn.execute('SELECT * FROM reviews WHERE job_id=? ORDER BY created', (job_id,))]
    return data

@app.post('/api/jobs/{job_id}/cancel', dependencies=[Depends(authorize)])
def cancel(job_id: str):
    read_job(job_id)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=? AND status IN ('queued','running')", (time.time(), job_id))
    return {'status': read_job(job_id)['status'], 'notice': '已停止后续保存；正在进行的单次网络/模型请求可能持续至超时。'}

class Review(BaseModel):
    decision: str
    sha256: str
    note: str = Field(default='', max_length=3000)

@app.post('/api/jobs/{job_id}/review', dependencies=[Depends(authorize)])
def review(job_id: str, data: Review):
    if data.decision not in ('approved', 'rejected'):
        raise HTTPException(422, '审核结论无效')
    job = read_job(job_id)
    if job['status'] != 'completed' or job['result']['sha256'] != data.sha256:
        raise HTTPException(409, '结果不存在或版本已变化')
    with connect() as conn:
        conn.execute('INSERT INTO reviews(job_id,decision,note,sha256,created) VALUES(?,?,?,?,?)',
                     (job_id, data.decision, data.note, data.sha256, time.time()))
        conn.execute('UPDATE jobs SET review=?,updated=? WHERE id=?', (data.decision, time.time(), job_id))
    return {'review': data.decision, 'publish_available': False}

@app.get('/api/jobs/{job_id}/export', dependencies=[Depends(authorize)])
def export(job_id: str):
    job = job_detail(job_id)
    if job['status'] != 'completed':
        raise HTTPException(409, '只能导出已完成任务')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('task.json', json.dumps(job, ensure_ascii=False, indent=2))
        result = job['result']['result']
        if job['operation'] == 'artifacts':
            for name in ('llms.txt', 'llms-full.txt', 'facts.json'):
                value = result.get(name)
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, dict) else str(value))
        if result.get('content'): archive.writestr('draft.md', result['content'])
        if result.get('diff'): archive.writestr('preview.diff', result['diff'])
    stream.seek(0)
    return StreamingResponse(stream, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="geo-draft-{job_id[:8]}.zip"'})

SETTINGS_KEYS = ('MODEL_BASE_URL', 'MODEL_API_KEY', 'MODEL_NAME', 'FUSION_REPO_ROOT')

@app.get('/api/settings', dependencies=[Depends(authorize)])
def settings():
    return {key: (bool(os.environ.get(key)) if key == 'MODEL_API_KEY' else os.environ.get(key, '')) for key in SETTINGS_KEYS}

class Settings(BaseModel):
    MODEL_BASE_URL: str = Field(default='', max_length=2048)
    MODEL_NAME: str = Field(default='', max_length=300)
    MODEL_API_KEY: str | None = Field(default=None, max_length=3000)
    FUSION_REPO_ROOT: str = Field(default='', max_length=1000)

@app.post('/api/settings', dependencies=[Depends(authorize)])
def save_settings(data: Settings):
    from app import origin
    values = data.model_dump(exclude_none=True)
    if any('\n' in v or '\r' in v for v in values.values()):
        raise HTTPException(422, '配置不能包含换行')
    if data.MODEL_BASE_URL:
        origin(data.MODEL_BASE_URL)
    if data.FUSION_REPO_ROOT and not Path(data.FUSION_REPO_ROOT).is_dir():
        raise HTTPException(422, '源码目录不存在')
    with CONFIG_LOCK:
        file = ENV_FILE
        current = {}
        if file.exists():
            for line in file.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.partition('=')
                if sep: current[key] = value
        current.update(values)
        file.parent.mkdir(parents=True, exist_ok=True)
        temp = file.with_suffix('.pending')
        temp.write_text(''.join(k + '=' + v + '\n' for k, v in current.items()), encoding='utf8')
        temp.replace(file)
        os.environ.update(values)
    return settings()

@app.get('/api/capabilities', dependencies=[Depends(authorize)])
def capabilities():
    return {'version': app.version, 'model_ready': all(os.environ.get(k) for k in ('MODEL_BASE_URL','MODEL_API_KEY','MODEL_NAME')),
            'repository_ready': bool(os.environ.get('FUSION_REPO_ROOT')), 'keyword_engine': 'GEORank 8 dimensions',
            'audit_engine': 'OmniRank 0.4.0', 'database': 'SQLite', 'publish_available': False,
            'mode': 'local-single-user'}

@app.post('/api/model-test', dependencies=[Depends(authorize)])
async def model_test():
    from georank_bridge import ai_client
    start = time.monotonic()
    try:
        reply = await ai_client.complete('Reply with OK only.', 'Connection test', temperature=0)
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError('Empty response')
    except Exception:
        raise HTTPException(502, '模型连接失败，请检查已保存的地址、模型名称和密钥') from None
    return {'model': os.environ.get('MODEL_NAME'), 'elapsed_ms': round((time.monotonic()-start)*1000)}
