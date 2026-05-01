# utils.py
import re
from urllib.parse import urlparse

def is_safe_url(url: str) -> bool:
    """Verifica se a URL é segura (não aponta para localhost/redes privadas)"""
    parsed_url = urlparse(url)
    ip_pattern = re.compile(
        r"^(127\.0\.0\.1|localhost|0\.0\.0\.0|::1|10\.\d+\.\d+\.\d+|"
        r"172\.1[6-9]\.\d+\.\d+|172\.2[0-9]\.\d+\.\d+|"
        r"172\.3[0-1]\.\d+\.\d+|192\.168\.\d+\.\d+)$"
    )
    hostname = parsed_url.hostname
    if (hostname and ip_pattern.match(hostname)) or parsed_url.scheme == "file":
        return False
    return True