"""CPU-only integration tests for private browser access; no models, GPU, or real secrets."""
from pathlib import Path
import asyncio
import gzip
import hashlib
import hmac
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
    spec = importlib.util.spec_from_file_location('browser_bootstrap', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
TOKEN = 'ab' * 32  # Test fixture only; production always uses secrets.token_hex(32).
ORIGIN = 'https://testpod123-8080.proxy.runpod.net'
HOST = 'testpod123-8080.proxy.runpod.net'
SESSION = hmac.new(TOKEN.encode(), b'openjutsu-browser-session-v1', hashlib.sha256).hexdigest()
COOKIE = '__Host-openjutsu-session=' + SESSION


class PersistentLink(unittest.TestCase):
    def test_random_link_persists_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            token = module.web_access_token(name)
            self.assertRegex(token, r'^[a-f0-9]{64}$')
            self.assertNotEqual(token, TOKEN)
            self.assertEqual(module.web_access_token(name), token)
            path = Path(name) / '.web-access-token'
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('invalid')
            with self.assertRaises(RuntimeError):
                module.web_access_token(name)


class BrowserGateway(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.release_stream = asyncio.Event()

        async def backend(request):
            self.calls.append(request.path_qs)
            if request.path == '/events':
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                await response.write(b'data: first\n\n')
                await self.release_stream.wait()
                await response.write(b'data: last\n\n')
                return response
            if request.path == '/compressed':
                return web.Response(body=gzip.compress(b'video test payload'), headers={'Content-Encoding': 'gzip'})
            if request.path == '/redirect':
                raise web.HTTPFound(str(self.backend.make_url('/destination')))
            body = await request.read()
            return web.json_response({'method': request.method, 'path': request.path_qs,
                'size': len(body), 'sha': hashlib.sha256(body).hexdigest(),
                'host': request.host, 'origin': request.headers.get('Origin'),
                'cookie': request.headers.get('Cookie'),
                'forwarded_host': request.headers.get('X-Forwarded-Host'),
                'proto': request.headers.get('X-Forwarded-Proto')})

        backend_app = web.Application(client_max_size=8 * 1024**2)
        backend_app.router.add_route('*', '/{tail:.*}', backend)
        self.backend = TestServer(backend_app)
        await self.backend.start_server()
        self.gateway = TestServer(module.web_access_app(TOKEN, ORIGIN, str(self.backend.make_url('')).rstrip('/')))
        await self.gateway.start_server()
        self.client = ClientSession(auto_decompress=False)

    async def asyncTearDown(self):
        self.release_stream.set()
        await self.client.close()
        await self.gateway.close()
        await self.backend.close()

    def headers(self, **extra):
        return {'Host': HOST, 'Cookie': COOKIE, **extra}

    async def call(self, path='/', method='GET', headers=None, **kwargs):
        return await self.client.request(method, self.gateway.make_url(path), headers=headers or {'Host': HOST}, **kwargs)

    async def test_anonymous_requests_never_reach_the_application(self):
        for headers in [{'Host': HOST}, {'Host': HOST, 'Cookie': '__Host-openjutsu-session=wrong'}]:
            async with await self.call('/api/jobs', method='POST', headers=headers, data=b'not forwarded') as response:
                self.assertEqual(response.status, 401)
        async with await self.call('/') as response:
            html = await response.text()
            self.assertEqual(response.status, 200)
            self.assertNotIn(TOKEN, html)
            self.assertIn('location.hash', html)
            self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
        self.assertEqual(self.calls, [])

    async def test_login_requires_valid_link_and_issues_private_cookie(self):
        async with await self.call('/__openjutsu/login', method='POST', json={'access': TOKEN}) as response:
            self.assertEqual(response.status, 403)
        for wrong in ['wrong', 'é' * 64, None, 123]:
            async with await self.call('/__openjutsu/login', method='POST', headers={'Host': HOST, 'Origin': ORIGIN}, json={'access': wrong}) as response:
                self.assertEqual(response.status, 401)
        async with await self.call('/__openjutsu/login', method='POST', headers={'Host': HOST, 'Origin': ORIGIN}, json={'access': TOKEN}) as response:
            self.assertEqual(response.status, 200)
            cookie = response.headers['Set-Cookie']
            for part in ['__Host-openjutsu-session=', 'HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/']:
                self.assertIn(part, cookie)
            self.assertNotIn(TOKEN, cookie)
        self.assertEqual(self.calls, [])

    async def test_split_login_body_and_body_limit(self):
        async def split():
            for byte in json.dumps({'access': TOKEN}).encode():
                yield bytes([byte])
                await asyncio.sleep(0)
        async with await self.call('/__openjutsu/login', method='POST', headers={'Host': HOST, 'Origin': ORIGIN}, data=split()) as response:
            self.assertEqual(response.status, 200)
        async with await self.call('/__openjutsu/login', method='POST', headers={'Host': HOST, 'Origin': ORIGIN}, data=b'x' * 1025) as response:
            self.assertEqual(response.status, 413)

    async def test_real_runpod_relay_can_open_login_and_forward_authenticated_requests(self):
        relay = {'Host': '100.65.32.116:60112', 'X-Forwarded-Host': HOST,
                 'X-Forwarded-Proto': 'https'}
        async with await self.call('/', headers=relay) as response:
            self.assertEqual(response.status, 200)
        async with await self.call('/api/models', headers=relay) as response:
            self.assertEqual(response.status, 401)
        async with await self.call('/__openjutsu/login', method='POST',
                headers={**relay, 'Origin': ORIGIN}, json={'access': TOKEN}) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('Secure', response.headers['Set-Cookie'])
        async with await self.call('/api/probe', method='POST', data=b'probe',
                headers={**relay, 'Origin': ORIGIN, 'Cookie': COOKIE}) as response:
            self.assertEqual(response.status, 200)
            result = await response.json()
            self.assertIsNone(result['cookie'])
            self.assertIsNone(result['forwarded_host'])
            self.assertEqual(result['origin'], str(self.backend.make_url('')).rstrip('/'))
        async with await self.call('/api/probe', method='POST',
                headers={**relay, 'Origin': 'https://otherpod-8080.proxy.runpod.net', 'Cookie': COOKIE}) as response:
            self.assertEqual(response.status, 403)

    async def test_forwarded_host_cannot_replace_authentication_or_accept_foreign_relays(self):
        relay = {'Host': '100.65.32.116:60112', 'X-Forwarded-Host': HOST,
                 'X-Forwarded-Proto': 'https', 'Cookie': COOKIE}
        for invalid in [{'Host': 'evil.invalid'}, {'Host': '8.8.8.8:8080'},
                        {'X-Forwarded-Host': 'otherpod-8080.proxy.runpod.net'},
                        {'X-Forwarded-Proto': 'http'}, {'X-Forwarded-Host': HOST + ', evil.invalid'}]:
            async with await self.call('/api/models', headers={**relay, **invalid}) as response:
                self.assertEqual(response.status, 421)
        self.assertEqual(self.calls, [])

    async def test_uploads_stream_and_private_proxy_headers_are_rewritten(self):
        block = b'public test upload' * 4096
        async def body():
            for _ in range(24):
                yield block
        headers = self.headers(Origin=ORIGIN, **{'Sec-Fetch-Site': 'same-origin', 'X-Forwarded-Host': 'evil.invalid',
                                              'X-Forwarded-Proto': 'https'})
        async with await self.call('/api/jobs?mode=simple', method='POST', headers=headers, data=body()) as response:
            self.assertEqual(response.status, 200)
            result = await response.json()
            self.assertEqual(result['sha'], hashlib.sha256(block * 24).hexdigest())
            self.assertEqual(result['host'], self.backend.make_url('/').host + ':' + str(self.backend.port))
            self.assertEqual(result['origin'], str(self.backend.make_url('')).rstrip('/'))
            self.assertEqual(result['proto'], 'http')
            self.assertIsNone(result['cookie'])
            self.assertIsNone(result['forwarded_host'])

    async def test_other_runpod_origins_cannot_trigger_authenticated_api(self):
        for extra in [{'Origin': 'https://otherpod-8080.proxy.runpod.net'}, {'Sec-Fetch-Site': 'same-site'}, {'Sec-Fetch-Site': 'cross-site'}]:
            async with await self.call('/api/jobs', method='POST', headers=self.headers(**extra), data=b'bad') as response:
                self.assertEqual(response.status, 403)
        async with await self.call('/api/models', headers={'Host': 'evil.invalid', 'Cookie': COOKIE}) as response:
            self.assertEqual(response.status, 421)
        self.assertEqual(self.calls, [])

    async def test_progress_events_are_delivered_before_the_stream_closes(self):
        async with await self.call('/events', headers=self.headers()) as response:
            first = await asyncio.wait_for(response.content.readuntil(b'\n\n'), timeout=2)
            self.assertEqual(first, b'data: first\n\n')
            self.release_stream.set()
            self.assertEqual(await response.read(), b'data: last\n\n')

    async def test_compressed_download_is_not_corrupted(self):
        async with await self.call('/compressed', headers=self.headers()) as response:
            raw = await response.read()
            self.assertEqual(gzip.decompress(raw), b'video test payload')
            self.assertEqual(int(response.headers['Content-Length']), len(raw))

    async def test_redirect_stays_on_the_public_address(self):
        async with await self.call('/redirect', headers=self.headers(), allow_redirects=False) as response:
            self.assertEqual(response.headers['Location'], ORIGIN + '/destination')

    async def test_authenticated_waiting_page_preserves_the_session(self):
        await self.backend.close()
        async with await self.call('/', headers=self.headers()) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('prépare les modèles', await response.text())
            self.assertNotIn('Set-Cookie', response.headers)
        async with await self.call('/api/models', headers=self.headers()) as response:
            self.assertEqual(response.status, 503)
            self.assertIn('demarrage', (await response.json())['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
