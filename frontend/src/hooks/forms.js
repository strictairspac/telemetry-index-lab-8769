export function read(form){return Object.fromEntries(new FormData(form));}
export async function submit(form,fn){let b=form.querySelector('[type=submit]');b.disabled=true;try{await fn(read(form));}finally{b.disabled=false;}}
