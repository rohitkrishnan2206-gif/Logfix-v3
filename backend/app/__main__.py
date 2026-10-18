"""python -m app  ->  starts the server on 127.0.0.1:8000 (override with HOST/PORT)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:get_app", factory=True, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
