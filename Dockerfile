FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

# Diretório de trabalho
WORKDIR /code

COPY requirements.txt .

# Instala dependências do sistema para compilação (necessário para alguns pacotes Python)
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=pt_BR.UTF-8  
ENV LANGUAGE=pt_BR:pt  
ENV LC_ALL=pt_BR.UTF-8

# Instala dependências Python (melhor aproveitamento de cache)
RUN pip install -r requirements.txt && \
    apt-get update && apt-get install -y nodejs npm && \
    npm install

COPY . .

# Ambiente
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

# Expor porta para SSE
EXPOSE 8000

# Executa o MCP por STDIO
CMD ["/bin/sh", "-c", "python main.py"]