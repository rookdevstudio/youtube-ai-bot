"""Atomically persist approved configuration without exposing saved secrets."""
import os
import tempfile
import threading
from pathlib import Path
from dotenv import set_key

_lock = threading.Lock()
ALLOWED = {'GEMINI_API_KEY', 'GEMINI_MODEL'}


def update_environment(path, values):
    if not values or not set(values).issubset(ALLOWED):
        raise ValueError('Unsupported configuration setting.')
    path = Path(path)
    with _lock:
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False,suffix='.env.tmp') as temp:
                name = temp.name
                if path.exists():
                    temp.write(path.read_text(encoding='utf-8-sig'))
            for key, value in values.items():
                set_key(name,key,value,quote_mode='always')
            os.replace(name,path)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()
        os.environ.update(values)
