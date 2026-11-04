"""Private RunPod bootstrap for the pinned OpenJutsu V3 snapshot. Never submits a job."""
from pathlib import Path
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import urllib.error
import zipfile

BUNDLE = Path(__file__).resolve().parent
ROOT = Path('/workspace/openjutsu')
CODE = Path(os.environ.get('OPENJUTSU_CODE_ROOT', str(ROOT)))
APP = CODE / 'app'
COMFY = CODE / 'ComfyUI'
PYTHON = CODE / 'venv/bin/python'
PROXY = CODE / 'proxy-venv/bin/python'
NODE_ROOT = CODE / 'node-v24.11.1-linux-x64'
NODE = NODE_ROOT / 'bin/node'
CHILDREN = []


def web_access_token(root=ROOT):
    """One private opening link per persistent workspace; never baked into the image."""
    path = Path(root) / '.web-access-token'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text().strip()
    else:
        token = secrets.token_hex(32)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(token + '\n')
    if not re.fullmatch(r'[a-f0-9]{64}', token):
        raise RuntimeError('Invalid private web access file; refusing to expose OpenJutsu.')
    path.chmod(0o600)
    return token


def web_access_origin():
    pod = os.environ.get('RUNPOD_POD_ID', '')
    if not re.fullmatch(r'[a-zA-Z0-9]{5,64}', pod):
        raise RuntimeError('RUNPOD_POD_ID is required for the private browser link.')
    return 'https://' + pod + '-8080.proxy.runpod.net'


