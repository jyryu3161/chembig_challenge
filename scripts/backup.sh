#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
exec 9>/tmp/chembig-backup.lock
flock -n 9 || exit 0
task_data_dir=$(docker compose config --format json | python3 -c 'import json,sys; print(next(v["source"] for v in json.load(sys.stdin)["services"]["web"]["volumes"] if v["target"]=="/data"))')
export CHEMBIG_BACKUP_ROOT="$task_data_dir"
python3 - <<'PY'
import os,pathlib,subprocess,datetime,hashlib,json,tarfile,shutil
root=pathlib.Path(os.environ['CHEMBIG_BACKUP_ROOT'])
backup=root/'backups'; backup.mkdir(exist_ok=True)
now=datetime.datetime.now(datetime.timezone.utc)
work=backup/(now.strftime('%Y%m%dT%H%M%SZ')+'.partial'); work.mkdir()
with (work/'database.dump').open('wb') as f:
    subprocess.run(['docker','compose','exec','-T','db','sh','-c','pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'],stdout=f,check=True)
# Files are immutable and uniquely named. A DB snapshot precedes the file archive.
with tarfile.open(work/'files.tar.gz','w:gz') as tar:
    for name in ['originals','student','submissions']:
        if (root/name).exists(): tar.add(root/name,arcname=name)
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
completed=datetime.datetime.now(datetime.timezone.utc).isoformat()
manifest={'completed_at':completed,'files':{p.name:digest(p) for p in work.iterdir()},
          'app_image':subprocess.check_output(['docker','compose','images','--format','json']).decode()}
(work/'manifest.json').write_text(json.dumps(manifest,indent=2))
final=work.with_suffix('');work.rename(final)
(backup/'last_success.json').write_text(json.dumps({'completed_at':completed,'directory':final.name}))
os.chmod(backup/'last_success.json',0o644)
for p in sorted(backup.iterdir()):
    if p.is_dir() and not p.name.endswith('.partial') and now.timestamp()-p.stat().st_mtime>30*86400:shutil.rmtree(p)
print(final)
PY
