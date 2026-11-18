import hashlib,hmac,secrets,jwt
from datetime import datetime,timezone,timedelta
from src.config.settings import JWT_SECRET
def hash_password(password):
 salt=secrets.token_hex(12)
 return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),120000).hex()
def verify_password(password,encoded):
 salt,digest=encoded.split(':')
 return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),120000).hex(),digest)
def token(user):
 return jwt.encode({'sub':str(user.id),'role':user.role,'exp':datetime.now(timezone.utc)+timedelta(hours=8)},JWT_SECRET,algorithm='HS256')
