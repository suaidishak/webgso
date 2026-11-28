import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import get_settings, now_local
from .db import engine
from .routers import admin, auth, data, emails, reports
from .seed import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# httpx logs full request URLs at INFO, which would leak plant API keys sent as query parameters.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # A Poll now run still "running" belonged to an API process that has since stopped.
    async with engine.begin() as c:
        await c.execute(text("UPDATE poll_run SET status = 'failed', finished_at = now() AT TIME ZONE :tz, "
                             "summary = summary || '{\"error\": \"Interrupted by an API restart\"}'::jsonb "
                             "WHERE status = 'running'"), {"tz": get_settings().timezone})
    yield


app = FastAPI(title="GSO Solar Data Platform (beta)", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","),
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth.router, data.router, admin.router, emails.router, reports.router):
    app.include_router(r, prefix="/api/v1")


@app.get("/api/healthz")
async def healthz():
    async with engine.connect() as c:
        await c.execute(text("SELECT 1"))
    return {"ok": True, "time": now_local()}
