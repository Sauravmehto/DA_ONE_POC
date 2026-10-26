# ---- base ----
FROM python:3.12-slim AS base
WORKDIR /app
RUN pip install pipenv

# ---- dev ----
FROM base AS dev
COPY Pipfile Pipfile.lock ./
RUN pipenv install --dev --system --deploy
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- prod ----
FROM base AS prod
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy
COPY . .
EXPOSE 8000
# --no-proxy-headers: the app decides which X-Forwarded-For to believe, from
# TRUSTED_PROXIES (app/core/rate_limit.py). uvicorn rewriting the client
# address first would let a FORWARDED_ALLOW_IPS setting widen that silently.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--no-proxy-headers"]
