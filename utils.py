# utils.py
import re
from typing import Optional
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


# Códigos de erro de navegação do Chrome que indicam problema de rede/proxy
NAVIGATION_ERROR_CODES = (
    'ERR_PROXY_CONNECTION_FAILED',
    'ERR_TUNNEL_CONNECTION_FAILED',
    'ERR_SOCKS_CONNECTION_FAILED',
    'ERR_PROXY_AUTH_REQUESTED',
    'ERR_INVALID_AUTH_CREDENTIALS',
    'ERR_HTTPS_PROXY_TUNNEL_RESPONSE',
    'ERR_NAME_NOT_RESOLVED',
    'ERR_INTERNET_DISCONNECTED',
    'ERR_CONNECTION_TIMED_OUT',
    'ERR_CONNECTION_REFUSED',
    'ERR_CONNECTION_RESET',
    'ERR_EMPTY_RESPONSE',
    'ERR_CERT_AUTHORITY_INVALID',
)


def find_navigation_error(html: str) -> Optional[str]:
    """Procura códigos de erro do Chrome (ex.: ERR_PROXY_CONNECTION_FAILED)
    no HTML da aba. Retorna o código encontrado ou None."""
    if not html:
        return None
    for code in NAVIGATION_ERROR_CODES:
        if code in html:
            return code
    return None