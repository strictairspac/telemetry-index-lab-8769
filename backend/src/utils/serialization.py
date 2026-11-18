def record(row):
 return {c.name:(getattr(row,c.name).isoformat() if hasattr(getattr(row,c.name),'isoformat') else getattr(row,c.name)) for c in row.__table__.columns if c.name!='password'}
