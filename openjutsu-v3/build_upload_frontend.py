"""Build only the web app; reuse the immutable, previously validated GPU image."""
from pathlib import Path
import ast
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

bundle = Path(__file__).resolve().parent
base_ref = '92b4999302e2be2f8e51ffe4031c9589c3d5131a'
base_url = 'https://raw.githubusercontent.com/nwtia/runpod-templates/' + base_ref + '/openjutsu-v3/'
old_code = urllib.request.urlopen(base_url + 'bootstrap.py', timeout=60).read()
assert hashlib.sha256(old_code).hexdigest() == '3a74598273fe68decdc7e00f1e3522639149a912d826f19cf39cbb6ab5480f2d'
old_overlay = urllib.request.urlopen(base_url + 'overlay.zip', timeout=60).read()
assert hashlib.sha256(old_overlay).hexdigest() == 'f0ca9cf91c67fdf91b60de774ab8d5573ab831bb700f3922d6b50fa08a8ace43'
previous, current = ast.parse(old_code), ast.parse((bundle / 'bootstrap.py').read_bytes())
def function(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
for name in ('install', 'download_models', 'checkout', 'unpack', 'sha256', 'start', 'wait_http', 'stop'):
    assert ast.dump(function(previous, name)) == ast.dump(function(current, name)), name
normalized = copy.deepcopy(function(current, 'main'))
for call in ast.walk(normalized):
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'start':
        if isinstance(call.args[0], ast.Constant) and call.args[0].value in ('comfy', 'proxy'):
            args = call.args[1].elts
            for i, arg in enumerate(args[:-1]):
                if isinstance(arg, ast.Constant) and arg.value in ('--max-upload-size', '--max-upload-mb'):
                    assert args[i + 1].value == '2048'
                    args[i + 1].value = '512' if arg.value == '--max-upload-size' else '500'
assert ast.dump(normalized) == ast.dump(function(previous, 'main')), 'GPU service configuration changed'

allowed = {'app/lib/http.ts', 'app/lib/browser-upload.ts', 'app/lib/chunked-upload.ts',
           'app/components/openjutsu.tsx', 'app/test/chunked-upload.test.ts'}
overlay_bytes = (bundle / 'overlay.zip').read_bytes()
with zipfile.ZipFile(io.BytesIO(old_overlay)) as old, zipfile.ZipFile(io.BytesIO(overlay_bytes)) as new:
    assert set(old.namelist()) <= set(new.namelist())
    for name in new.namelist():
        if name not in allowed:
            assert name in old.namelist() and new.read(name) == old.read(name), name
    app_overlay = bundle / 'app-overlay'
    app_overlay.mkdir(exist_ok=True)
    for name in new.namelist():
        if name.startswith('app/'):
            target = app_overlay / name[4:]
            assert target.resolve().is_relative_to(app_overlay.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(new.read(name))
identity = hashlib.sha256(overlay_bytes + (bundle / 'bootstrap.py').read_bytes()).hexdigest()
(bundle / 'installed.upload.json').write_text(json.dumps({'identity': identity,
    'generation_tested': False, 'browser_access_update': True, 'large_uploads': True}))
app_ref = json.loads((bundle / 'sources.lock.json').read_text())[0]['commit']
assert app_ref == '7fe118bb5a61fcfac0148e1bfea3dd29884dbf26'
app_archive = urllib.request.urlopen('https://codeload.github.com/D3voz/openjutsu/zip/' + app_ref, timeout=60).read()
build = bundle / 'frontend-build'
build.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(app_archive)) as archive:
    for item in archive.infolist():
        if item.is_dir():
            continue
        relative = item.filename.split('/', 1)[1]
        target = build / relative
        assert target.resolve().is_relative_to(build.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(item))
for source in app_overlay.rglob('*'):
    if source.is_file():
        target = build / source.relative_to(app_overlay)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
print('GPU code, models, LoRA configuration and custom nodes unchanged. Only upload limits and web transport changed.', flush=True)
if '--prepare-only' not in sys.argv:
    env = dict(os.environ, NEXT_TELEMETRY_DISABLED='1', USE_COMFY_ENV='0')
    subprocess.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=build, env=env, check=True)
    subprocess.run(['npm', 'test'], cwd=build, env=env, check=True)
    subprocess.run(['npm', 'run', 'build'], cwd=build, env=env, check=True)
    cache = build / '.next/cache'
    if cache.exists():
        assert cache.resolve().is_relative_to(build.resolve())
        shutil.rmtree(cache)
    print('Web app built and tested. No GPU inference or personal media used.', flush=True)
