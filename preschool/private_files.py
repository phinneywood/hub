"""Private local journals only; never external effects or access credentials."""
import json
import os
from pathlib import Path
import re
import tempfile
import time

def private_directory(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Use an owner-only access directory, not a symlink or shared folder.')
    return directory


def write_private(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.access-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mutate(label, operation, directory):
    """An ambiguous write stops; a confirmed write is not replayed automatically."""
    folder = private_directory(Path(directory)/'operations')
    if not re.fullmatch(r'[a-z0-9-]{1,100}', label):
        raise ValueError('Invalid operation label.')
    path = folder/(label+'.json')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise ValueError('This operation already has a journal. Inspect its outcome; do not retry.') from None
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps({'phase': 'in_flight', 'started': time.time()})+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    folder_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(folder_fd)
    finally:
        os.close(folder_fd)
    value = operation()
    # Save only identifiers/status, never environment values, credentials or logs.
    safe = {key: value[key] for key in ('id','uid','readyState','version') if isinstance(value,dict) and key in value}
    write_private(path, json.dumps({'phase': 'confirmed', 'finished': time.time(), 'result': safe})+'\n')
    return value

