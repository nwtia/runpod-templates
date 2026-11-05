"""Authenticated, disk-backed multipart transport. Never retries an uncertain job submission."""
from pathlib import Path
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from urllib.parse import urlsplit
from aiohttp import web

MAX_BYTES = 2 * 1024**3
CHUNK_BYTES = 8 * 1024**2
PREFIX = '/__openjutsu/uploads'


class ChunkUploads:
    def __init__(self, root, reserve_bytes=8 * 1024**3):
        self.root = Path(root)
        self.reserve = reserve_bytes
        self.locks = {}
        self.tasks = set()

    def error(self, text, status=400):
        return web.json_response({'error': text}, status=status, headers={'Cache-Control': 'no-store'})

    async def small_json(self, request):
        data = bytearray()
        async for block in request.content.iter_chunked(2048):
            data.extend(block)
            if len(data) > 2048:
                raise ValueError('Upload metadata is too large.')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('Invalid upload metadata.')
        return result

    def save(self, folder, meta):
        temp = folder / 'state.tmp'
        temp.write_text(json.dumps(meta), encoding='utf-8')
        os.replace(temp, folder / 'state.json')

    def result(self, meta):
        if meta['state'] == 'cancelled':
            return self.error('Upload cancelled before job submission. Select the files again.', 410)
        if meta['state'] == 'done':
            return web.Response(body=base64.b64decode(meta['body']), status=meta['status'],
                                content_type='application/json', headers={'Cache-Control': 'no-store'})
        if meta['state'] in {'submitting', 'uploading'}:
            return web.json_response({'uploadPending': True, 'state': meta['state']}, status=202,
                                     headers={'Cache-Control': 'no-store'})
        return self.error('The upload reached the server, but the job response could not be confirmed. '
                          'Do not submit it again until the job queue has been checked.', 409)

    async def handle(self, request, session, upstream):
        try:
            if request.path == PREFIX and request.method == 'POST':
                data = await self.small_json(request)
                content_type = data.get('contentType', '')
                size = data.get('filesBytes')
                if not isinstance(content_type, str) or not re.fullmatch(
                        r'multipart/form-data; boundary=[A-Za-z0-9_\-]{1,70}', content_type):
                    return self.error('Invalid multipart upload.')
                if type(size) is not int or not 0 < size < MAX_BYTES:
                    return self.error('Files must total less than 2 GiB.', 413)
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                upload_id = data.get('uploadId', secrets.token_hex(16))
                if not isinstance(upload_id, str) or not re.fullmatch(r'[a-f0-9]{32}', upload_id):
                    return self.error('Invalid upload identifier.')
                folder = self.root / upload_id
                if (folder / 'state.json').is_file():
                    existing = json.loads((folder / 'state.json').read_text())
                    if existing['contentType'] != content_type or existing['filesBytes'] != size:
                        return self.error('An upload identifier has conflicting metadata.', 409)
                    return web.json_response({'uploadId': upload_id, 'chunkBytes': CHUNK_BYTES}, status=201)
                # Only our generated directories are considered; never touch inputs or outputs.
                active = 0
                for entry in self.root.iterdir():
                    if not entry.is_dir() or not re.fullmatch(r'[a-f0-9]{32}', entry.name):
                        continue
                    state_file = entry / 'state.json'
                    if state_file.exists():
                        old = json.loads(state_file.read_text())
                        if time.time() - old['created'] > 24 * 3600 and old['state'] != 'submitting':
                            for part in entry.iterdir():
                                if part.is_file() and not part.is_symlink():
                                    part.unlink()
                            entry.rmdir()
                            self.locks.pop(entry.name, None)
                        elif old['state'] not in {'done', 'unknown', 'cancelled'}:
                            active += 1
                if active >= 8:
                    return self.error('Too many unfinished uploads. Finish an existing upload first.', 429)
                if shutil.disk_usage(self.root).free < size + self.reserve:
                    return self.error('Not enough free space on the pod for this video and working files.', 507)
                folder.mkdir(mode=0o700)
                self.save(folder, {'created': time.time(), 'state': 'uploading', 'contentType': content_type,
                                   'filesBytes': size, 'parts': {}})
                return web.json_response({'uploadId': upload_id, 'chunkBytes': CHUNK_BYTES}, status=201,
                                         headers={'Cache-Control': 'no-store'})

            match = re.fullmatch(PREFIX + r'/([a-f0-9]{32})/(status|commit|cancel|[0-9]{1,3})', request.path)
            if not match:
                return self.error('Unknown upload route.', 404)
            upload_id, action = match.groups()
            folder = self.root / upload_id
            lock = self.locks.setdefault(upload_id, asyncio.Lock())
            async with lock:
                if not (folder / 'state.json').is_file():
                    self.locks.pop(upload_id, None)
                    return self.error('This upload has expired. Select the files again.', 404)
                meta = json.loads((folder / 'state.json').read_text())
                if action == 'status' and request.method == 'GET':
                    return self.result(meta)
                if action == 'cancel' and request.method == 'DELETE':
                    if meta['state'] not in {'uploading', 'cancelled'}:
                        return self.error('An upload being submitted cannot be cancelled here.', 409)
                    meta['state'] = 'cancelled'
                    self.save(folder, meta)
                    for part in folder.glob('*.part'):
                        part.unlink()
                    return web.json_response({'cancelled': True})
                if action == 'commit' and request.method == 'POST':
                    data = await self.small_json(request)
                    if meta['state'] != 'uploading':
                        return self.result(meta)
                    count, total = data.get('parts'), data.get('bytes')
                    if type(count) is not int or not 1 <= count <= 256 or type(total) is not int:
                        return self.error('Invalid upload completion.')
                    if set(meta['parts']) != {str(i) for i in range(count)}:
                        return self.error('Upload is missing a part.', 409)
                    if total != sum(p['bytes'] for p in meta['parts'].values()) or not meta['filesBytes'] <= total <= MAX_BYTES:
                        return self.error('The uploaded size does not match.', 409)
                    meta.update(state='submitting', bytes=total)
                    self.save(folder, meta)
                    task = asyncio.create_task(self.forward(folder, meta, session, upstream))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                    return self.result(meta)
                if not action.isdigit() or request.method != 'PUT':
                    return self.error('Unsupported upload operation.', 405)
                index = int(action)
                expected = request.headers.get('Upload-Sha256', '')
                if index >= 256 or not re.fullmatch(r'[a-f0-9]{64}', expected):
                    return self.error('Invalid upload part.')
                # A repeated PUT is harmless, including a response lost after storing the part.
                previous = meta['parts'].get(str(index))
                if previous:
                    if previous['sha256'] != expected:
                        return self.error('An upload part has conflicting contents.', 409)
                    return web.json_response({'ok': True, **previous})
                if meta['state'] != 'uploading':
                    return self.error('This upload is already being submitted.', 409)
                if request.content_length is not None and request.content_length > CHUNK_BYTES:
                    return self.error('The upload part is too large.', 413)
                if sum(p['bytes'] for p in meta['parts'].values()) + CHUNK_BYTES > MAX_BYTES:
                    return self.error('Files must total less than 2 GiB.', 413)
                temp = folder / ('part-' + secrets.token_hex(8) + '.tmp')
                size, digest = 0, hashlib.sha256()
                try:
                    with temp.open('xb') as output:
                        async for block in request.content.iter_chunked(128 * 1024):
                            size += len(block)
                            if size > CHUNK_BYTES:
                                return self.error('The upload part is too large.', 413)
                            output.write(block)
                            digest.update(block)
                    if not size or digest.hexdigest() != expected:
                        return self.error('An upload part was damaged in transit.', 409)
                    os.replace(temp, folder / f'{index:03d}.part')
                    meta['parts'][str(index)] = {'bytes': size, 'sha256': expected}
                    self.save(folder, meta)
                    return web.json_response({'ok': True, 'bytes': size, 'sha256': expected})
                finally:
                    temp.unlink(missing_ok=True)
        except (ValueError, TypeError, KeyError):
            return self.error('Invalid upload metadata.')
        except OSError:
            return self.error('The pod could not store this upload. Check free disk space.', 507)

    async def forward(self, folder, meta, session, upstream):
        async def body():
            for index in range(len(meta['parts'])):
                with (folder / f'{index:03d}.part').open('rb') as source:
                    while block := source.read(128 * 1024):
                        yield block
                        await asyncio.sleep(0)
        try:
            headers = {'Content-Type': meta['contentType'], 'Content-Length': str(meta['bytes']),
                       'Host': urlsplit(upstream).netloc, 'Origin': upstream, 'Accept-Encoding': 'identity'}
            async with session.post(upstream + '/api/jobs', headers=headers, data=body(), allow_redirects=False) as response:
                data = bytearray()
                async for block in response.content.iter_chunked(65536):
                    data.extend(block)
                    if len(data) > 1024**2:
                        raise ValueError('Unexpected oversized job response')
                # Preserve the application's status and JSON, including validation failures.
                json.loads(data)
                meta.update(state='done', status=response.status, body=base64.b64encode(data).decode())
                self.save(folder, meta)
                for part in folder.glob('*.part'):
                    part.unlink()
        except (Exception, asyncio.CancelledError):
            meta.update(state='unknown')
            self.save(folder, meta)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
