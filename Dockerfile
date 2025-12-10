# Usa a imagem oficial do Playwright para Python (já vem com browsers e dependências do OS)
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

# Define diretório de trabalho
WORKDIR /code

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 1. Instala as libs Python
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2. (Opcional) Se o requirements.txt atualizar o Playwright para uma versão
# diferente da v1.56.0, precisamos garantir os navegadores compatíveis:
RUN playwright install --with-deps chromium

# 3. Copia o código do MCP
COPY . .

# Variáveis do MCP
ENV LOG_LEVEL=INFO \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

CMD ["python", "main.py"]