"""Allow a browser-access update only when the installed GPU runtime stays identical."""
from pathlib import Path
import ast
import hashlib
import json

bundle = Path(__file__).resolve().parent
previous = ast.parse((bundle / 'bootstrap.base.py').read_text())
current = ast.parse((bundle / 'bootstrap.py').read_text())

def function(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)

for name in ('install', 'download_models', 'checkout', 'unpack', 'sha256', 'start', 'wait_http', 'stop'):
    if ast.dump(function(previous, name)) != ast.dump(function(current, name)):
        raise RuntimeError('Browser update changed the pinned runtime function: ' + name)

def services(tree):
    return {node.args[0].value: ast.dump(node) for node in ast.walk(function(tree, 'main'))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'start'
        and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in {'comfy', 'proxy', 'app'}}

if services(previous) != services(current):
    raise RuntimeError('Browser update changed the tested GPU/application service configuration')
marker = Path('/opt/nwtia/openjutsu-runtime/installed.json')
state = json.loads(marker.read_text())
expected = hashlib.sha256((bundle / 'overlay.zip').read_bytes() + (bundle / 'bootstrap.base.py').read_bytes()).hexdigest()
if state.get('identity') != expected:
    raise RuntimeError('The base installation identity does not match its pinned source')
state['identity'] = hashlib.sha256((bundle / 'overlay.zip').read_bytes() + (bundle / 'bootstrap.py').read_bytes()).hexdigest()
state['browser_access_update'] = True
marker.write_text(json.dumps(state))
print('GPU runtime and application commands unchanged; private browser access update validated.')
