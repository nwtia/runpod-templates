"""CPU-only registration check used during Docker build; no models or jobs."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import urllib.error

code = Path(os.environ['OPENJUTSU_CODE_ROOT'])
with tempfile.TemporaryDirectory(prefix='nwtia-openjutsu-check-') as temporary:
    target = Path(temporary)
    for name in ('user', 'temp', 'output', 'input'):
        (target / name).mkdir()
    with (target / 'comfy.log').open('w+') as log:
        process = subprocess.Popen([str(code / 'venv/bin/python'), str(code / 'ComfyUI/main.py'),
            '--cpu', '--listen', '127.0.0.1', '--port', '18190', '--disable-auto-launch',
            '--user-directory', str(target / 'user'), '--temp-directory', str(target / 'temp'),
            '--output-directory', str(target / 'output'), '--input-directory', str(target / 'input')],
            cwd=code / 'ComfyUI', stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 180
            nodes = None
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:18190/object_info', timeout=3) as response:
                        nodes = json.load(response)
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(1)
            if nodes is None:
                log.seek(0)
                print(log.read())
                raise RuntimeError('ComfyUI failed the CPU startup check')
            required = {'OpenJutsu3VideoTrackCached', 'OpenJutsu3EditLatentCached', 'OpenJutsu3ReferenceCached',
                'OpenJutsuTrackForeground', 'OpenJutsuRegionTrackSlice', 'OpenJutsuPreserveRegion', 'OpenJutsuExcludeRegion',
                'OpenJutsuTextGenerateCached', 'ClipProjApply', 'MiniMaxH3FantasticVideoEditLatent', 'H3SourceSlice'}
            if missing := required - set(nodes):
                log.seek(0)
                print(log.read())
                raise RuntimeError('Required nodes failed to import: ' + ', '.join(sorted(missing)))
            print('NWTIA CPU startup validation passed:', len(nodes), 'registered nodes. No generation submitted.')
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