def web_access_app(token, origin, upstream='http://127.0.0.1:3000'):
    """Authenticated streaming gateway. The application and Comfy services stay on loopback."""
    from aiohttp import web, ClientSession, ClientTimeout, ClientError
    from multidict import CIMultiDict
    from urllib.parse import urlsplit

    public = urlsplit(origin)
    internal = urlsplit(upstream)
    if public.scheme != 'https' and public.hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('Public browser access requires HTTPS')
    if internal.hostname not in {'127.0.0.1', 'localhost'} or internal.scheme != 'http':
        raise ValueError('The web upstream must be a local HTTP application')
    cookie_name = '__Host-openjutsu-session' if public.scheme == 'https' else 'openjutsu-local-test'
    session_key = hmac.new(token.encode(), b'openjutsu-browser-session-v1', hashlib.sha256).hexdigest()
    limit = 512 * 1024**2
    session = None
    hop = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
           'te', 'trailer', 'transfer-encoding', 'upgrade', 'proxy-connection'}

    def secure_headers():
        return {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'}

    def page(message, status=200, login=False):
        nonce = secrets.token_urlsafe(18)
        script = '''
const secret=new URLSearchParams(location.hash.slice(1)).get('access');
if(secret){history.replaceState(null,'',location.pathname);fetch('/__openjutsu/login',{
method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access:secret})
}).then(r=>{if(!r.ok)throw Error();location.replace('/')}).catch(()=>{
document.getElementById('message').textContent='Ce lien ne fonctionne pas. Ouvre le lien affiché dans les logs de ton pod RunPod.'})}
''' if login else "setTimeout(()=>location.reload(),5000)"
        body = ('<!doctype html><html lang="fr"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>OpenJutsu</title><style>body{font:18px system-ui;max-width:650px;'
                'margin:15vh auto;padding:24px;background:#111318;color:#eceff5}'
                'h1{font-size:32px}p{line-height:1.6}</style><h1>OpenJutsu</h1>'
                '<p id="message">' + message + '</p><script nonce="' + nonce + '">' + script + '</script></html>')
        headers = secure_headers()
        headers['Content-Security-Policy'] = ("default-src 'none'; script-src 'nonce-" + nonce +
            "'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        return web.Response(text=body, content_type='text/html', status=status, headers=headers)

    async def lifecycle(app):
        nonlocal session
        session = ClientSession(timeout=ClientTimeout(total=None, sock_connect=10, sock_read=None),
                                auto_decompress=False, trust_env=False)
        yield
        await session.close()

    async def handle(request):
        if request.path == '/__openjutsu/health' and request.method == 'GET':
            return web.json_response({'gateway': 'ready'}, headers=secure_headers())
        # RunPod rewrites Host to its private relay and preserves the public
        # address in X-Forwarded-Host. These headers never authenticate a session.
        try:
            relay = ipaddress.ip_address(urlsplit('http://' + request.host).hostname)
            local_relay = relay.is_private or relay in ipaddress.ip_network('100.64.0.0/10')
        except ValueError:
            local_relay = False
        forwarded_public = (local_relay
            and request.headers.get('X-Forwarded-Host', '').lower() == public.netloc.lower()
            and request.headers.get('X-Forwarded-Proto') == 'https')
        if request.host.lower() != public.netloc.lower() and not forwarded_public:
            return web.json_response({'error': 'Unknown address'}, status=421, headers=secure_headers())
        supplied_origin = request.headers.get('Origin')
        if supplied_origin is not None and supplied_origin != origin:
            return web.json_response({'error': 'Cross-origin request refused'}, status=403, headers=secure_headers())
        if request.path == '/__openjutsu/login':
            if request.method != 'POST' or supplied_origin != origin:
                return web.json_response({'error': 'Open the private browser link'}, status=403, headers=secure_headers())
            raw = bytearray()
            async for chunk in request.content.iter_chunked(1024):
                raw.extend(chunk)
                if len(raw) > 1024:
                    return web.json_response({'error': 'Invalid opening link'}, status=413, headers=secure_headers())
            try:
                supplied = json.loads(raw).get('access', '') if len(raw) <= 1024 else ''
            except (ValueError, AttributeError):
                supplied = ''
            if not isinstance(supplied, str) or not re.fullmatch(r'[a-f0-9]{64}', supplied) or not hmac.compare_digest(supplied, token):
                return web.json_response({'error': 'Invalid opening link'}, status=401, headers=secure_headers())
            response = web.json_response({'ok': True}, headers=secure_headers())
            response.set_cookie(cookie_name, session_key, secure=public.scheme == 'https',
                                httponly=True, samesite='Strict', max_age=30 * 24 * 3600, path='/')
            return response
        cookie = request.cookies.get(cookie_name, '')
        if not re.fullmatch(r'[a-f0-9]{64}', cookie) or not hmac.compare_digest(cookie, session_key):
            if request.path == '/' and request.method == 'GET':
                return page('Ouvre le lien privé affiché dans les logs de ton pod RunPod. '
                            'Ensuite, garde cette page en favori.', login=True)
            return web.json_response({'error': 'Open the private browser link'}, status=401, headers=secure_headers())
        # Other customers' proxy.runpod.net hosts may be same-site, but are never same-origin.
        if request.path != '/' and request.headers.get('Sec-Fetch-Site') not in {None, 'same-origin', 'none'}:
            return web.json_response({'error': 'Cross-origin request refused'}, status=403, headers=secure_headers())
        if request.content_length is not None and request.content_length > limit:
            return web.json_response({'error': 'Upload too large'}, status=413, headers=secure_headers())
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            return web.json_response({'error': 'WebSocket upgrades are not exposed'}, status=426, headers=secure_headers())
        excluded = hop | {part.strip().lower() for part in request.headers.get('Connection', '').split(',')}
        headers = CIMultiDict((key, value) for key, value in request.headers.items()
            if key.lower() not in excluded | {'host', 'cookie', 'origin', 'forwarded', 'x-real-ip'}
            and not key.lower().startswith('x-forwarded-'))
        headers['Host'] = internal.netloc
        headers['X-Forwarded-Proto'] = 'http'
        if supplied_origin is not None:
            headers['Origin'] = upstream
        response = None
        try:
            async with session.request(request.method, upstream + request.raw_path,
                    headers=headers, data=request.content.iter_chunked(128 * 1024) if request.can_read_body else None,
                    allow_redirects=False) as remote:
                response_headers = CIMultiDict((key, value) for key, value in remote.headers.items()
                    if key.lower() not in hop | {'x-frame-options', 'referrer-policy'})
                response_headers['Referrer-Policy'] = 'no-referrer'
                response_headers['X-Frame-Options'] = 'DENY'
                response_headers.add('Content-Security-Policy', "frame-ancestors 'none'")
                if response_headers.get('Location', '').startswith(upstream + '/'):
                    response_headers['Location'] = origin + response_headers['Location'][len(upstream):]
                response = web.StreamResponse(status=remote.status, headers=response_headers)
                await response.prepare(request)
                async for chunk in remote.content.iter_any():
                    await response.write(chunk)
                await response.write_eof()
                return response
        except ClientError:
            if response is not None and response.prepared:
                response.force_close()
                return response
            if request.path == '/' and request.method == 'GET':
                return page('OpenJutsu démarre et prépare les modèles. Cette page se met à jour automatiquement.')
            return web.json_response({'error': 'OpenJutsu est en cours de demarrage.'}, status=503, headers=secure_headers())

    app = web.Application(client_max_size=limit)
    app.cleanup_ctx.append(lifecycle)
    app.router.add_route('*', '/{tail:.*}', handle)
    return app


def serve_web_access():
    from aiohttp import web
    web.run_app(web_access_app(web_access_token(), web_access_origin()),
                host='0.0.0.0', port=8080, access_log=None, print=None)


def run(args, cwd=None, env=None):
    print('[NWTIA]', ' '.join(map(str, args[:5])), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def unpack(archive, destination):
    destination = destination.resolve()
    for name in archive.namelist():
        if not (destination / name).resolve().is_relative_to(destination):
            raise ValueError('Archive path outside installation directory')
    archive.extractall(destination)


def checkout(repo):
    target = CODE / repo['path']
    if not (target / '.git').is_dir():
        target.mkdir(parents=True, exist_ok=True)
        run(['git', 'init', target])
        run(['git', '-C', target, 'remote', 'add', 'origin', repo['url']])
    current = subprocess.run(['git', '-C', target, 'rev-parse', 'HEAD'], capture_output=True, text=True)
    if current.stdout.strip() != repo['commit']:
        run(['git', '-C', target, 'fetch', '--depth', '1', 'origin', repo['commit']])
        run(['git', '-C', target, 'checkout', '--detach', 'FETCH_HEAD'])


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(8 * 1024**2):
            result.update(chunk)
    return result.hexdigest()


def install():
    repos = json.loads((BUNDLE / 'sources.lock.json').read_text())
    CODE.mkdir(parents=True, exist_ok=True)
    marker = CODE / 'installed.json'
    identity = hashlib.sha256((BUNDLE / 'overlay.zip').read_bytes() + (BUNDLE / 'bootstrap.py').read_bytes()).hexdigest()
    if marker.exists() and json.loads(marker.read_text()).get('identity') == identity:
        print('[NWTIA] Existing installation reused.', flush=True)
        return
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    run(['apt-get', 'update'], env=env)
    run(['apt-get', 'install', '-y', '--no-install-recommends', 'git', 'curl', 'ca-certificates',
         'python3', 'python3-venv', 'python3-dev', 'build-essential', 'ffmpeg', 'openssh-server',
         'libgl1', 'libglib2.0-0', 'libgomp1'], env=env)
    for repo in repos:
        checkout(repo)
    with zipfile.ZipFile(BUNDLE / 'overlay.zip') as archive:
        unpack(archive, CODE)
    if not NODE.exists():
        name = NODE_ROOT.name + '.tar.xz'
        base = 'https://nodejs.org/dist/v24.11.1/'
        checksums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=30).read().decode()
        expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == name)
        target = CODE / name
        urllib.request.urlretrieve(base + name, target)
        if sha256(target) != expected:
            raise RuntimeError('Node archive checksum mismatch')
        with tarfile.open(target) as archive:
            archive.extractall(CODE, filter='data')
        target.unlink()
    os.environ['PATH'] = str(NODE_ROOT / 'bin') + ':' + os.environ['PATH']
    run(['/usr/bin/python3', '-m', 'venv', CODE / 'venv'])
    run([PYTHON, '-m', 'pip', 'install', '--upgrade', 'pip', 'wheel', 'setuptools', 'Cython'])
    run([PYTHON, '-m', 'pip', 'install', 'torch==2.13.0', 'torchvision==0.28.0',
         '--index-url', 'https://download.pytorch.org/whl/cu130'])
    constraints = CODE / 'constraints.txt'
    constraints.write_text('torch==2.13.0+cu130\ntorchvision==0.28.0+cu130\nnumpy==2.3.2\ntransformers==5.15.0\ninsightface==1.0.1\nonnxruntime-gpu==1.24.4\n')
    run([PYTHON, '-m', 'pip', 'install', '-c', constraints, '-r', COMFY / 'requirements.txt'])
    for repo in repos:
        requirements = CODE / repo['path'] / 'requirements.txt'
        if repo['path'].startswith('ComfyUI/custom_nodes/') and requirements.is_file():
            # SAM2's isolated build would download another, unpinned Torch + CUDA stack.
            build_flags = ['--no-build-isolation'] if 'github.com/facebookresearch/sam2' in requirements.read_text() else []
            run([PYTHON, '-m', 'pip', 'install', *build_flags, '-c', constraints, '-r', requirements])
    run([PYTHON, '-m', 'pip', 'install', '-c', constraints, 'imageio-ffmpeg', 'onnxruntime-gpu', 'huggingface_hub', 'scenedetect>=0.7'])
    run(['/usr/bin/python3', '-m', 'venv', CODE / 'proxy-venv'])
    run([PROXY, '-m', 'pip', 'install', 'comfy-api-proxy==0.1.6'])
    run([PROXY, APP / 'patch_proxy.py'])
    run([NODE_ROOT / 'bin/npm', 'ci', '--no-audit', '--no-fund'], cwd=APP)
    run([NODE_ROOT / 'bin/npm', 'test'], cwd=APP)
    run([NODE_ROOT / 'bin/npm', 'run', 'build'], cwd=APP)
    marker.write_text(json.dumps({'identity': identity, 'generation_tested': False}))


def download_models():
    # The separate interpreter has the same Hugging Face downloader as ComfyUI.
    script = '''
from pathlib import Path
import hashlib, json, os, shutil, sys
from huggingface_hub import hf_hub_download
root=Path(sys.argv[1]); manifest=json.loads(Path(sys.argv[2]).read_text())
record=root/'verified-models.json'
verified=json.loads(record.read_text()) if record.exists() else {}
for model in manifest:
    target=root/'models'/model['destination']; target.parent.mkdir(parents=True,exist_ok=True)
    stat=target.stat() if target.exists() else None
    stamp=[stat.st_size,stat.st_mtime_ns] if stat else None
    if stamp and stamp[0]==model['bytes'] and verified.get(model['destination'])==[model['sha256'],stamp]:
        continue
    source=target if stat and stat.st_size==model['bytes'] else Path(hf_hub_download(
        repo_id=model['repo'], filename=model['file'], revision=model['revision'],
        local_dir=root/'downloads'/model['repo'], token=False))
    print('[NWTIA] Verifying',model['destination'],flush=True)
    digest=hashlib.sha256()
    with source.open('rb') as stream:
        while chunk:=stream.read(8*1024**2): digest.update(chunk)
    if digest.hexdigest()!=model['sha256']: raise RuntimeError('Model checksum mismatch: '+model['destination'])
    if source!=target: os.replace(source,target)
    stat=target.stat(); verified[model['destination']]=[model['sha256'],[stat.st_size,stat.st_mtime_ns]]
    temp=record.with_suffix('.tmp'); temp.write_text(json.dumps(verified)); os.replace(temp,record)
'''
    run([PYTHON, '-u', '-c', script, ROOT, BUNDLE / 'models.lock.json'])


def start(name, args, cwd=None):
    log = (ROOT / 'logs' / (name + '.log')).open('ab', buffering=0)
    process = subprocess.Popen(list(map(str, args)), cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    CHILDREN.append(process)
    return process


def wait_http(url, process, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Service stopped before readiness; check /workspace/openjutsu/logs')
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError('Service readiness timed out: ' + url)


def ssh():
    # Reuse the RunPod account's public key; no passwords are created or embedded.
    directory = Path('/root/.ssh')
    directory.mkdir(mode=0o700, exist_ok=True)
    keys = directory / 'authorized_keys'
    public_key = (os.environ.get('PUBLIC_KEY', '').strip()
                  or os.environ.get('SSH_PUBLIC_KEY', '').strip())
    if public_key:
        existing = keys.read_text() if keys.exists() else ''
        if public_key not in existing:
            keys.write_text(existing.rstrip() + '\n' + public_key + '\n')
        keys.chmod(0o600)
    if not keys.exists() or not keys.read_text().strip():
        print('[NWTIA] SSH optionnel non active. Utiliser le lien web OpenJutsu.', flush=True)
        return
    run(['ssh-keygen', '-A'])
    Path('/run/sshd').mkdir(exist_ok=True)
    start('ssh', ['/usr/sbin/sshd', '-D', '-e', '-o', 'PasswordAuthentication=no', '-o', 'PermitRootLogin=prohibit-password'])


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = (ROOT / 'bootstrap.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for name in ['temp', 'logs', 'input', 'output', 'user', 'proxy-state', 'cache', 'models/loras']:
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    for key, relative in {'TEMP': 'temp', 'TMP': 'temp', 'TMPDIR': 'temp', 'HF_HOME': 'cache/huggingface',
                          'TORCH_HOME': 'cache/torch', 'PIP_CACHE_DIR': 'cache/pip', 'npm_config_cache': 'cache/npm',
                          'XDG_CACHE_HOME': 'cache', 'YOLO_CONFIG_DIR': 'cache/ultralytics',
                          'OPENJUTSU_V2_CACHE': 'cache/captions-v2', 'OPENJUTSU_V3_CACHE': 'cache/v3'}.items():
        os.environ[key] = str(ROOT / relative)
    os.environ.update(NEXT_TELEMETRY_DISABLED='1', HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                      AIOHTTP_NOSENDFILE='1', USE_COMFY_ENV='0', PYTHONDONTWRITEBYTECODE='1',
                      COMFYUI_URL='http://127.0.0.1:8190', COMFY_BASE_URL='http://127.0.0.1:8189')
    os.environ.pop('OPENJUTSU_GPU_PEER', None)
    os.environ.pop('OPENJUTSU_PASSWORD', None)
    models = json.loads((BUNDLE / 'models.lock.json').read_text())
    missing_bytes = sum(model['bytes'] for model in models if not (ROOT / 'models' / model['destination']).is_file())
    if shutil.disk_usage(ROOT).free < missing_bytes + 8 * 1024**3:
        raise RuntimeError('Insufficient free persistent storage for models plus 8 GiB working space.')
    driver = subprocess.check_output(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'], text=True).splitlines()[0]
    if int(driver.split('.')[0]) < 580:
        raise RuntimeError('This pinned CUDA 13.0 runtime requires an NVIDIA 580+ driver.')
    install()
    os.environ['PATH'] = str(NODE_ROOT / 'bin') + ':' + os.environ['PATH']
    ssh()
    token = web_access_token()
    opening_link = web_access_origin() + '/#access=' + token
    gateway = start('web', [PYTHON, '-u', BUNDLE / 'bootstrap.py', '--web-access'])
    wait_http('http://127.0.0.1:8080/__openjutsu/health', gateway, seconds=30)
    print('[NWTIA] OUVRIR OPENJUTSU : ' + opening_link, flush=True)
    download_models()
    config = ROOT / 'models.yaml'
    config.write_text('nwtia:\n  base_path: /workspace/openjutsu/models\n  is_default: true\n' + ''.join(
        '  ' + folder + ': ' + folder + '\n' for folder in ['diffusion_models', 'text_encoders', 'vae', 'loras',
        'checkpoints', 'clip_projections', 'controlnet', 'upscale_models', 'ultralytics', 'sams', 'insightface']))
    comfy = start('comfy', [PYTHON, '-u', COMFY / 'main.py', '--listen', '127.0.0.1', '--port', '8190',
        '--max-upload-size', '512', '--extra-model-paths-config', config, '--input-directory', ROOT / 'input',
        '--output-directory', ROOT / 'output', '--temp-directory', ROOT / 'temp/comfy', '--user-directory', ROOT / 'user',
        '--reserve-vram', '3', '--fast', 'fp16_accumulation', '--disable-fast-disk', '--disable-dynamic-vram',
        '--disable-async-offload', '--disable-pinned-memory', '--use-pytorch-cross-attention', '--disable-auto-launch'], cwd=COMFY)
    wait_http('http://127.0.0.1:8190/system_stats', comfy)
    with urllib.request.urlopen('http://127.0.0.1:8190/object_info', timeout=30) as response:
        nodes = json.load(response)
    required = {'OpenJutsu3VideoTrackCached', 'OpenJutsuTrackForeground', 'ClipProjApply',
                'MiniMaxH3FantasticVideoEditLatent', 'H3SourceSlice'}
    if missing := required - set(nodes):
        raise RuntimeError('Required nodes failed to load: ' + ', '.join(sorted(missing)))
    proxy = start('proxy', [PROXY, '-m', 'comfy_api_proxy.cli', 'run', '--comfyui', 'http://127.0.0.1:8190',
        '--host', '127.0.0.1', '--port', '8189', '--max-upload-mb', '500', '--state-dir', ROOT / 'proxy-state'], cwd=APP)
    wait_http('http://127.0.0.1:8189/api/v2/health', proxy, seconds=30)
    app = start('app', [NODE, APP / 'node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '3000'], cwd=APP)
    wait_http('http://127.0.0.1:3000/api/models', app)
    print('[NWTIA] OpenJutsu est pret. OUVRIR OPENJUTSU : ' + opening_link, flush=True)
    while True:
        for process in CHILDREN:
            if process.poll() is not None:
                raise RuntimeError('A service stopped; check /workspace/openjutsu/logs')
        time.sleep(2)


def stop(*_):
    raise KeyboardInterrupt


if __name__ == '__main__':
    if sys.argv[1:] == ['--web-access']:
        serve_web_access()
        raise SystemExit(0)
    if sys.argv[1:] == ['--install-only']:
        install()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        main()
    finally:
        for child in CHILDREN:
            if child.poll() is None:
                child.terminate()
        for child in CHILDREN:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
