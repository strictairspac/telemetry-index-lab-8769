from fastapi import APIRouter,Depends
from src.middlewares.auth import current,writer
from src.schemas.business import CandidateInput
from src.services import candidates
from src.controllers.business import save_candidate
router=APIRouter()
@router.get('/candidates')
def listing(u=Depends(current)):return candidates.list_items()
@router.post('/candidates')
def create(data:CandidateInput,u=Depends(writer)):return save_candidate(data,u)
@router.put('/candidates/{item_id}')
def update(item_id:int,data:CandidateInput,u=Depends(writer)):return save_candidate(data,u,item_id)
