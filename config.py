# config.py

# Títulos de página de desafio (PT/EN)
CHALLENGE_TITLES = [
    'just a moment...',
    'só um momento...',
    'aguarde um momento...',
    'um momento…',          # ← português com ellipsis real (três pontos)
    'ddos-guard',
    'verifying you are human. give it a few seconds...',
]

# Seletores CSS comuns em páginas de desafio
CHALLENGE_SELECTORS = [
    'script[src*="/cdn-cgi/challenge-platform/"]',   # ← novo, infalível
    'meta[http-equiv="refresh"][content="360"]',     # ← indicador extra
    '#cf-challenge-running',
    '.ray_id',
    '.attack-box',
    '#cf-please-wait',
    '#challenge-spinner',
    '#trk_jschal_js',
    '#turnstile-wrapper',
    '.lds-ring',
    'td.info #js_info',
    'div.vc div.text-box h2',
    'iframe[src*="challenges.cloudflare.com"]',      # iframe do Turnstile
]

# Palavras-chave infalíveis no HTML (independente de idioma)
CHALLENGE_HTML_KEYWORDS = [
    '/cdn-cgi/challenge-platform/',
    'just a moment',
    'um momento',
    'só um momento',
    'aguarde',
    'verifying you are human',
    'challenge-running',
    'turnstile-wrapper',
]

# Títulos de página de bloqueio (acesso negado)
ACCESS_DENIED_TITLES = [
    'access denied',
    'acesso negado',
    'attention required! | cloudflare',
]

# Seletores CSS comuns em páginas de bloqueio
ACCESS_DENIED_SELECTORS = [
    'div.cf-error-title span.cf-code-label span',
    '#cf-error-details div.cf-error-overview h1',
    '.cf-error-code',
]

# Seletores para o widget Turnstile
TURNSTILE_SELECTORS = [
    "input[name='cf-turnstile-response']",
    "iframe[src*='challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/']"
]

# Número máximo de tentativas de bypass
MAX_RETRIES = 5

# Tamanho mínimo da página (em bytes) para considerar que carregou
MIN_PAGE_SIZE = 10000