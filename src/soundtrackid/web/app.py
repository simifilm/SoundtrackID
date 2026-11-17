"""The FastAPI app: its startup, the page, and the routes of the modules next
to this one."""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from soundtrackid import paths
from soundtrackid.envfiles import load_env_files
from soundtrackid.web import analyze, data, history, keys, stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Move what an earlier Windows build kept under the macOS path, read the
    # environment files and apply the saved API keys over them, then pre-warm
    # the classifier so the first request doesn't block.
    if paths.FROZEN and sys.platform == "win32":
        paths.move_misplaced_windows_data(data.root)
    load_env_files()  # .env.dev, then .env
    keys.load_saved_keys()
    await asyncio.to_thread(analyze.get_classifier)
    yield


class _RevalidatedStaticFiles(StaticFiles):
    """The page's styles and scripts, checked for changes on every load (a 304
    when unchanged), so an edit or an update shows on a normal reload."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="SoundtrackID", lifespan=lifespan)
app.mount(
    "/static", _RevalidatedStaticFiles(directory=str(paths.STATIC_DIR)), name="static"
)
for module in (keys, analyze, stream, history):
    app.include_router(module.router)


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    # No-store, like the styles and scripts it loads (revalidated), so UI
    # changes show on a normal reload instead of a stale cached page.
    return FileResponse(
        paths.STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
