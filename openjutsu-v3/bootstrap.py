"""Private RunPod bootstrap for the pinned OpenJutsu V3 snapshot. Never submits a job."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
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
        raise RuntimeError('Add an SSH public key to your RunPod account before deploying this SSH-only template.')
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
    print('[NWTIA] OpenJutsu ready. Use an SSH tunnel to localhost:3000. No generation has been submitted.', flush=True)
    while True:
        for process in CHILDREN:
            if process.poll() is not None:
                raise RuntimeError('A service stopped; check /workspace/openjutsu/logs')
        time.sleep(2)


def stop(*_):
    raise KeyboardInterrupt


if __name__ == '__main__':
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
