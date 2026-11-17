"""Small source-only candidate from the working tree, reusing backup file selection."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime,timezone
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from tools.backup_snapshot import source_files,digest,inventory,write_json

def verify(destination):
    destination=Path(destination).resolve();manifest=json.loads((destination/'build-manifest.json').read_text('utf-8'))
    files={k:v for k,v in inventory(destination/'source').items() if not ('__pycache__' in Path(k).parts and k.endswith('.pyc'))}
    if files!=manifest['files']:raise ValueError('Candidate content differs from manifest')
    if hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=manifest['tree_sha256']:raise ValueError('Candidate tree hash mismatch')
    return manifest

def build(destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Choose a new candidate directory; never overwrite a release')
    selected,_=source_files(PROJECT)
    allowed={'.py','.html','.css','.js','.json','.xsd','.xml','.txt','.md','.ini','.cmd','.ps1','.svg','.png','.jpg','.ico','.woff','.woff2','.ttf','.mako'}
    selected=[n for n in selected if Path(n).suffix.lower() in allowed and Path(n).parts[0] not in ('migration_access','examples','tests') and not Path(n).name.startswith('.env')]
    # Explicitly synthetic fixture generator, no imported XML, patient data or keys.
    selected += ['tests/__init__.py','tests/r3_export_helpers.py']
    selected=[n for n in selected if (PROJECT/n).is_file()]
    required=['app/main.py','app/resources/ich-r3-1.11/manifest.json','alembic.ini','requirements.lock.txt','requirements-dev.lock.txt','tools/as2-requirements.lock.txt','tools/acceptance_stand.py']
    for n in required:
        if n not in selected:raise ValueError('Missing required source: '+n)
    for n in selected:
        p=PROJECT/n
        if p.is_symlink() or not p.resolve().is_relative_to(PROJECT):raise ValueError('Linked source refused')
        if re.search(rb'(?m)^-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----',p.read_bytes()):raise ValueError('Private key in source selection: '+n)
    before={n:digest(PROJECT/n) for n in selected}
    destination.mkdir(parents=True);source=destination/'source';source.mkdir()
    for n in selected:
        target=source/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/n,target)
    if any(digest(PROJECT/n)!=h or digest(source/n)!=h for n,h in before.items()):raise ValueError('Working tree changed while assembling')
    files=inventory(source);tree=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    git=lambda *args:subprocess.check_output(['git',*args],cwd=PROJECT).decode('utf-8').strip()
    manifest=dict(format='pv1-acceptance-source-1',build_id='pv1-uat-'+tree[:16],created_at=datetime.now(timezone.utc).isoformat(),
        git_head=git('rev-parse','HEAD'),branch=git('branch','--show-current'),dirty=bool(git('status','--porcelain')),
        tree_sha256=tree,files=files,exclusions=['databases','storage/messages','secrets/private keys','environments','logs/temp','non-synthetic test imports'])
    write_json(destination/'build-manifest.json',manifest);return verify(destination)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','verify']);p.add_argument('destination',type=Path);a=p.parse_args()
    result=build(a.destination) if a.action=='build' else verify(a.destination)
    print(json.dumps(dict(build_id=result['build_id'],files=len(result['files']),verified=True)))
