#!/usr/bin/env python3
"""Destructive Redis reset is restricted to the dedicated chembig-e2e project."""
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid

os.environ.update(COMPOSE_FILE='compose.yaml:compose.verify.yaml',COMPOSE_PROJECT_NAME='chembig-e2e',COMPOSE_ENV_FILES='artifacts/verify.env')

def compose(*args,**kwargs):
    return subprocess.check_output(['docker','compose',*args],text=True,**kwargs).strip()

def django(code):
    return compose('exec','-T','web','python','manage.py','shell','-c',code).splitlines()[-1]

if compose('port','nginx','80') != '127.0.0.1:18080':
    raise SystemExit('공개 서비스에는 검증용 Redis 초기화·재시작을 실행할 수 없습니다.')

base='http://127.0.0.1:18080'
state=json.loads(Path('artifacts/browser-user.json').read_text())
username=state['username']
cookie='; '.join(f"{c['name']}={c['value']}" for c in state['cookies'])

def request(path,data=None,headers=None,auth=True):
    hdr={'Cookie':cookie} if auth else {}
    hdr.update(headers or {})
    req=urllib.request.Request(base+path,data=data,headers=hdr)
    try:
        with urllib.request.urlopen(req,timeout=20) as response:return response.status,response.read()
    except urllib.error.HTTPError as e:return e.code,e.read()

# Approval setup is isolated test data; operator access is tested in the Django suite.
django(f"from arena.models import Membership; Membership.objects.filter(user__username={username!r}).update(status='approved'); print('approved')")
meta=json.loads(django("from arena.models import Problem; import json; p=Problem.objects.first(); print(json.dumps({'problem':p.pk,'contest':p.contest_id,'file':p.manifest['val']['student']}))"))
pid,cid=meta['problem'],meta['contest']
assert request(f'/problems/{pid}/files/val/',auth=False)[0]==403
status,val=request(f'/problems/{pid}/files/val/')
assert status==200 and b'target' not in val and b'SMILES' in val
assert request('/_protected_student/'+meta['file'].removeprefix('student/'))[0]==404
assert request('/originals/'+meta['file'])[0]==404
raw=b'sample_id,prediction\nval_001,-1.2\nval_002,-0.7\nval_003,-2.1\ntest_001,-1.2\ntest_002,-0.7\ntest_003,-2.1\n'

def upload():
    _,html=request(f'/contests/{cid}/submit/')
    csrf=re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"',html).group(1).decode()
    key=str(uuid.uuid4());boundary='chembig'+uuid.uuid4().hex
    parts=[]
    for name,value in [('csrfmiddlewaretoken',csrf),('request_key',key)]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="prediction.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()+raw+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    status,body=request(f'/problems/{pid}/submit/',b''.join(parts),{'Content-Type':f'multipart/form-data; boundary={boundary}'})
    assert status==200, (status,body[:200])
    return django(f"from arena.models import Submission; print(Submission.objects.get(request_key={key!r}).pk)")

def scored(sid):
    for _ in range(20):
        result=json.loads(django(f"from arena.models import Submission; import json; s=Submission.objects.get(pk={sid!r}); print(json.dumps([s.status,s.val_score,s.test_score,s.attempts]))"))
        if result[0]=='scored':
            assert result[1]==0 and result[2] is None and result[3]==1
            return
        time.sleep(1)
    raise AssertionError(result)

first=upload();scored(first)
compose('stop','worker')
second=upload()
# This Redis belongs only to the disposable chembig-e2e project.
assert compose('exec','-T','redis','redis-cli','FLUSHDB')=='OK'
compose('exec','-T','web','python','manage.py','reconcile')
compose('start','worker');scored(second)
compose('restart','db','redis','web','worker','nginx')
for _ in range(30):
    try:
        if request('/health/')[0]==200:break
    except (OSError,http.client.RemoteDisconnected):pass
    time.sleep(1)
assert django(f"from arena.models import Submission; print(Submission.objects.filter(pk__in={[first,second]!r},status='scored').count())")=='2'
assert request(f'/problems/{pid}/files/val/')[1]==val
print(json.dumps({'httpUploadAndRealCelery':'passed','nginxInternalAccess':'passed','redisLossRecovery':'passed','containerRestartPersistence':'passed','submissionIds':[first,second]},indent=2))
