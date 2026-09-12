import hashlib
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from arena.models import Submission,Problem,Membership
from arena.services import disk_path
assert Membership.objects.filter(status='approved').exists()
submissions=list(Submission.objects.all())
assert submissions
for s in submissions:
    assert s.status=='scored' and s.val_score==0
    assert hashlib.sha256(disk_path(s.path).read_bytes()).hexdigest()==s.file_hash
for p in Problem.objects.filter(published_at__isnull=False):
    files=[disk_path(p.manifest[split]['original']).read_bytes() for split in ['train','val','test']]
    assert hashlib.sha256(b''.join(files)).hexdigest()==p.dataset_version
    for split in ['train','val','test','sample']:assert disk_path(p.manifest[split]['student']).is_file()
print(f'Restored database and independent files verified: {len(submissions)} submissions; original dataset and submission hashes match.')
