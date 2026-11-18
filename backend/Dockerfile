# Uma imagem para os três assinantes do backend; o compose escolhe o comando:
#   python -m persistencia | uvicorn operador.app:app | uvicorn motorista.app:app
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 app
USER app
