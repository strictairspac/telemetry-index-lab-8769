from sqlalchemy import Column,Integer,String,DateTime,func,Text
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True)
 email=Column(String(120),unique=True,nullable=False)
 password=Column(String(200),nullable=False)
 role=Column(String(20),nullable=False)
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True)
 actor=Column(String(120));action=Column(String(200));detail=Column(Text)
 created_at=Column(DateTime,server_default=func.now())
