"""One transaction for routes that compose several legacy CRUD operations."""
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.orm import Session


_operation = ContextVar("pv_write_operation", default=None)


def register_new_file(path) -> None:
    """Register a newly allocated path before writing, never an existing file."""
    operation = _operation.get()
    if operation is not None:
        operation.files.add(Path(path))


class WriteSession(Session):
    def commit(self):
        operation = _operation.get()
        if operation is not None and operation.db is self:
            self.flush()
            operation.commit_requested = True
        else:
            super().commit()

    def rollback(self):
        operation = _operation.get()
        if operation is not None and operation.db is self:
            operation.commit_requested = False
        super().rollback()


class WriteOperation:
    def __init__(self, db):
        self.db = db
        self.files = set()
        self.commit_requested = False
        self.commit_validation = False

    def finish(self, response):
        location = getattr(response, "headers", {}).get("location", "")
        parameters = parse_qs(urlsplit(location).query)
        failed = getattr(response, "status_code", 200) >= 400 or any(
            key in parameters for key in (('error',) if self.commit_validation else ('error', 'validation'))
        )
        context = getattr(response, 'context', {})
        failed = failed or any(context.get(key) for key in ('error', 'validation', 'validation_errors'))
        if self.commit_requested and not failed:
            # Bypass only our deferral override, retaining SQLAlchemy events.
            Session.commit(self.db)
            self.files.clear()
        else:
            self.db.rollback()

    def cleanup(self):
        for path in self.files:
            path.unlink(missing_ok=True)


def atomic_write(function=None, *, serialize=False, commit_validation=False):
    """Defer composed CRUD commits until the route has completed successfully.

    An exception, explicit rollback or validation response rolls back all rows
    and removes only the newly allocated files registered by this operation.
    """
    if function is None:
        return lambda target: atomic_write(target, serialize=serialize, commit_validation=commit_validation)

    def start(db):
        operation = WriteOperation(db)
        operation.commit_validation = commit_validation
        if serialize:
            from app.concurrency import lock_snapshot_inputs, PSMF_INPUTS, IMPORT_INPUTS
            lock_snapshot_inputs(db, PSMF_INPUTS + IMPORT_INPUTS)
        return operation

    if iscoroutinefunction(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            operation = start(kwargs["db"])
            token = _operation.set(operation)
            try:
                response = await function(*args, **kwargs)
                operation.finish(response)
                return response
            except BaseException:
                operation.db.rollback()
                raise
            finally:
                _operation.reset(token)
                operation.cleanup()
    else:
        @wraps(function)
        def wrapped(*args, **kwargs):
            operation = start(kwargs["db"])
            token = _operation.set(operation)
            try:
                response = function(*args, **kwargs)
                operation.finish(response)
                return response
            except BaseException:
                operation.db.rollback()
                raise
            finally:
                _operation.reset(token)
                operation.cleanup()
    return wrapped
