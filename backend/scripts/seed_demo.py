from datetime import datetime,timedelta
from src.config.database import Base,engine,Session
from src.models.identity import User,Audit
from src.models.workload import Workload
from src.models.candidate import Candidate
from src.models.experiment import Experiment
from src.models.telemetry import Telemetry
from src.utils.security import hash_password
def seed():
 Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','operator','viewer'):
   if not s.query(User).filter_by(email=role+'@index.local').first():s.add(User(email=role+'@index.local',role=role,password=hash_password('Trial2026!')))
  workloads=[('泵站17温升排查',17,None),('电机36日班回溯',36,'motor'),('阀组59异常区间',59,'valve')]
  for name,sensor,device in workloads:
   if not s.query(Workload).filter_by(name=name).first():s.add(Workload(name=name,sensor_id=sensor,start_at='2026-11-01T00:00:00',end_at='2026-11-30T23:59:59',device_type=device,description='岩川设备可靠性组 / November 2026'))
  s.flush()
  for i,(name,_,_) in enumerate(workloads):
   cname=['传感器优先复合索引','时间优先反例','设备类型覆盖候选'][i]
   if not s.query(Candidate).filter_by(name=cname).first():s.add(Candidate(name=cname,workload_id=s.query(Workload).filter_by(name=name).one().id,columns=[['sensor_id','recorded_at'],['recorded_at','sensor_id'],['device_type','sensor_id','recorded_at']][i],note='固定演示候选，可编辑'))
  existing=set(x[0] for x in s.query(Telemetry.id))
  base=datetime(2026,11,1)
  data=[{'id':i+1,'sensor_id':i%100+1,'recorded_at':(base+timedelta(minutes=i*8)).isoformat(),'device_type':['pump','motor','valve'][i%3],'value':round(30+(i*17%900)/10,2)} for i in range(5000) if i+1 not in existing]
  if data:s.bulk_insert_mappings(Telemetry,data)
  s.commit()
  counts={m.__tablename__:s.query(m).count() for m in (User,Workload,Candidate,Telemetry,Experiment)}
  print(counts);return counts
if __name__=='__main__':seed()
