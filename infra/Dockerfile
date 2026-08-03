FROM python:3.12-slim 

ENV DEBIAN_FRONTEND=noninteractive 
ENV PATH="/root/.local/bin:${PATH}" 
ENV PYTHONPATH="/workspaces/network_service" 
ENV PYTHONUNBUFFERED=1

# Instalação de dependências de sistema necessárias para GDAL [cite: 1]
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ gcc libgdal-dev gdal-bin python3-dev \
    && rm -rf /var/lib/apt/lists/* [cite: 1]

# MATCH THIS TO devcontainer.json "workspaceFolder"
WORKDIR /workspaces/network_service

# 1. Copy the entire folder to maintain relative paths for -r base.txt
COPY requirements/ ./requirements/
COPY cisei_lib/ ./cisei_lib/ 

# 2. Use python3 -m pip to ensure it installs to the correct site-packages
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r requirements/prod.txt && \
    python3 -m pip install -e ./cisei_lib

# 3. Copy application code
COPY app/ ./app/

EXPOSE 8080

# Inicializa o Hub (main_hub.py). O Hub gerencia o spawn dos workers.
CMD ["python3", "-m", "uvicorn", "app.main_hub:hub_app", "--host", "0.0.0.0", "--port", "8080"]


