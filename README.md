# ☁️ BypassCloud – Cloudflare Bypass API

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)
![Docker](https://img.shields.io/badge/Docker-ready-blueviolet)
![License](https://img.shields.io/github/license/werioliveira/bypasscloud)

API robusta escrita em Python para **resolução automática de desafios Cloudflare** (Turnstile, checkbox "Checking your browser" e detecção de IP banido).  
Projetada para ser usada como **micro-serviço**, recebendo requisições HTTP e retornando o HTML final + cookies, exatamente como um navegador real faria.

Inspirada no [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), porém com código enxuto, fácil manutenção e suporte nativo a **proxy por requisição**.

---

## ✨ Principais Funcionalidades

- ✅ **Resolução de Turnstile** (Cloudflare Challenge) via shadow DOM e fallback por teclado
- ✅ **Detecção de IP bloqueado** ("Access Denied") com resposta HTTP 403 detalhada
- ✅ **Proxy dinâmico por requisição** (`http`, `https`, `socks5`) – sem precisar reiniciar o serviço
- ✅ **Requisições simultâneas** (até 5 abas paralelas)
- ✅ **Limpeza de sessão** (cookies, localStorage e sessionStorage do Cloudflare)
- ✅ **Modo stealth** – desabilita imagens, desabilita flags de automação e randomiza user-agent
- ✅ **Compatível com Windows, Linux e Docker**

---

## 📦 Instalação

### Pré-requisitos
- Python 3.9 ou superior
- Google Chrome ou Chromium instalado
- Pip atualizado

### Passos

```bash
# Clone o repositório
git clone https://github.com/werioliveira/bypasscloud.git
cd bypasscloud

# Crie e ative o ambiente virtual
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt
```

---

## 🚀 Uso

### Inicie o servidor

```bash
python main.py --port 8000
```
> Por padrão, a API ficará disponível em `http://localhost:8000`.

### Endpoints

| Método | Rota | Descrição |
| :--- | :--- | :--- |
| `POST` | `/v1` | Resolve um site protegido |
| `GET` | `/health` | Status do serviço |

### Exemplo de requisição (sem proxy)

```json
{
  "cmd": "request.get",
  "url": "https://sitecomcaptcha.com",
  "maxTimeout": 60000,
  "clear_session": false
}
```

### Exemplo com proxy

```json
{
  "cmd": "request.get",
  "url": "https://sitecomcaptcha.com",
  "proxy": "socks5://127.0.0.1:1080",
  "clear_session": true
}
```

---

## 💬 Respostas da API

### ✅ Sucesso (HTTP 200)

```json
{
  "status": "ok",
  "message": "",
  "version": "2.1.0",
  "solution": {
    "url": "https://sitecomcaptcha.com/",
    "status": 200,
    "response": "<html lang=\"en\">...</html>",
    "cookies": [
      {
        "name": "cf_clearance",
        "value": "xyz..."
      }
    ],
    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "turnstile_token": "0.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  }
}
```

### ⛔ IP Bloqueado (HTTP 403)

```json
{
  "detail": "Access denied: seu IP foi bloqueado pela Cloudflare. Use um proxy ou VPN para tentar novamente."
}
```

### ⏳ Timeout (HTTP 408)

```json
{
  "detail": "Falha ao resolver o desafio Cloudflare (timeout)"
}
```

### 🚫 Erro interno (HTTP 500)

```json
{
  "detail": "Descrição do erro"
}
```

### Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "browser": "connected",
  "platform": "Windows"
}
```

---

## 🐳 Docker

### Build da imagem

```bash
# A partir da raiz do projeto
docker build -f docker/Dockerfile -t bypasscloud .
```

### Executar container

```bash
docker run -d -p 8000:8000 bypasscloud
```

Ou use o `docker-compose.yml` incluído:

```bash
cd docker
docker-compose up -d
```
> A API ficará acessível em `http://localhost:8000`.

---

## ⚙️ Configuração Avançada

Edite o arquivo `config.py` para personalizar:
- Títulos de desafio (português, inglês, outros idiomas)
- Seletores CSS usados para detectar páginas de desafio
- Número máximo de tentativas de bypass (`MAX_RETRIES`)
- Timeout de espera pela página real (em segundos)

**Exemplo de customização para adicionar suporte a espanhol:**

```python
CHALLENGE_TITLES = [
    'just a moment...',
    'um momento…',
    'un momento...',
    'aguarde um momento...',
]
```

---

## 🧠 Como funciona

1. Recebe uma URL via API REST
2. Abre uma aba no Chromium (com ou sem proxy)
3. Detecta se há desafio Cloudflare (título, script `/cdn-cgi/challenge-platform/`, seletores visuais)
4. Se for desafio Turnstile → Localiza o checkbox no shadow DOM e clica
5. Se for desafio comum → Usa `Tab + Space` ou clique direto
6. Aguarda a página real carregar (título muda, elementos do desafio desaparecem)
7. Retorna o HTML, cookies, user-agent e token Turnstile (se houver)

> Todo o processo é transparente e simula um usuário real.

---

## 📁 Estrutura do Projeto

```text
bypasscloud/
├── .gitignore
├── README.md
├── requirements.txt
├── main.py                # Ponto de entrada da aplicação
├── config.py              # Constantes e seletores
├── logger.py              # Logger em formato JSON
├── models.py              # Modelos Pydantic
├── utils.py               # Funções auxiliares
├── bypasser.py            # Motor de bypass Cloudflare
├── browser.py             # Configuração do Chromium
├── api.py                 # Rotas FastAPI
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

---

## 🤝 Contribuindo

1. Faça um fork do projeto
2. Crie uma branch (`git checkout -b feature/nova-funcionalidade`)
3. Commit suas alterações (`git commit -m 'Adiciona funcionalidade X'`)
4. Push para a branch (`git push origin feature/nova-funcionalidade`)
5. Abra um Pull Request

---

## 📝 Licença

Este projeto está sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.

---

## 📧 Contato

**Weri Oliveira**  
GitHub: [@werioliveira](https://github.com/werioliveira)

---
*Feito com muito ☕ e alguns desafios Cloudflare resolvidos.*