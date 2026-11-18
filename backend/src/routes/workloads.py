from fastapi import APIRouter,Depends
from src.middlewares.auth import current,writer
from src.schemas.business import WorkloadInput
from src.services import workloads
from src.controllers.business import save_workload
router=APIRouter()
@router.get('/workloads')
def listing(u=Depends(current)):return workloads.list_items()
@router.post('/workloads')
def create(data:WorkloadInput,u=Depends(writer)):return save_workload(data,u)
@router.put('/workloads/{item_id}')
def update(item_id:int,data:WorkloadInput,u=Depends(writer)):return save_workload(data,u,item_id)
