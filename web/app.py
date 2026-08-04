"""
FastAPI entry point for the Smart API Testing Assistant web UI.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from web.routes.generate import router as generate_router
from web.routes.export import router as export_router
from web.routes import cicd
from web.routes import execute
from web.routes import security
from web.routes import openapi
from web.routes import history
from core.database import init_db

app = FastAPI(
    title="Smart API Testing Assistant",
    description="AI-powered API test case generator",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate_router)
app.include_router(export_router)
app.include_router(cicd.router, prefix="/api")
app.include_router(execute.router, prefix="/api")
app.include_router(security.router, prefix="/api")
app.include_router(openapi.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "web" / "static")), name="static")


@app.on_event("startup")
async def startup() -> None:
    init_db()


@app.get("/")
async def index(request: Request):
    return FileResponse(str(PROJECT_ROOT / "web" / "templates" / "index.html"))
