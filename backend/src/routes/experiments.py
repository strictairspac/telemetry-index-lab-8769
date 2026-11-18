import json
from fastapi import APIRouter,Depends
from fastapi.responses import Response
from src.middlewares.auth import current,writer
from src.schemas.business import ExperimentInput
from src.services import experiments
from src.controllers.business import start_experiment
router=APIRouter()
@router.get('/experiments')
def listing(u=Depends(current)):return experiments.list_items()
@router.post('/experiments')
def create(data:ExperimentInput,u=Depends(writer)):return start_experiment(data,u)
@router.get('/experiments/{item_id}')
def detail(item_id:int,u=Depends(current)):return experiments.detail(item_id)
@router.get('/experiments/{item_id}/export')
def export(item_id:int,u=Depends(current)):
 return Response(json.dumps(experiments.detail(item_id),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="trial-{item_id}.json"'})
