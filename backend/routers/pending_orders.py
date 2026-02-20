from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List
from dependencies import get_db_conn,get_ib
from services.pending_orders import *


router = APIRouter(
    prefix="/api/pending_orders",
    tags=["Pending orders"]
)



@router.get("/manual")
async def get_open_orders():
    try:
        return await fetch_manual_orders()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    
@router.delete("/manual/{order_id}")
async def cancel_order(order_id: str):
    try:
        return await cancel_manual_order(order_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    

@router.get("/auto")
async def get_auto_orders(db_conn=Depends(get_db_conn)):
    try:
        return await fetch_auto_orders(db_conn)
    except Exception as e:
        raise HTTPException(status_code=404,detail=f"Failed to fetch auto orders: {str(e)}")


@router.post("/auto/{order_id}")
async def deactivate_auto_order(order_id: int, db_conn=Depends(get_db_conn))-> Dict:
    try:
        return await deactivate_auto_order1(order_id,db_conn)
    except Exception as e:
        # Only catch the custom "not found" error and return 404
        raise HTTPException(status_code=404, detail=f"Failed to deactivate auto orders: {str(e)}")


@router.get("/orders")
async def get_all_pending_orders(db_conn=Depends(get_db_conn),ib=Depends(get_ib))-> List[PendingOrder]:
    try:
        pending_orders = await process_open_orders(db_conn,ib)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [PendingOrder(**order.__dict__)for order in pending_orders]

