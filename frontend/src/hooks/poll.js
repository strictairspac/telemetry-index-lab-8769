export function poll(fn,ms=2500){const id=setInterval(()=>{if(!document.hidden)fn()},ms);return ()=>clearInterval(id);}
