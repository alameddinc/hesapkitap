# ---- api: Python backend (dev compose bu stage'i kullanır)
FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- web-build: React uygulamasını derle
FROM node:24-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- prod: tek container, API + statik arayüz aynı porttan
FROM api AS prod
COPY --from=web-build /web/dist /app/web-dist
ENV WEB_DIST=/app/web-dist EKSTRE_DB=/app/data/ekstre.db
RUN useradd --uid 1000 --create-home app && mkdir -p /app/data && chown -R app /app/data
COPY docker-entrypoint.sh /usr/local/bin/
VOLUME ["/app/data"]
# root olarak başlar, data/ sahipliğini düzeltip "app" kullanıcısına geçer (bkz. docker-entrypoint.sh)
ENTRYPOINT ["docker-entrypoint.sh"]
# PORT: bulut servisleri (Render, Railway, Fly…) kendi portunu verir; yoksa 8000
ENV PORT=8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/auth/status')"
CMD ["sh", "-c", "exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
