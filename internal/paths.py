"""Application resources and portable state stored alongside the executable."""
import json
import os
from pathlib import Path
import sys

INTERNAL_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_ROOT = Path(sys._MEIPASS) if FROZEN else INTERNAL_DIR.parent
def data_directory():
    if os.environ.get('ROCK_GIFT_DATA_DIR'):
        return Path(os.environ['ROCK_GIFT_DATA_DIR'])
    return Path(sys.executable).resolve().parent/'data' if FROZEN else INTERNAL_DIR.parent


PROJECT_ROOT = data_directory()


def initialize_data():
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT/'logs').mkdir(exist_ok=True)
    destination = PROJECT_ROOT/'config.json'
    if not destination.exists():
        source = RESOURCE_ROOT/('default_config.json' if FROZEN else 'config.json')
        settings = json.loads(source.read_text(encoding='utf-8-sig'))
        try:
            with destination.open('x', encoding='utf-8') as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2)
        except FileExistsError:
            # Another first-launch process won the race; never replace user settings.
            pass


def process_command(role, *arguments):
    prefix = [sys.executable] if FROZEN else [sys.executable, '-X', 'utf8', str(INTERNAL_DIR/'app.py')]
    return [*prefix, '--'+role, *map(str, arguments)]
