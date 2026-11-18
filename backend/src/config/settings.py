import os
DATABASE_URL=os.getenv('DATABASE_URL','mysql+pymysql://trial:trialpass@db:3306/indextrial')
REDIS_URL=os.getenv('REDIS_URL','redis://redis:6379/0')
JWT_SECRET=os.environ['JWT_SECRET']
