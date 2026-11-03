#!/usr/bin/env python3
"""Audit current release files without installing packages or accessing a network."""
from pathlib import Path
import hashlib,csv,re,sys,os
root=Path(__file__).resolve().parent.parent
manifest=root/'PUBLIC_RELEASE_MANIFEST_SHA256.csv'
with open(manifest,encoding='utf-8',newline='') as fh: rows=list(csv.DictReader(fh))
expected={row['path']:row for row in rows}
files={str(p.relative_to(root)).replace(os.sep,'/'):p for p in root.rglob('*') if p.is_file() and not any(part=='.git' for part in p.relative_to(root).parts) and p.name!=manifest.name}
missing=sorted(set(expected)-set(files));unlisted=sorted(set(files)-set(expected));failed=[]
for path,row in expected.items():
    if path not in files:continue
    fp=files[path]
    if fp.stat().st_size!=int(row['bytes']):failed.append(path);continue
    if hashlib.sha256(fp.read_bytes()).hexdigest()!=row['sha256']:failed.append(path)
forbidden=[s for s in files if ('data/raw/' in s or s.startswith('.git/') or s.endswith(('.sqlite3','.sqlite3-wal','.sqlite3-shm','.pem','.key','.p12','.pfx')) or s in ('.env','.env.local'))]
print(f'manifest={len(rows)} verified={len(expected)-len(missing)-len(failed)} mismatched={len(failed)} missing={len(missing)} unlisted={len(unlisted)} forbidden={len(forbidden)}')
if forbidden: print('forbidden file paths:',forbidden[:10])
if missing:print('missing:',missing[:10])
if unlisted:print('unlisted:',unlisted[:10])
if failed:print('mismatched:',failed[:10])
sys.exit(1 if missing or unlisted or failed or forbidden else 0)
