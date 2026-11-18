from fastapi import APIRouter,HTTPException,Depends
from src.config.database import Session
from src.models.identity import User
from src.schemas.business import Login
from src.utils.security import verify_password,token
from src.middlewares.auth import current
from src.services.audit import log
router=APIRouter()
@router.post('/auth/login')
def login(data:Login):
 with Session() as s:u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify_password(data.password,u.password):raise HTTPException(401,'邮箱或密码不正确')
 log(u.email,'auth.login');return {'token':token(u),'user':{'email':u.email,'role':u.role}}
@router.get('/auth/me')
def me(u=Depends(current)):return {'email':u.email,'role':u.role}
