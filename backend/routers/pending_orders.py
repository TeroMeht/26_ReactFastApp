from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List
from services.pending_orders import OrderService
from dependencies import get_db_conn, release_db_conn,get_ib

from schemas.api_schemas import AutoOrderResponse

router = APIRouter(
    prefix="/api/pending_orders",
    tags=["pending_orders"]
)


@router.get("/manual")
async def get_open_orders(db_conn=Depends(get_db_conn),ib=Depends(get_ib)):

    service = OrderService(db_conn,ib)
    try:
        orders = await service.fetch_manual_orders()

        if orders is None:
            raise HTTPException(
                status_code=502,
                detail="Failed to fetch orders from Alpaca"
            )

        return orders

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    
@router.delete("/manual/{order_id}")
async def cancel_order(order_id: str,db_conn=Depends(get_db_conn),ib=Depends(get_ib)):

    service = OrderService(db_conn,ib)
    try:
        
        return await service.cancel_manual_order(order_id)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/auto", response_model=List[AutoOrderResponse])
async def get_auto_orders(db_conn=Depends(get_db_conn),ib=Depends(get_ib)):

    service = OrderService(db_conn,ib)
    try:
        return await service.fetch_auto_orders()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch auto orders: {str(e)}"
        )
    finally:
        await release_db_conn(db_conn)


@router.post("/auto/{order_id}")
async def deactivate_auto_order(order_id: int, db_conn=Depends(get_db_conn),ib=Depends(get_ib)):

    service = OrderService(db_conn,ib)
    try:
        result = await service.deactivate_auto_order(order_id)

        if result["status"] == "not_found":
            raise HTTPException(
                status_code=404,
                detail=f"No auto order found with ID {order_id}"
            )

        return {
            "status": "success",
            "message": f"Auto order {order_id} deactivated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to deactivate auto order: {str(e)}"
        )
    finally:
        await release_db_conn(db_conn)





@router.get("/pending", response_model=List[Dict])
async def get_all_pending_orders(db_conn=Depends(get_db_conn),ib=Depends(get_ib)):
    """
    Fetch all pending orders (Alpaca + DB auto orders combined).
    Used for testing process_open_orders().
    """
    service = OrderService(db_conn, ib)

    try:
        result = await service.process_open_orders()

        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        await release_db_conn(db_conn)