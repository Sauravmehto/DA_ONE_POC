# Demo deployment on Render (docs/specs/0009-free-demo-deployment.md).
# Lives inside backend/ so Render's Root Directory can be "backend" and the
# Dockerfile path left at its default. The real image definition is
# docker/backend.Dockerfile; this is its prod stage with ONE worker, because
# SESSION_BACKEND=memory keeps a session in a single process.
FROM python:3.12-slim
WORKDIR /app
RUN pip install pipenv
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy
COPY . .
EXPOSE 8000
# --no-proxy-headers: see docker/backend.Dockerfile.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-proxy-headers"]
