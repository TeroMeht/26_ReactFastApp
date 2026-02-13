from fastapi import APIRouter, HTTPException, Depends
from pathlib import Path
from services.script import ScriptService
from core.config import settings, Settings

router = APIRouter(
    prefix="/api/run-script",
    tags=["scripts"]
)

def get_script_service(settings: Settings = Depends(lambda: settings)) -> ScriptService:
    return ScriptService(
        script_dir=Path(settings.SCRIPT_DIR),
        target_script=settings.TARGET_SCRIPT
    )

@router.post("/")
def run_script(service: ScriptService = Depends(get_script_service)):
    try:
        output = service.run_script()
        return {"output": output}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
