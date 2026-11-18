from fastapi import HTTPException
from src.config.database import Session
from src.models.workload import Workload
from src.models.candidate import Candidate
from src.utils.serialization import record
def list_items():
 with Session() as s:return [record(r) for r in s.query(Candidate).order_by(Candidate.id.desc()).all()]
def save(data,item_id=None):
 with Session() as s:
  if not s.get(Workload,data.workload_id):raise HTTPException(404,'未找到负载')
  row=s.get(Candidate,item_id) if item_id else Candidate()
  if not row:raise HTTPException(404,'未找到索引候选')
  for k,v in data.model_dump().items():setattr(row,k,v)
  s.add(row);s.commit();s.refresh(row);return record(row)
