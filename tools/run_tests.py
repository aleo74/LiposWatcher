"""Run in a disposable app container with tests and dev requirements mounted."""
import os
import subprocess
import sys
from urllib.parse import urlsplit

url = os.environ.get('TEST_DATABASE_URL', '')
name = urlsplit(url).path.removeprefix('/')
dev = urlsplit(os.environ.get('DATABASE_URL', '')).path.removeprefix('/')
if not name.endswith('_test') or name == dev:
    raise SystemExit('TEST_DATABASE_URL doit désigner une base *_test distincte du développement')
subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '-r', 'requirements-dev.txt'], check=True)
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *sys.argv[1:]]))
