from fastapi import APIRouter, HTTPException
from services.script import run_script





router = APIRouter(
    prefix="/api",
    tags=["Scripts"]
)


@router.post("/run-script")
def start_script():

    try:
        output = run_script()
        return {"output": output}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
