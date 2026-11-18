from sqlalchemy import Column,Integer,String,Float
from src.config.database import Base
class Telemetry(Base):
 __tablename__='telemetry'
 id=Column(Integer,primary_key=True)
 sensor_id=Column(Integer,nullable=False)
 recorded_at=Column(String(30),nullable=False)
 device_type=Column(String(40),nullable=False)
 value=Column(Float,nullable=False)
