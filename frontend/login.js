import {request} from './api.js';
const form=document.querySelector('#access-form');
form.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=document.querySelector('#access-submit'), error=document.querySelector('#access-error');
  button.disabled=true;button.textContent='Signing in…';error.hidden=true;
  try{
    await request('access/login',{passcode:document.querySelector('#passcode').value});
    location.replace('/app');
  }catch(e){error.textContent=e.message;error.hidden=false;}
  finally{button.disabled=false;button.textContent='Enter workspace';}
});
