export function feedback(message,error=false){const box=document.getElementById('feedback');if(box){box.textContent=message;box.className=error?'feedback error':'feedback';box.hidden=false;}}
