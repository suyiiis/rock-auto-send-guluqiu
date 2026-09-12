"""Fetch verified build inputs; never install drivers or commit native libraries."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name('interception-source.json')
DESTINATION = ROOT / 'vendor' / 'interception'
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def destination_for(relative):
    destination = (DESTINATION / relative).resolve()
    if not destination.is_relative_to(DESTINATION.resolve()):
        raise ValueError(f'Invalid dependency path: {relative}')
    return destination


def prepare(archive=None):
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    files = manifest['files']
    cached = all(
        destination_for(name).is_file()
        and sha256(destination_for(name).read_bytes()) == expected
        for name, expected in files.items()
    )
    if not cached or archive is not None:
        if archive is not None:
            if Path(archive).stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError('Dependency archive is too large')
            payload = Path(archive).read_bytes()
        else:
            print(f"Downloading Interception {manifest['version']}...", flush=True)
            request = Request(manifest['url'], headers={'User-Agent': 'RockGift-build'})
            with urlopen(request, timeout=30) as response:
                payload = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(payload) > MAX_ARCHIVE_BYTES or sha256(payload) != manifest['archive_sha256']:
            raise ValueError('Interception archive SHA-256 mismatch; no files were installed')
        with ZipFile(io.BytesIO(payload)) as archive_file:
            contents = {name: archive_file.read(name) for name in files}
        # Verify every file before modifying the local dependency directory.
        for name, content in contents.items():
            if sha256(content) != files[name]:
                raise ValueError(f'Interception file SHA-256 mismatch: {name}')
        for name, content in contents.items():
            destination = destination_for(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / 'SOURCE.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print('Interception files verified (driver installation was not run).', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use a downloaded official Interception.zip')
    arguments = parser.parse_args()
    try:
        prepare(arguments.archive)
    except Exception as exc:
        parser.exit(1, f'Dependency preparation failed: {exc}\n')
