from src.config.database import Session
from src.models.identity import Audit
def log(actor,action,detail=''):
 with Session() as s:s.add(Audit(actor=actor,action=action,detail=detail));s.commit()
