from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import create_tables
from app.templates import templates  # noqa: F401
import app.models  # noqa: F401


@asynccontextmanager
async def lifespan(app):
    create_tables()
    yield


app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

from app.routers import auth as auth_router
from app.routers import dashboard as dashboard_router
from app.routers import ferments as ferments_router
from app.routers import batches as batches_router
from app.routers import logs as logs_router
from app.routers import ingredients as ingredients_router
from app.routers import additives as additives_router
from app.routers import schedules as schedules_router
from app.routers import tools as tools_router
from app.routers import users as users_router
from app.routers import settings as settings_router

app.include_router(auth_router.router)
app.include_router(dashboard_router.router)
app.include_router(logs_router.router)       # before batches — avoids route shadowing
app.include_router(batches_router.router)
app.include_router(ferments_router.router)
app.include_router(ingredients_router.router)
app.include_router(additives_router.router)
app.include_router(schedules_router.router)
app.include_router(tools_router.router)
app.include_router(users_router.router)
app.include_router(settings_router.router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}