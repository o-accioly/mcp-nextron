FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

# Diretório de trabalho
WORKDIR /app

# Instala dependências Python (melhor aproveitamento de cache)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do código
COPY . /app

# Ambiente
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

# Expor porta para SSE
EXPOSE 8000

# Executa o MCP por STDIO
CMD ["python", "main.py"]