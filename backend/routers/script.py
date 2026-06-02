from fastapi import APIRouter, HTTPException
from services.script import ScriptService

router = APIRouter(
    prefix="/api",
    tags=["Scripts"]
)


@router.post("/run-script")
def run_script():
    service = ScriptService()
    try:
        output = service.run_script()
        return {"output": output}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/streamer-status")
def streamer_status():
    """
    Health probe for the 22_WatchlistStreamer process. Returns:
        {"status": "running" | "offline" | "error", ...}

    The UI polls this to render the green/grey/red dot next to "Live Strategy
    Assistance". A 200 with status=error means the backend itself can't
    determine the state (psutil missing, script path bad, etc).
    """
    service = ScriptService()
    return service.is_running()
