import io
import json
import socket
import time
import zipfile
import httpx
import pytest
from fastapi.testclient import TestClient
import fusion
import network_audit

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(fusion, 'DB', tmp_path/'test.sqlite3')
    monkeypatch.setattr(fusion, 'ROOT', tmp_path)
    monkeypatch.setattr(fusion, 'ENV_FILE', tmp_path/'.env')
    fusion.initialize()
    monkeypatch.setenv('FUSION_API_TOKEN','test-service-token')
    for key in ('MODEL_BASE_URL','MODEL_API_KEY','MODEL_NAME','FUSION_REPO_ROOT'):
        monkeypatch.delenv(key, raising=False)
    return TestClient(fusion.app, headers={'Authorization':'Bearer test-service-token'})

def wait(client, job):
    for _ in range(150):
        result = client.get('/api/jobs/'+job['id']).json()
        if result['status'] not in ('queued','running'): return result
        time.sleep(.02)
    pytest.fail('Job did not finish')

def create(client, op, payload):
    r=client.post('/api/jobs',json={'operation':op,'payload':payload})
    assert r.status_code==202, r.text
    return wait(client,r.json())

def artifact(client):
    return create(client,'artifacts',{'site_url':'https://example.org','name':'Demo','pages':[]})

def test_local_cookie_and_csrf_header(client):
    browser=TestClient(fusion.app)
    assert browser.post('/api/session').status_code==403
    r=browser.post('/api/session',headers={'X-Fusion-Client':'workbench'})
    assert r.status_code==200 and 'HttpOnly' in r.headers['set-cookie']
    assert browser.get('/api/jobs').status_code==200
    assert browser.post('/api/jobs',json={}).status_code==403

def test_untrusted_host(client):
    assert client.get('/health',headers={'Host':'evil.example'}).status_code==400

def test_artifact_review_export_and_restart_persistence(client):
    job=artifact(client)
    assert job['status']=='completed'
    assert job['result']['result']['facts.json']['license']=='none'
    path='/api/jobs/'+job['id']
    assert client.post(path+'/review',json={'decision':'approved','sha256':'stale'}).status_code==409
    assert client.post(path+'/review',json={'decision':'approved','sha256':job['result']['sha256'],'note':'verified'}).json()['publish_available'] is False
    exported=client.get(path+'/export')
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        assert {'llms.txt','llms-full.txt','facts.json','task.json'}<=set(z.namelist())
        assert json.loads(z.read('task.json'))['review']=='approved'
    fusion.initialize()
    reread=client.get(path).json()
    assert reread['review']=='approved' and reread['reviews'][0]['note']=='verified'

def test_georank_template_status_explicit(client):
    job=create(client,'keywords',{'seeds':['企业知识库']})
    assert job['status']=='completed'
    assert job['result']['provider_status']=='template-fallback'
    assert len(job['result']['result']['dimensions'])==8
    assert job['result']['result']['summary']['total_keywords']>0

def test_model_absence_fails_without_fabrication(client):
    job=create(client,'content',{'brief':'Verified content'})
    assert job['status']=='failed' and job['result'] is None

def test_model_adapter_and_plan_job(client,monkeypatch):
    for key,value in [('MODEL_BASE_URL','https://models.example/v1'),('MODEL_API_KEY','test-secret'),('MODEL_NAME','fixture-model')]:
        monkeypatch.setenv(key,value)
    async def respond(*args,**kwargs):
        return httpx.Response(200,json={'choices':[{'message':{'content':'30/60/90 day plan'}}]},request=httpx.Request('POST','https://models.example'))
    monkeypatch.setattr(httpx.AsyncClient,'post',respond)
    job=create(client,'plan',{'brief':'Test plan'})
    assert job['status']=='completed'
    assert job['result']['result']['content']=='30/60/90 day plan'

def test_model_settings_hide_secret_and_preserve_it(client):
    payload={'MODEL_BASE_URL':'https://model.example/v1','MODEL_NAME':'model','MODEL_API_KEY':'private-test-secret'}
    response=client.post('/api/settings',json=payload)
    assert response.status_code==200 and 'private-test-secret' not in response.text
    assert client.get('/api/settings').json()['MODEL_API_KEY'] is True
    payload.pop('MODEL_API_KEY')
    assert client.post('/api/settings',json=payload).status_code==200
    import os
    assert os.environ['MODEL_API_KEY']=='private-test-secret'
    assert client.post('/api/settings',json={'MODEL_NAME':'evil\nINJECT=1'}).status_code==422

