from pathlib import Path
import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer

SOURCE = Path(os.environ.get('NWTIA_BOOTSTRAP', str(Path(__file__).resolve().parents[1] / 'outputs/NWTIA-OpenJutsu-V3-RunPod/bootstrap.py')))
sys.path.insert(0, str(SOURCE.parent))
with patch.dict(sys.modules, {'fcntl': types.SimpleNamespace()}):
    spec = importlib.util.spec_from_file_location('upload_bootstrap', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
from gateway_upload import ChunkUploads, PREFIX, MAX_BYTES

TOKEN = 'ab' * 32
ORIGIN = 'https://testpod123-8080.proxy.runpod.net'


class Chunks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.release = asyncio.Event()
        async def backend(request):
            digest, count = hashlib.sha256(), 0
            async for block in request.content.iter_chunked(65536):
                digest.update(block); count += len(block)
            self.calls.append((count, digest.hexdigest(), dict(request.headers)))
            await self.release.wait()
            return web.json_response({'id': 'test-job', 'sha256': digest.hexdigest(), 'bytes': count}, status=202)
        backend_app = web.Application()
        backend_app.router.add_post('/api/jobs', backend)
        self.backend = TestServer(backend_app)
        await self.backend.start_server()
        self.gateway = TestServer(module.web_access_app(TOKEN, ORIGIN,
            str(self.backend.make_url('')).rstrip('/'), upload_root=self.temp.name))
        await self.gateway.start_server()
        self.client = ClientSession()
        import hmac
        cookie = hmac.new(TOKEN.encode(), b'openjutsu-browser-session-v1', hashlib.sha256).hexdigest()
        self.headers = {'Host': '100.65.32.116:60112', 'X-Forwarded-Host': 'testpod123-8080.proxy.runpod.net',
            'X-Forwarded-Proto': 'https', 'Origin': ORIGIN, 'Cookie': '__Host-openjutsu-session=' + cookie}

    async def asyncTearDown(self):
        self.release.set()
        await self.client.close(); await self.gateway.close(); await self.backend.close()
        self.temp.cleanup()

    async def request(self, path, method='GET', **kwargs):
        headers = kwargs.pop('headers', self.headers)
        async with self.client.request(method, self.gateway.make_url(path), headers=headers, **kwargs) as response:
            return response.status, await response.json()

    async def initialize(self, size):
        status, data = await self.request(PREFIX, 'POST', json={'filesBytes': size,
            'contentType': 'multipart/form-data; boundary=diagnostic-boundary'})
        self.assertEqual(status, 201)
        return PREFIX + '/' + data['uploadId']

    async def put(self, base, index, body, sha=None):
        return await self.request(base + '/' + str(index), 'PUT', data=body,
            headers={**self.headers, 'Upload-Sha256': sha or hashlib.sha256(body).hexdigest()})

    async def completed(self, base):
        for _ in range(100):
            status, data = await self.request(base + '/status')
            if not data.get('uploadPending'):
                return status, data
            await asyncio.sleep(.01)
        self.fail('Upload did not complete')

    async def test_large_transport_preserves_bytes_and_submits_exactly_once(self):
        body = bytes(range(256)) * 12000
        base = await self.initialize(len(body))
        parts = [body[:1000000], body[1000000:2000000], body[2000000:]]
        for i, part in enumerate(parts):
            self.assertEqual((await self.put(base, i, part))[0], 200)
        self.assertEqual((await self.put(base, 0, parts[0]))[0], 200)
        commit = {'parts': 3, 'bytes': len(body)}
        self.assertEqual((await self.request(base + '/commit', 'POST', json=commit))[0], 202)
        self.assertEqual((await self.request(base + '/commit', 'POST', json=commit))[0], 202)
        self.release.set()
        status, result = await self.completed(base)
        self.assertEqual(status, 202)
        self.assertEqual(result['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(result['bytes'], len(body))
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('Cookie', self.calls[0][2])
        self.assertEqual(self.calls[0][2]['Origin'], str(self.backend.make_url('')).rstrip('/'))
        self.assertEqual((await self.request(base + '/commit', 'POST', json=commit))[1]['id'], 'test-job')
        self.assertEqual(list(Path(self.temp.name).rglob('*.part')), [])

    async def test_bad_checksum_and_conflicting_retry_cannot_submit(self):
        base = await self.initialize(3)
        self.assertEqual((await self.put(base, 0, b'abc', sha='0' * 64))[0], 409)
        self.assertEqual((await self.put(base, 0, b'abc'))[0], 200)
        self.assertEqual((await self.put(base, 0, b'xyz'))[0], 409)
        self.assertEqual((await self.request(base + '/commit', 'POST', json={'parts': 2, 'bytes': 6}))[0], 409)
        self.assertEqual(self.calls, [])

    async def test_chunk_routes_require_authentication_and_same_origin(self):
        for headers, expected in [({'Host': 'testpod123-8080.proxy.runpod.net'}, 401),
                ({**self.headers, 'Origin': 'https://otherpod-8080.proxy.runpod.net'}, 403)]:
            self.assertEqual((await self.request(PREFIX, 'POST', headers=headers, json={}))[0], expected)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    async def test_limit_path_and_metadata_rejections(self):
        for content_type, size in [('multipart/form-data; boundary=x', MAX_BYTES),
                ('application/json', 123), ('multipart/form-data; boundary=x\r\nHost: evil', 123)]:
            status, _ = await self.request(PREFIX, 'POST', json={'contentType': content_type, 'filesBytes': size})
            self.assertIn(status, (400, 413))
        self.assertEqual((await self.request(PREFIX + '/not-a-valid-id/status'))[0], 404)

    async def test_retried_initialization_and_cancel_do_not_leave_duplicate_uploads(self):
        data = {'uploadId': 'c' * 32, 'filesBytes': 3, 'contentType': 'multipart/form-data; boundary=diagnostic'}
        for _ in range(3):
            self.assertEqual((await self.request(PREFIX, 'POST', json=data))[0], 201)
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)
        base = PREFIX + '/' + data['uploadId']
        await self.put(base, 0, b'abc')
        self.assertEqual((await self.request(base + '/cancel', 'DELETE'))[0], 200)
        self.assertEqual((await self.request(base + '/commit', 'POST', json={'parts': 1, 'bytes': 3}))[0], 410)
        self.assertEqual(list(Path(self.temp.name).rglob('*.part')), [])
        self.assertEqual(self.calls, [])

    async def test_uncertain_submission_is_not_repeated(self):
        base = await self.initialize(3)
        await self.put(base, 0, b'abc')
        await self.backend.close()
        await self.request(base + '/commit', 'POST', json={'parts': 1, 'bytes': 3})
        status, data = await self.completed(base)
        self.assertEqual(status, 409)
        self.assertIn('could not be confirmed', data['error'])
        self.assertEqual((await self.request(base + '/commit', 'POST', json={'parts': 1, 'bytes': 3}))[0], 409)
        self.assertEqual(self.calls, [])

if __name__ == '__main__':
    unittest.main(verbosity=2)
