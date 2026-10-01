"""Separate application files from persistent Railway state."""
import os
from pathlib import Path
from dotenv import load_dotenv, dotenv_values


def data_directory(base_dir=None):
    base = Path(base_dir or Path(__file__).parent)
    directory = Path(os.getenv('BOT_DATA_DIR') or base).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def settings_environment_path(base_dir=None):
    base = Path(base_dir or Path(__file__).parent).resolve()
    directory = data_directory(base)
    return directory / ('.env' if directory == base else 'settings.env')


def load_runtime_environment(base_dir=None):
    base = Path(base_dir or Path(__file__).parent).resolve()
    load_dotenv(base/'.env')
    settings_file = settings_environment_path(base)
    if settings_file != base/'.env' and settings_file.exists():
        # UI-saved Gemini settings override bootstrap values after a redeploy.
        # Stored state cannot override administrator/deployment configuration.
        saved = dotenv_values(settings_file,interpolate=False)
        for key in ('GEMINI_API_KEY','GEMINI_MODEL'):
            if saved.get(key):
                os.environ[key] = saved[key]
