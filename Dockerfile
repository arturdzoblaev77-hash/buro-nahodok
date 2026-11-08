FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Системное хранилище доверенных корней + корни Минцифры
# (platform-api2.max.ru отдаёт сертификат российской инфраструктуры).
# Каждый шаг гарантирует успешную сборку даже при недоступной сети.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && mkdir -p /usr/local/share/ca-certificates \
    && curl -fsSLo /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
         https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt || true \
    && curl -fsSLo /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
         https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt || true \
    && update-ca-certificates || true \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static

ENV DATA_DIR=/app/data
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" > /dev/null 2>&1 || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
