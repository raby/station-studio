"""Launch the FastAPI app locally on :8001 (so it doesn't collide with weekly on :8000)."""
from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    uvicorn.run("api.main:app", host="127.0.0.1", port=8001, reload=True)
