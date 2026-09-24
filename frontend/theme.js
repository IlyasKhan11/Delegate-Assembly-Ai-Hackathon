// Runs before CSS so returning visitors do not see a flash of the wrong theme.
(() => {
  const key='delegate-theme';
  const valid=value=>['light','dark','system'].includes(value)?value:'system';
  let preference='system';
  try{preference=valid(window.localStorage.getItem(key));}catch{}
  const media=window.matchMedia?.('(prefers-color-scheme: dark)');
  const apply=()=>{
    const resolved=preference==='system'?(media?.matches?'dark':'light'):preference;
    document.documentElement.dataset.theme=resolved;
    document.documentElement.style.colorScheme=resolved;
    const meta=document.querySelector('meta[name="theme-color"]');
    if(meta)meta.setAttribute('content',resolved==='dark'?'#101814':'#f4f6f2');
    window.dispatchEvent(new Event('delegate-theme-change'));
  };
  window.DelegateTheme={
    preference:()=>preference,
    current:()=>document.documentElement.dataset.theme,
    set(value){preference=valid(value);try{window.localStorage.setItem(key,preference);}catch{}apply();},
    toggle(){this.set(this.current()==='dark'?'light':'dark');},
  };
  media?.addEventListener?.('change',()=>{if(preference==='system')apply();});
  window.addEventListener('storage',event=>{if(event.key===key||event.key===null){preference=valid(event.newValue);apply();}});
  apply();
})();
