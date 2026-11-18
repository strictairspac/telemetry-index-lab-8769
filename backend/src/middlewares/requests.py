import time,uuid
from starlette.middleware.base import BaseHTTPMiddleware
class RequestTrace(BaseHTTPMiddleware):
 async def dispatch(self,request,call_next):
  started=time.monotonic();response=await call_next(request)
  response.headers['X-Request-ID']=uuid.uuid4().hex
  response.headers['Server-Timing']=f'app;dur={(time.monotonic()-started)*1000:.2f}'
  return response
