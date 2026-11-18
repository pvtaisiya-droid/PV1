from pathlib import Path
import os

import pytest


def register_attachment(path):
    from app.database import SessionLocal
    from app.models import Attachment
    with SessionLocal() as db:
        attachment = Attachment(file_name=Path(path).name, storage_path=str(path), mime_type='text/plain')
        db.add(attachment)
        db.commit()
        return attachment.id


@pytest.mark.parametrize('folder', ['e2b_imports', 'documents'])
@pytest.mark.parametrize('action', ['view', 'download'])
def test_registered_attachment_inside_storage_is_served(client, tmp_path, folder, action):
    from app.config import get_settings
    from app.database import engine
    assert Path(engine.url.database).resolve().is_relative_to(tmp_path)
    storage = get_settings().storage_root
    assert storage.is_relative_to(tmp_path)
    path = storage / folder / 'source.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b'Isolated imported source attachment'
    path.write_bytes(content)
    identifier = register_attachment(path)
    response = client.get(f'/documents/{identifier}/{action}')
    assert response.status_code == 200
    assert response.content == content


@pytest.mark.parametrize('relative', [False, True])
@pytest.mark.parametrize('action', ['view', 'download'])
def test_registered_attachment_outside_storage_is_rejected(client, tmp_path, relative, action):
    path = tmp_path / 'outside-storage.txt'
    path.write_bytes(b'Private file outside the configured storage')
    storage_path = os.path.relpath(path, Path.cwd()) if relative else str(path)
    identifier = register_attachment(storage_path)
    response = client.get(f'/documents/{identifier}/{action}')
    assert response.status_code == 404
    assert b'Private file' not in response.content
