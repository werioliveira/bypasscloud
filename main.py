# main.py
import argparse
import logging
from logger import setup_logger
from api import app
import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cloudflare Bypass API")
    parser.add_argument("--debug", action="store_true", help="Habilita log de debug")
    parser.add_argument("--port", help="Porta do servidor", default=8000)
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logger = setup_logger("cloudflare-bypass", level=log_level)
    # O logger raiz do pacote deve ser usado por todos os módulos
    logging.getLogger("cloudflare-bypass").handlers = logger.handlers
    logging.getLogger("cloudflare-bypass").setLevel(log_level)

    logger.info(f"Iniciando servidor na porta {args.port}")
    uvicorn.run(app, host="0.0.0.0", port=int(args.port), log_config=None)