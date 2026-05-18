"""FastAPI app for Station Studio."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import stations, sources, generate, meta, rules


app = FastAPI(
    title="Station Studio",
    description="Generalised radio-station playlist generator. See DESIGN.md.",
    version="0.1.0",
)

# SvelteKit dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stations.router)
app.include_router(sources.router)
app.include_router(generate.router)
app.include_router(meta.router)
app.include_router(rules.router)


@app.get("/")
def root():
    return {"service": "station-studio", "version": "0.1.0"}
