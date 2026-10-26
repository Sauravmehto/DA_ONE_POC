# AiReporting

Private credit reporting application with a React frontend and FastAPI backend.

## Stack

| Layer    | Tech                                                |
| -------- | --------------------------------------------------- |
| Frontend | React 19, Vite 7, TypeScript, Tailwind 4, shadcn/ui |
| Backend  | FastAPI, Python 3.12, pipenv                        |

## Getting started

Run the backend and frontend in separate terminals:

1. **[Backend setup & run](backend/README.md)** — install Python deps and start the API on port 8000
2. **[Frontend setup & run](frontend/README.md)** — install Node deps and start the dev server on port 5173

With both running, open [http://localhost:5173](http://localhost:5173). The frontend proxies `/api` to the backend in development.

## Docker

For containerized runs, see `docker-compose.yml` (local) and `docker-compose.prod.yml` (production) in the repo root. Hosted dev uses `docker-compose.dev.yml`.

## Deployment

Production and hosted-dev setup (Redis, volumes, accounts and email, Connect Snowflake, secrets, Portainer, and image tags) is documented in **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**. Read that before deploying — several settings will break login or persistency if missed.