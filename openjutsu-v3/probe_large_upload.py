"""Public synthetic media only. Verifies uploads; deliberately never creates a GPU job."""
from pathlib import Path
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import struct
import subprocess
import time
from aiohttp import ClientSession, ClientTimeout, FormData

SIZE = 1_100_000_000
CHUNK = 8 * 1024**2
ROOT = Path('/workspace/openjutsu')

async def main():
    folder = ROOT / 'cache/large-upload-probe'
    folder.mkdir(parents=True, exist_ok=True)
    small = folder / 'public-blue-frame.mp4'
    source = folder / 'public-transport-1.1GB.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=32x32:r=1',
                    '-t','1','-c:v','libx264','-movflags','+faststart','-y',str(small)],check=True)
    with source.open('wb') as output:
        output.write(small.read_bytes())
        output.write(struct.pack('>I4s', SIZE - small.stat().st_size, b'free'))
        output.truncate(SIZE)
    expected = hashlib.sha256()
    with source.open('rb') as stream:
        while block := stream.read(CHUNK): expected.update(block)
    digest = expected.hexdigest()
    token = (ROOT / '.web-access-token').read_text().strip()
    cookie = hmac.new(token.encode(), b'openjutsu-browser-session-v1', hashlib.sha256).hexdigest()
    origin = 'https://' + os.environ['RUNPOD_POD_ID'] + '-8080.proxy.runpod.net'
    headers = {'Cookie':'__Host-openjutsu-session=' + cookie, 'Origin':origin}
    async with ClientSession(timeout=ClientTimeout(total=None,sock_connect=20,sock_read=300)) as client:
        async with client.get('http://127.0.0.1:8190/queue') as response:
            queue_before = {key:len(value) for key,value in (await response.json()).items()}
        assert queue_before.get('queue_running') == 0 and queue_before.get('queue_pending') == 0, 'A user job is running; skip this diagnostic.'
        boundary = 'nwtia-transport-probe-' + secrets.token_hex(8)
        prefix = ('--'+boundary+'\r\nContent-Disposition: form-data; name="mode"\r\n\r\n__nwtia_transport_probe__\r\n'
                  '--'+boundary+'\r\nContent-Disposition: form-data; name="video"; filename="public-transport-1.1GB.mp4"\r\n'
                  'Content-Type: video/mp4\r\n\r\n').encode()
        tail = ('\r\n--'+boundary+'--\r\n').encode()
        upload_id = secrets.token_hex(16)
        base = origin + '/__openjutsu/uploads/' + upload_id
        initialized = {'uploadId':upload_id,'filesBytes':SIZE,'contentType':'multipart/form-data; boundary='+boundary}
        started = time.monotonic()
        async with client.post(origin+'/__openjutsu/uploads',headers=headers,json=initialized) as response:
            assert response.status == 201, await response.text()
        parts, sent = 0, 0
        with source.open('rb') as stream:
            buffer = bytearray(prefix)
            while True:
                data = stream.read(CHUNK - len(buffer))
                buffer.extend(data)
                if len(buffer) < CHUNK:
                    buffer.extend(tail)
                    finished = True
                else:
                    finished = False
                if buffer:
                    raw = bytes(buffer)
                    part_headers = {**headers, 'Upload-Sha256':hashlib.sha256(raw).hexdigest(), 'Content-Type':'application/octet-stream'}
                    async with client.put(base+'/'+str(parts),headers=part_headers,data=raw) as response:
                        assert response.status == 200, await response.text()
                    if parts == 0:
                        async with client.put(base+'/0',headers=part_headers,data=raw) as response:
                            assert response.status == 200, await response.text()
                    sent += len(raw); parts += 1
                    if parts % 16 == 0: print(json.dumps({'uploaded_bytes':sent,'file_bytes':SIZE}),flush=True)
                if finished: break
                buffer.clear()
        commit = {'parts':parts,'bytes':sent}
        for _ in range(2):
            async with client.post(base+'/commit',headers=headers,json=commit) as response:
                assert response.status in {202,400}, await response.text()
        for _ in range(300):
            async with client.get(base+'/status',headers=headers) as response:
                result = await response.json()
                if not result.get('uploadPending'):
                    assert response.status == 400 and result.get('error') == 'Unknown mode.', result
                    break
            await asyncio.sleep(1)
        else: raise RuntimeError('Job validation did not complete in time')
        gateway_seconds = time.monotonic() - started
        # The deliberately invalid mode proves the whole body reached the app's
        # parser, but cannot invoke any submit* function or GPU workflow.
        path = 'input/nwtia_transport_probe_' + secrets.token_hex(8) + '/public-transport-1.1GB.mp4'
        with source.open('rb') as stream:
            form = FormData()
            form.add_field('file',stream,filename='public-transport-1.1GB.mp4',content_type='video/mp4')
            form.add_field('file_path',path)
            async with client.post('http://127.0.0.1:8189/api/v2/assets',data=form) as response:
                asset = await response.json()
                assert response.status in {200,201}, asset
        stored = ROOT / path
        assert stored.is_file() and stored.stat().st_size == SIZE, 'ComfyUI did not store the complete file'
        received = hashlib.sha256()
        with stored.open('rb') as stream:
            while block := stream.read(CHUNK): received.update(block)
        assert received.hexdigest() == digest, 'ComfyUI source bytes changed'
        async with client.get('http://127.0.0.1:8190/queue') as response:
            queue_after = {key:len(value) for key,value in (await response.json()).items()}
        record = {'file_bytes':SIZE,'sha256':digest,'public_gateway_verified':True,'app_parser_verified':True,
                  'proxy_and_comfy_verified':True,'gateway_seconds':round(gateway_seconds,2),'parts':parts,
                  'queue_before':queue_before,'queue_after':queue_after,'gpu_jobs_created':0,'private_media_used':False}
        (folder/'validation.json').write_text(json.dumps(record,indent=2))
        print('NWTIA_LARGE_UPLOAD_VALIDATED '+json.dumps(record),flush=True)
        # Only this generated diagnostic file is removed; user inputs are untouched.
        assert stored.resolve().is_relative_to((ROOT/'input').resolve()) and stored.parent.name.startswith('nwtia_transport_probe_')
        stored.unlink(); stored.parent.rmdir()
        source.unlink(); small.unlink()

asyncio.run(main())
