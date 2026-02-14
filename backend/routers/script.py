from fastapi import APIRouter, HTTPException
from services.script import ScriptService

router = APIRouter(
    prefix="/api/run-script",
    tags=["scripts"]
)


@router.post("/")
def run_script():
    try:
        service = ScriptService()
        output = service.run_script()
        return {"output": output}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
