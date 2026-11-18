from sqlalchemy import Column,Integer,String,JSON,ForeignKey
from src.config.database import Base
class Candidate(Base):
 __tablename__='candidates'
 id=Column(Integer,primary_key=True)
 name=Column(String(120),unique=True,nullable=False)
 workload_id=Column(Integer,ForeignKey('workloads.id'),nullable=False)
 columns=Column(JSON,nullable=False)
 note=Column(String(500),nullable=False,default='')
