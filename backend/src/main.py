import os,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from src.config.database import engine
from src.services.experiments import queue
from src.routes import auth,workloads,candidates,experiments,audit
from src.middlewares.errors import install_errors
from src.middlewares.requests import RequestTrace
@asynccontextmanager
async def lifespan(app):
 if os.getenv('SEED_DEMO','true').lower()=='true':
  try:
   from scripts.seed_demo import seed
   seed()
  except Exception:logging.exception('seed failed; API remains available')
 yield
app=FastAPI(title='岩川 · 设备遥测索引试验室',lifespan=lifespan)
app.add_middleware(RequestTrace);install_errors(app)
for r in (auth,workloads,candidates,experiments,audit):app.include_router(r.router)
@app.get('/health')
def health():
 with engine.connect() as c:c.execute(text('SELECT 1'))
 queue.ping();return {'status':'ok'}
