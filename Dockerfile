# Dockerfile para Betting Analytics
FROM python:3.11-slim

# Evitar prompts interactivos y buffers de salida
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar requerimientos e instalar dependencias
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pg8000 apscheduler

# Copiar el código de la aplicación
COPY . /app/

# Exponer el puerto para Streamlit
EXPOSE 8501

# Por defecto arranca el dashboard de Streamlit
CMD ["streamlit", "run", "src/views/streamlit_view.py", "--server.port=8501", "--server.address=0.0.0.0"]
