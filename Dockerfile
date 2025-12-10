FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

# Diretório de trabalho
WORKDIR /code

# --- 1. Instalação de Dependências do Sistema (Incluindo Node/NPM) ---
# Fazemos isso primeiro pois muda com menos frequência
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=pt_BR.UTF-8  
ENV LANGUAGE=pt_BR:pt  
ENV LC_ALL=pt_BR.UTF-8

# --- 2. Instalação de Dependências Python ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 3. Instalação de Dependências Node ---
# Precisamos copiar o package.json ANTES de rodar npm install
# Se você tiver package-lock.json, é bom copiar também, caso contrário, remova a parte do lock

# --- 4. Copia o código fonte ---
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