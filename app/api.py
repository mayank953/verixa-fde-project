# final app.py — Fraud Analytics FastAPI app entry point
#
# Wires together the app, middleware, static files, and routers.
#
# Run:
#     uvicorn final_app:app --reload --port 8000

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).parent))


from app.endpoint import router 

app = FastAPI(title="Fraud Analytics API", version="1.0.0")

# ── CORS ───────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ─────────────────────────────────────────────────────
app.include_router(router)

# ── Static / UI ────────────────────────────────────────────────
ui_dir = Path(__file__).resolve().parent.parent / "frontend"

print("Serving UI from {}".format(ui_dir))
if ui_dir.exists():
    print(f"Serving UI from {ui_dir}")
    app.mount("/frontend", StaticFiles(directory=str(ui_dir)), name="frontend")



@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main UI."""
    for candidate in [
        Path(__file__).resolve().parent.parent / "frontend" / "index.html",
        Path(__file__).resolve().parent.parent / "index.html",
    ]:
        if candidate.exists():
            return HTMLResponse(candidate.read_text())
    return HTMLResponse("<h1>UI not found</h1>", status_code=404)


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("APP_PORT", "8000")),
        timeout_keep_alive=300,
        reload=False,
    )
