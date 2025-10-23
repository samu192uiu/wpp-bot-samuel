# services/urls.py
from urllib.parse import urljoin

def build_url(base: str, path: str) -> str:
    if not base.endswith('/'):
        base += '/'
    return urljoin(base, path.lstrip('/'))