def test_private_dns_and_rebinding_answers_blocked(monkeypatch):
    for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1'):
        monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))])
        with pytest.raises(ValueError): network_audit.public_target('https://public.example','public.example')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kw:[(2,1,6,'',('1.1.1.1',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError): network_audit.public_target('https://public.example','public.example')

def test_cross_host_and_custom_port_blocked():
    for url in ('https://other.example','http://site.example:8080','file:///tmp/test'):
        with pytest.raises(ValueError): network_audit.public_target(url,'site.example')

def test_fake_ip_dns_uses_only_verified_public_address(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('198.18.0.14',443))])
    monkeypatch.setattr(network_audit,'public_dns',lambda *a:['1.1.1.1'])
    assert network_audit.public_target('https://example.com','example.com')[1]=='1.1.1.1'
    with pytest.raises(ValueError): network_audit.public_target('http://198.18.0.14','198.18.0.14')

def test_public_dns_rejects_private_result(monkeypatch):
    network_audit.public_dns.cache_clear()
    monkeypatch.setattr(httpx,'get',lambda *a,**kw:httpx.Response(200,json={'Status':0,'Answer':[{'type':1,'data':'127.0.0.1'}]},request=httpx.Request('GET','https://dns.google/resolve')))
    with pytest.raises(ValueError): network_audit.public_dns('example.com',999)

def test_transport_budget_and_no_network(monkeypatch):
    transport=network_audit.PublicTransport('site.example')
    transport.requests=65
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Unexpected socket'))
    assert transport.respond(httpx.Request('GET','https://site.example')).status_code==599

def test_invalid_job_rejected(client):
    assert client.post('/api/jobs',json={'operation':'publish','payload':{}}).status_code==422
    assert client.post('/api/jobs',json={'operation':'keywords','payload':{'seeds':[]}}).status_code==422
    assert client.post('/api/jobs',json={'operation':'audit','payload':{'site_url':'https://example.org','sample_size':999}}).status_code==422

def test_snapshot_to_fix_preview_does_not_write(client,tmp_path,monkeypatch):
    repo=tmp_path/'site';repo.mkdir()
    source=repo/'index.html';source.write_bytes(b'<html>\n<head><title>Demo</title></head>\n<body><h1>Demo</h1></body>\n</html>\n')
    before=source.read_bytes()
    monkeypatch.setenv('FUSION_REPO_ROOT',str(repo))
    job=create(client,'snapshot',{'site_url':'https://example.org','resources':{'https://example.org/':{'body':before.decode()}}})
    assert job['status']=='completed'
    finding=next(f for f in job['result']['result']['findings'] if f['id']=='seo.canonical.missing')
    fix=create(client,'fix-preview',{'audit_id':job['id'],'finding_id':finding['id'],'url':finding['url']})
    assert fix['status']=='completed',fix
    assert fix['result']['result']['diff'] and fix['result']['source_sha256']
    assert source.read_bytes()==before

def test_fix_rejects_unrelated_finding(client):
    job=artifact(client)
    result=create(client,'fix-preview',{'audit_id':job['id'],'finding_id':'seo.canonical.missing','url':'https://example.org/'})
    assert result['status']=='failed'

def test_cancel_prevents_result_commit(client,monkeypatch):
    import threading
    entered=threading.Event();release=threading.Event()
    def slow(*args):
        entered.set();release.wait(3);return {'status':'draft','result':{}}
    monkeypatch.setattr(fusion,'live_audit',slow)
    job=client.post('/api/jobs',json={'operation':'audit','payload':{'site_url':'https://example.org'}}).json()
    assert entered.wait(1)
    assert client.post('/api/jobs/'+job['id']+'/cancel').json()['status']=='cancelled'
    release.set();time.sleep(.1)
    result=client.get('/api/jobs/'+job['id']).json()
    assert result['status']=='cancelled' and result['result'] is None

def test_no_auto_publish_route(client):
    assert client.post('/api/publish').status_code==404

def test_live_audit_rejects_zero_successful_pages(monkeypatch):
    monkeypatch.setattr(network_audit,'public_target',lambda *a:(None,'1.1.1.1'))
    monkeypatch.setattr(network_audit.PublicTransport,'respond',lambda self,request:httpx.Response(599,text='blocked'))
    with pytest.raises(ValueError,match='No page'):
        network_audit.live_audit('https://example.org')
