import logging
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
def install_errors(app):
 @app.exception_handler(IntegrityError)
 async def conflict(request,exc):return JSONResponse({'detail':'名称已存在或关联数据不存在，请修改后重试'},status_code=409)
 @app.exception_handler(Exception)
 async def failure(request,exc):
  logging.exception('request failure')
  return JSONResponse({'detail':'服务暂时不可用，输入已保留，请稍后重试'},status_code=500)
