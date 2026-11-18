from fastapi import HTTPException
from src.config.database import Session
from src.models.workload import Workload
from src.utils.serialization import record
def list_items():
 with Session() as s:return [record(r) for r in s.query(Workload).order_by(Workload.id.desc()).all()]
def save(data,item_id=None):
 with Session() as s:
  row=s.get(Workload,item_id) if item_id else Workload()
  if not row:raise HTTPException(404,'未找到负载')
  for k,v in data.model_dump().items():setattr(row,k,v)
  s.add(row);s.commit();s.refresh(row);return record(row)
