"""Redirect immutable evidence paths only through an explicit isolated restore map."""
import json
import os
from pathlib import Path

def resolve_storage(value):
    path=Path(value).resolve()
    location=os.environ.get('PV_RESTORE_PATH_MAP')
    if not location:return path
    mapping=json.loads(Path(location).read_text('utf-8'))
    if mapping.get('format')!='pv1-storage-restore-1':raise ValueError('Invalid restore path map')
    original=Path(mapping['original_storage']).resolve();target=Path(mapping['storage']).resolve()
    if target!=Path(os.environ['PV_STORAGE_ROOT']).resolve():raise ValueError('Restore storage is not the configured isolated storage')
    if path.is_relative_to(original):return target/path.relative_to(original)
    if path.is_relative_to(target):return path
    raise ValueError('Recovered evidence path lies outside the isolated storage; no fallback to the original')
