from sqlalchemy import Column,Integer,String,JSON
from src.config.database import Base
class Workload(Base):
 __tablename__='workloads'
 id=Column(Integer,primary_key=True)
 name=Column(String(120),unique=True,nullable=False)
 sensor_id=Column(Integer,nullable=False)
 start_at=Column(String(30),nullable=False)
 end_at=Column(String(30),nullable=False)
 device_type=Column(String(40),nullable=True)
 description=Column(String(500),nullable=False,default='')
