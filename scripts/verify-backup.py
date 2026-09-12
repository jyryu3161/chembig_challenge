#!/usr/bin/env python3
import hashlib,json,pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1]).resolve()
manifest=json.loads((root/'manifest.json').read_text())
assert set(manifest['files'])=={'database.dump','files.tar.gz'}, 'Unexpected backup manifest'
for name,expected in manifest['files'].items():
    h=hashlib.sha256()
    with (root/name).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    assert h.hexdigest()==expected, f'Checksum failed: {name}'
with tarfile.open(root/'files.tar.gz') as tar:
    for member in tar:
        p=pathlib.PurePosixPath(member.name)
        assert not p.is_absolute() and '..' not in p.parts and p.parts[0] in ['originals','student','submissions']
        assert member.isfile() or member.isdir(), 'Links/devices are not supported'
print('Backup checksums and archive paths verified.')
