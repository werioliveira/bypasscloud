# ☁️ BypassCloud - Cloudflare Bypass API

API em Python para bypass automático de desafios Cloudflare (Turnstile, checkbox e acesso negado).
Inspirado no [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr).

## ✨ Funcionalidades
- ✅ Bypass de Cloudflare Challenge (Turnstile e "Checking your browser")
- ✅ Detecção de IP bloqueado (Access Denied) com resposta HTTP 403 detalhada
- ✅ Proxy dinâmico por requisição (`http`, `socks5`)
- ✅ Requisições simultâneas (até 5 abas paralelas)
- ✅ Limpeza de sessão (cookies e storage Cloudflare)
- ✅ Desabilita imagens para acelerar carregamento
- ✅ Compatível com Windows e Linux (Docker)

## 📦 Instalação
```bash
git clone https://github.com/seu-usuario/bypasscloud.git
cd bypasscloud
python -m venv venv
source venv/bin/activate   # Linux
venv\Scripts\activate      # Windows
pip install -r requirements.txt