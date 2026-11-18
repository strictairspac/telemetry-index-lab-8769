"""Controlled MySQL index trial. All dynamic identifiers are server-whitelisted."""
import hashlib,json,time,statistics
from sqlalchemy import text
from src.config.database import engine
from src.constants.enums import COLUMNS
def prefix_score(columns,w):
 equality={'sensor_id'}|({'device_type'} if w.get('device_type') else set())
 score=0;matched=[];reason=[]
 for col in columns:
  if col in equality:score+=40;matched.append(col);reason.append(f'{col}: equality prefix +40')
  elif col=='recorded_at':score+=20;matched.append(col);reason.append('recorded_at: range +20; following columns cannot extend lookup');break
  else:reason.append(f'{col}: unmatched prefix stops lookup');break
 return {'score':min(100,score),'matched_prefix':matched,'reason':reason,'formula':'equality prefix +40/column, first range +20, max 100; heuristic only'}
def _timed(conn,query,params):
 samples=[];rows=[]
 for _ in range(5):
  start=time.perf_counter();rows=[dict(r) for r in conn.execute(text(query),params).mappings()];samples.append((time.perf_counter()-start)*1000)
 return rows,round(statistics.median(samples),4),[round(x,4) for x in samples]
def _write_probe(conn):
 t=time.perf_counter()
 conn.execute(text("INSERT INTO telemetry(sensor_id,recorded_at,device_type,value) VALUES (999,'2026-11-17T12:00:00','pump',42.5)"),[{} for _ in range(20)])
 elapsed=(time.perf_counter()-t)*1000;conn.rollback();return round(elapsed,4)
def run(snapshot):
 w=snapshot['workload'];columns=snapshot['candidate']['columns']
 if not columns or any(c not in COLUMNS for c in columns) or len(set(columns))!=len(columns):raise ValueError('invalid index columns')
 params={'sensor':w['sensor_id'],'start':w['start_at'],'end':w['end_at']}
 query='SELECT id,sensor_id,recorded_at,device_type,value FROM telemetry WHERE sensor_id=:sensor AND recorded_at>=:start AND recorded_at<=:end'
 if w.get('device_type'):query+=' AND device_type=:device';params['device']=w['device_type']
 query+=' ORDER BY id'
 with engine.connect() as conn:
  if conn.execute(text("SELECT GET_LOCK('index_trial_serial',30)")).scalar()!=1:raise RuntimeError('another trial holds lock')
  try:
   existing=conn.execute(text("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='telemetry' AND index_name='trial_candidate'")).scalar()
   if existing:conn.execute(text('DROP INDEX trial_candidate ON telemetry'));conn.commit()
   before=[dict(r) for r in conn.execute(text('EXPLAIN '+query),params).mappings()]
   rows,before_ms,before_samples=_timed(conn,query,params)
   write_before=_write_probe(conn)
   conn.execute(text('CREATE INDEX trial_candidate ON telemetry ('+','.join('`'+c+'`' for c in columns)+')'));conn.commit()
   conn.execute(text('ANALYZE TABLE telemetry')).all()
   after=[dict(r) for r in conn.execute(text('EXPLAIN '+query),params).mappings()]
   result,after_ms,after_samples=_timed(conn,query,params)
   write_after=_write_probe(conn)
   canonical=lambda records:hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
   return {'prefix':prefix_score(columns,w),'query':query,'parameters':params,'rows':len(result),'before_plan':before,'after_plan':after,'before_median_ms':before_ms,'after_median_ms':after_ms,'before_samples_ms':before_samples,'after_samples_ms':after_samples,'insert20_before_ms':write_before,'insert20_after_ms':write_after,'equal_results':canonical(rows)==canonical(result),'checksum':canonical(result),'preview':result[:10],'limitations':['5000-row deterministic dataset; timings include Python driver and cache effects','20-row write probe is rolled back; sequence gaps are normal','heuristic prefix score is not MySQL optimizer cost; no guaranteed speedup']}
  finally:
   conn.rollback()
   try:
    exists=conn.execute(text("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='telemetry' AND index_name='trial_candidate'")).scalar()
    if exists:conn.execute(text('DROP INDEX trial_candidate ON telemetry'));conn.commit()
   finally:conn.execute(text("SELECT RELEASE_LOCK('index_trial_serial')"));conn.commit()
