import json
import traceback

from aiohttp import web
from server import PromptServer
from .worker.worker_manager import get_worker_manager

print("[StemSeparator] Loading server_routes.py")

def _json(data, status=200):
    return web.Response(
        text=json.dumps(data),
        status=status,
        content_type="application/json",
    )


def register_routes():
    ps = PromptServer.instance
    wm = get_worker_manager()

    @ps.routes.get("/stem_separator/status")
    async def stem_separator_status(request):
        s = wm.status_snapshot()
        return _json({"ok": True, "status": s})

    @ps.routes.post("/stem_separator/unload")
    async def stem_separator_unload(request):
        # Phase 1: models aren’t loaded yet; unload exists and must be safe.
        # In later phases this will clear actual model caches.
        try:
            wm.unload_models()
            return _json({"ok": True})
        except Exception as e:
            return _json({"ok": False, "error": str(e)}, status=500)

    @ps.routes.post("/stem_separator/restart")
    async def stem_separator_restart(request):
        try:
            interrupted = wm.restart_worker(force=True)
            return _json({"ok": True, "interrupted_active_job": interrupted})
        except Exception as e:
            return _json({"ok": False, "error": str(e)}, status=500)

    @ps.routes.post("/stem_separator/cancel")
    async def stem_separator_cancel(request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        job_id = body.get("job_id")
        try:
            ok = wm.cancel_job(job_id=job_id)
            return _json({"ok": ok})
        except Exception as e:
            return _json({"ok": False, "error": str(e)}, status=500)


# Register routes at import time (ComfyUI pattern used by many custom nodes)
register_routes()