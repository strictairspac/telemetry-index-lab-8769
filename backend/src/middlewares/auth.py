import jwt
from fastapi import Depends,HTTPException
from fastapi.security import HTTPBearer
from src.config.settings import JWT_SECRET
from src.config.database import Session
from src.models.identity import User
bearer=HTTPBearer(auto_error=False)
def current(auth=Depends(bearer)):
 try:
  data=jwt.decode(auth.credentials,JWT_SECRET,algorithms=['HS256'])
  with Session() as s:u=s.get(User,int(data['sub']))
  if not u:raise ValueError()
  return u
 except Exception:raise HTTPException(401,'登录已过期，请重新登录')
def writer(u=Depends(current)):
 if u.role not in ('admin','operator'):raise HTTPException(403,'观察员只有读取与导出权限')
 return u
def admin(u=Depends(current)):
 if u.role!='admin':raise HTTPException(403,'此记录仅管理员可见')
 return u
