FROM python:3.11-slim

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    wget \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libxkbcommon0 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Instala Playwright
RUN pip install playwright mcp
RUN playwright install --with-deps

# Copia seu código MCP
WORKDIR /code
COPY . /code

# Define comando para rodar o MCP
EXPOSE 8080
CMD ["python", "main.py", "--port", "8080"]