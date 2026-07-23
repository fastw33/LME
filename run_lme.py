from __future__ import annotations

import asyncio
import sys

import uvicorn


if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8010, reload=True)
