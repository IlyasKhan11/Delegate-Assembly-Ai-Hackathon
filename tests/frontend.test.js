import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {resolve, dirname} from 'node:path';

// The state machine and generated HTML run in a minimal DOM harness. This is
// deliberately not a substitute for screenshot or real-browser layout testing.
async function harness(responses = [], options = {}) {
  const storage = new Map();
  const localData = new Map();
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', focus(){}, select(){}, addEventListener(){}, showModal(){}, close(){}, querySelector(){return null;}});
    return elements.get(id);
  }
  const spoken = [];
  const requests = [];
  const context = vm.createContext({
    document: {querySelector: element, createElement: ()=>({click(){}})},
    location: {pathname:'/', hash:''}, history: {replaceState(){}},
    window: {localStorage:{setItem:(key,value)=>localData.set(key,value),getItem:key=>localData.get(key),removeItem:key=>localData.delete(key)},sessionStorage:{setItem:(key,value)=>storage.set(key,value),getItem:key=>storage.get(key),removeItem:key=>storage.delete(key)}, scrollTo(){}, addEventListener(){}, speechSynthesis:{cancel(){}, speak(u){spoken.push(u.text);}}},
    speechSynthesis: {cancel(){}, speak(u){spoken.push(u.text);}},
    SpeechSynthesisUtterance: class {constructor(text){this.text=text;}},
    navigator: {}, console, Date, Math, Number, String, Array, Object,
    setTimeout, clearTimeout, setInterval:()=>1, clearInterval(){},
    AbortController, FormData, Blob, URL,
    fetch: async(url, options)=>{
      requests.push({url,options});
      if(!responses.length)throw new Error(`Unexpected request: ${url}`);
      const next=responses.shift();
      return {ok:next.ok!==false,status:next.status??(next.ok===false?500:200),json:async()=>next.body};
    },
  });
  const modules = new Map();
  async function load(path) {
    if(modules.has(path))return modules.get(path);
    if(path.endsWith('.json')){
      const data=JSON.parse(await readFile(path,'utf8'));
      const mod=new vm.SyntheticModule(['default'],function(){this.setExport('default',data);},{context,identifier:path});
      modules.set(path,mod);return mod;
    }
    const mod=new vm.SourceTextModule(await readFile(path,'utf8'),{context,identifier:path});
    modules.set(path,mod);
    await mod.link(spec=>load(resolve(dirname(path),spec)));
    return mod;
  }
  // Export private state only inside this isolated test copy.
  const path=resolve('frontend/app.js');
  const source=(await readFile(path,'utf8'))+'\nexport {state, render, start, choose, approve, reject, interrupt, custom, end, restart, processTurn, perform, updateCustom, applySnapshot, resumeSession, rememberSession, editReply, speechEndpoint, heardFromRepresentative, flushHeldSpeech};';
  const app=new vm.SourceTextModule(source,{context,identifier:path});
  await app.link(spec=>load(resolve(dirname(path),spec)));
  await app.evaluate();
  // Most cases exercise the explicit sample scenario, not the default workspace.
  if(!options.production){app.namespace.state.source='demo';app.namespace.state.goal='Ask the hotel for early check-in, max $20.';}
  const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
  return {...app.namespace, html:()=>element('#app').innerHTML, spoken, requests, storage, localData, settle};
}

test('landing renders the supplied reference hierarchy', async()=>{
  const h=await harness();
  assert.match(h.html(),/Make the call<br>without speaking/);
  assert.match(h.html(),/Four steps, and you hold/);
  assert.match(h.html(),/The model talks/);
});

test('hotel negotiation requires a separate booking approval and exports a truthful summary',async()=>{
  const h=await harness();await h.start();
  assert.equal(h.state.page,'live');
  assert.equal(h.state.price,0);
  assert.equal(h.state.draft.key,'negotiate');
  assert.equal(h.spoken.length,0);
  await h.approve();
  assert.equal(h.state.price,20);
  assert.equal(h.state.phase,'booking');
  assert.equal(h.state.status,'DECISION_REQUIRED');
  assert.equal(h.state.draft,null);
  await h.choose('confirm');
  assert.equal(h.state.phase,'booking');
  await h.approve();
  assert.equal(h.state.phase,'confirmed');
  await h.end();
  assert.equal(h.state.page,'summary');
  assert.match(h.state.summary.outcome_headline,/held for \$20/);
  assert.equal(h.state.summary.unresolved_items.length,2);
  // The demo's promise is rehearsing without API credits. It may use the free
  // local voice route, but must never reach a session or AI endpoint.
  assert.deepEqual([...new Set(h.requests.map(row=>row.url))].filter(url=>!url.endsWith('/api/demo/speech')),[]);
});

test('one-time approval uses $30 without rewriting the displayed $20 ceiling',async()=>{
  const h=await harness();await h.start();await h.choose('accept');await h.approve();
  assert.equal(h.state.price,30);
  assert.match(h.html(),/limit \$20/);
  await h.choose('confirm');await h.approve();await h.end();
  assert.match(h.state.summary.outcome_headline,/held for \$30/);
  assert.match(h.state.summary.confirmed_items[1],/one-time exception/);
});

test('declining never produces a successful booking summary',async()=>{
  const h=await harness();await h.start();await h.choose('decline');await h.approve();await h.end();
  assert.equal(h.state.price,0);
  assert.match(h.state.summary.outcome_headline,/Offer declined/);
});

test('ending an unresolved call does not fabricate an agreement',async()=>{
  const h=await harness();await h.start();await h.end();
  assert.match(h.state.summary.outcome_headline,/Nothing has been confirmed/);
});

test('reject and interrupt discard staged text without speaking',async()=>{
  const h=await harness();await h.start();await h.reject();
  assert.equal(h.state.draft,null);assert.equal(h.spoken.length,0);
  await h.choose('accept');await h.interrupt();
  assert.equal(h.state.draft,null);assert.equal(h.state.status,'DECISION_REQUIRED');
  assert.equal(h.spoken.length,0);
});

test('interruption during an in-flight operation prevents later speech',async()=>{
  const h=await harness();await h.start();
  let finish;
  const pending=h.perform(()=>new Promise(resolve=>{finish=resolve;}));
  await h.interrupt();finish();await pending;
  assert.equal(h.state.draft,null);
  assert.equal(h.state.status,'DECISION_REQUIRED');
  assert.match(h.state.decision.reason,/You stopped the agent/);
});

test('all five selected languages have sample transcript translations',async()=>{
  for(const language of ['Portuguese (Brazil)','Spanish','French','German','English']){
    const h=await harness();h.state.language=language;await h.start();
    assert.ok(h.state.transcript.every(row=>row.translation.length>0),language);
    if(language!=='English')assert.notEqual(h.state.draft.translation,h.state.draft.text,language);
  }
});

test('custom text is escaped and not falsely presented as translated',async()=>{
  const h=await harness();await h.start();h.state.custom='<img src=x onerror=alert(1)>';await h.custom();
  assert.match(h.html(),/&lt;img/);
  assert.doesNotMatch(h.html(),/<img src=x/);
  assert.match(h.state.draft.translation,/spoken as written/);
  await h.approve();assert.notEqual(h.state.phase,'confirmed');
});

test('backend failures stay visible and do not silently switch to simulation',async()=>{
  const h=await harness([{ok:false,body:{detail:'Add AIML_API_KEY to .env'}}]);
  h.state.source='live';await h.start();
  assert.equal(h.state.source,'live');
  assert.match(h.state.error,/AIML_API_KEY/);
  assert.equal(h.state.started,null);
});

test('AI roleplay creates a real session and stages its opening',async()=>{
  const h=await harness([
    {body:{session_id:'test-session',extracted_rules:{target_business:'Dentist',opening_phrase:'May I book a checkup?'}}},
    {body:{status:'AWAITING_APPROVAL',staged_draft:{english_response:'May I book a checkup?',translated_response:'¿Puedo reservar una consulta?'}}},
  ]);
  h.state.source='live';await h.start();
  assert.equal(h.state.session,'test-session');
  assert.equal(h.state.status,'AWAITING_APPROVAL');
  assert.equal(h.spoken.length,0);
  assert.equal(h.requests[0].url,'/api/session/create');
  assert.equal(h.requests[1].url,'/api/session/test-session/user-action');
  assert.match(h.html(),/Dentist/);
});

test('restart clears prior terms and transcript',async()=>{
  const h=await harness();await h.start();await h.approve();await h.end();h.restart();
  assert.equal(h.state.page,'setup');assert.equal(h.state.price,0);
  assert.equal(h.state.transcript.length,0);assert.equal(h.state.summary,null);
});

test('typing an edit blocks approval of the old draft until preparation',async()=>{
  const h=await harness();await h.start();h.updateCustom('Please decline the offer.');
  assert.equal(h.state.customDirty,true);
  await h.approve();
  assert.equal(h.spoken.length,0);
  assert.equal(h.state.draft.key,'negotiate');
  await h.custom();
  assert.equal(h.state.customDirty,false);
  assert.equal(h.state.draft.text,'Please decline the offer.');
});

test('repeated pause clicks create just one interruption',async()=>{
  const h=await harness();await h.start();
  await h.interrupt();await h.interrupt();await h.interrupt();
  assert.equal(h.state.transcript.filter(row=>row.text==='Agent interrupted. Waiting for your correction.').length,1);
  assert.equal(h.state.interrupted,true);
});

test('a human response synchronizes the decision card without revealing a draft as spoken',async()=>{
  const h=await harness();h.state.source='live';
  h.applySnapshot({status:'DECISION_REQUIRED',transcript_history:[{speaker:'caller',text:'The fee is $30.',time:'00:15'},{speaker:'agent',text:'Let me check with my client.',time:'00:15'}],decision_ledger:[],pending_draft:null,last_decision:{violation_parameter:'price',violation_reason:'The $30 fee exceeds your $20 limit.',suggested_user_options:['Negotiate down to $20']}});
  assert.equal(h.state.decision.price,30);
  assert.equal(h.state.status,'DECISION_REQUIRED');
  await h.settle();
  assert.equal(h.spoken.length,1);
  h.applySnapshot({status:'AWAITING_APPROVAL',transcript_history:h.state.transcript.filter(row=>row.speaker!=='system'),decision_ledger:[],pending_draft:'Could you do twenty dollars?',pending_translation:'¿Podrían ser veinte dólares?'});
  assert.equal(h.state.draft.text,'Could you do twenty dollars?');
  await h.settle();
  assert.equal(h.spoken.length,1);
});

test('new custom replies request faithful translation instead of new negotiation instructions',async()=>{
  const h=await harness([{body:{status:'AWAITING_APPROVAL',staged_draft:{english_response:'Hi',translated_response:'Salut'}}}]);
  h.state.source='live';h.state.session='session';h.state.language='French';h.updateCustom('hi');await h.custom();
  const body=JSON.parse(h.requests[0].options.body);
  assert.equal(body.response_mode,'verbatim');
  assert.equal(body.custom_instruction,'hi');
  assert.equal(h.state.draft.text,'Hi');
});


test('refresh restores the saved call and draft identity without speaking old audio',async()=>{
  const h=await harness([{body:{status:'AWAITING_APPROVAL',mode:'assist',language:'French',raw_prompt:'Ask about rooms',created_at:1700000000,rules:{target_business:'Hotel'},transcript_history:[{speaker:'caller',text:'Hello'},{speaker:'agent',text:'Good morning'}],decision_ledger:[],pending_draft:'Is noon available?',pending_draft_id:'draft-2',participant_path:'/receptionist/private-token'}}]);
  h.state.page='setup';h.storage.set('delegate-active-session','saved-call');await h.resumeSession();
  assert.equal(h.state.page,'live');assert.equal(h.state.session,'saved-call');
  assert.equal(h.state.language,'French');assert.equal(h.state.draft.id,'draft-2');
  assert.equal(h.state.transcript.length,2);assert.equal(h.spoken.length,0);
});

test('restoring a paused call keeps the pause controls and never replays audio',async()=>{
  const h=await harness();
  h.applySnapshot({status:'DECISION_REQUIRED',paused:true,transcript_history:[{speaker:'caller',text:'Hello'},{speaker:'agent',text:'Hello there'}],decision_ledger:[]});
  assert.equal(h.state.interrupted,true);assert.match(h.state.decision.reason,/stopped the agent/);
  assert.equal(h.spoken.length,0);
});

test('a failed opening preparation preserves its created call for recovery',async()=>{
  const h=await harness([{body:{session_id:'saved',extracted_rules:{target_business:'Hotel',opening_phrase:'Hello'}}},{ok:false,body:{detail:'Provider unavailable'}}]);
  h.state.source='live';await h.start();
  assert.equal(h.state.page,'live');assert.equal(h.state.session,'saved');
  assert.equal(h.storage.get('delegate-active-session'),'saved');
  assert.match(h.state.error,/Provider unavailable/);
  assert.equal(h.state.custom,'Hello');
});

test('manual retry after ambiguous failure reuses its request ID',async()=>{
  const h=await harness([{ok:false,body:{detail:'Temporary failure'}},{body:{status:'AWAITING_APPROVAL',draft_id:'d1',staged_draft:{english_response:'Hi',translated_response:'Salut'}}}]);
  h.state.source='live';h.state.session='call';h.updateCustom('hi');await h.custom();await h.custom();
  const [first,second]=h.requests.map(row=>JSON.parse(row.options.body));
  assert.ok(first.request_id);assert.equal(first.request_id,second.request_id);
});

test('approval sends the reviewed draft identity and muted audio stays silent',async()=>{
  const h=await harness([{body:{speak_text:'Hello',session_status:'IN_PROGRESS'}},{body:{decision_ledger:[]}}]);
  Object.assign(h.state,{source:'live',session:'call',draft:{id:'reviewed',text:'Hello'},audioEnabled:false});
  await h.approve();
  assert.equal(JSON.parse(h.requests[0].options.body).draft_id,'reviewed');
  assert.equal(h.spoken.length,0);
});

async function receptionistHarness(){
  const elements=new Map(), spoken=[];let cancelled=0;
  const element=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},setAttribute(){}});return elements.get(id);};
  const context=vm.createContext({location:{pathname:'/receptionist/room'},document:{getElementById:element},window:{addEventListener(){},speechSynthesis:{cancel(){cancelled++;}}},speechSynthesis:{speak(u){spoken.push(u.text);}},SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},setInterval(){}});
  const api=new vm.SyntheticModule(['request'],function(){this.setExport('request',async path=>
    /\/voice$/.test(path)?{voice_live:true,agent_id:'agent-1',token:'one-time',websocket_url:'wss://agents.example/v1/ws'}
                         :{status:'ready',control_revision:0,transcript:[]});},{context});
  const speech=new vm.SyntheticModule(['dictate'],function(){this.setExport('dictate',()=>()=>{});},{context});
  const voice=new vm.SyntheticModule(['canPlaceVoiceCall','startVoiceCall'],function(){
    this.setExport('canPlaceVoiceCall',()=>true);
    this.setExport('startVoiceCall',async(credentials,onEvent)=>{onEvent({type:'ready'});return()=>onEvent({type:'ended'});});
  },{context});
  const listen=new vm.SyntheticModule(['canListenLive','startListening'],function(){
    this.setExport('canListenLive',()=>true);
    this.setExport('startListening',async(credentials,onEvent)=>{onEvent({type:'ready'});return()=>onEvent({type:'ended'});});
  },{context});
  const playback=new vm.SyntheticModule(['speakReply','stopSpeaking'],function(){
    this.setExport('speakReply',async(endpoint,text)=>{spoken.push(text);});
    this.setExport('stopSpeaking',()=>{cancelled++;});
  },{context});
  const source=await readFile('frontend/participant.js','utf8');
  const mod=new vm.SourceTextModule(source+'\nexport {display,poll,sendHeard};',{context});
  await mod.link(spec=>spec==='./api.js'?api
    :spec==='./voice_call.js'?voice
    :spec==='./voice_playback.js'?playback
    :spec==='./live_listen.js'?listen:speech);await mod.evaluate();
  await new Promise(resolve=>setTimeout(resolve,0));
  const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
  return {...mod.namespace,element,spoken,settle,cancelled:()=>cancelled};
}

test('receptionist audio stops on presenter pause and does not speak late added words',async()=>{
  const h=await receptionistHarness();h.element('audio-toggle').listeners.click();
  h.display({status:'ready',control_revision:0,transcript:[{speaker:'agent',text:'Hello'}]});
  await h.settle();
  assert.equal(h.spoken.at(-1),'Hello');const count=h.spoken.length, before=h.cancelled();
  h.display({status:'waiting',paused:true,control_revision:1,transcript:[{speaker:'agent',text:'Hello'},{speaker:'agent',text:'Late words'}]});
  await h.settle();
  assert.ok(h.cancelled()>before);assert.equal(h.spoken.length,count);
  assert.equal(h.element('participant-send').disabled,true);
});

test('ended receptionist rooms stop queued audio and lock further replies',async()=>{
  const h=await receptionistHarness();const before=h.cancelled();
  h.display({status:'ended',control_revision:1,transcript:[]});
  assert.ok(h.cancelled()>before);assert.equal(h.element('participant-text').disabled,true);
  assert.match(h.element('room-status').textContent,/Call ended/);
});

test('French recommendations lead with French while approval still speaks English',async()=>{
  const h=await harness();h.state.language='French';await h.start();
  assert.match(h.html(),/Négocier à \$20/);
  assert.match(h.html(),/Approuver et parler/);
  const preview=h.html().slice(h.html().indexOf('class="draft-preview"'));
  assert.ok(preview.indexOf(h.state.draft.translation)<preview.indexOf(h.state.draft.text));
  assert.match(preview,/Prononcé à la réception · Anglais/);
  h.editReply();assert.equal(h.state.custom,h.state.draft.translation);
  h.updateCustom('');await h.approve();
  assert.match(h.spoken[0],/My guest can go up to twenty dollars/);
});

test('all supported non-English languages localize decision choices and editing controls',async()=>{
  for(const [language,label] of [['Spanish','Negociar hasta $20'],['French','Négocier à $20'],['German','Auf $20 herunterhandeln'],['Portuguese (Brazil)','Negociar até $20']]){
    const h=await harness();h.state.language=language;await h.start();
    assert.ok(h.html().includes(label),language);
    assert.equal(h.state.decision.options[0].value,'negotiate');
    assert.notEqual(h.state.decision.translatedReason,h.state.decision.reason);
  }
});

test('live translated choices preserve canonical action values and survive refresh',async()=>{
  const h=await harness([{body:{status:'AWAITING_APPROVAL',draft_id:'d1',staged_draft:{english_response:'Is Saturday possible?',translated_response:'Samedi est-il possible ?'}}}]);
  Object.assign(h.state,{source:'live',session:'call',language:'French',page:'live'});
  const decision={violation_reason:'Friday is unavailable.',translated_violation_reason:'Vendredi est indisponible.',suggested_user_options:['Ask about Saturday'],translated_user_options:['Demander pour samedi']};
  h.applySnapshot({status:'DECISION_REQUIRED',transcript_history:[],decision_ledger:[],last_decision:decision});h.render();
  assert.match(h.html(),/Demander pour samedi/);assert.match(h.html(),/Vendredi est indisponible/);
  assert.equal(h.state.decision.options[0].value,'Ask about Saturday');
  await h.choose(h.state.decision.options[0].value);
  assert.equal(JSON.parse(h.requests[0].options.body).chosen_action,'Ask about Saturday');
});


test('setup lists exactly the five requested languages and uses the Brazilian locale',async()=>{
  const h=await harness();h.state.page='setup';h.render();
  const labels=[...h.html().matchAll(/data-action="language" data-value="([^"]+)"/g)].map(match=>match[1]);
  assert.deepEqual(labels,['Portuguese (Brazil)','Spanish','French','German','English']);
  assert.match(h.html(),/Português \(Brasil\)/);
  h.state.language='Portuguese (Brazil)';await h.start();
  assert.match(h.html(),/lang="pt-br"/);
  assert.match(h.state.draft.translation,/Meu hóspede/);
});

test('new workspace starts with a real task and no preset hotel or price',async()=>{
  const h=await harness([],{production:true});
  assert.equal(h.state.source,'live');assert.equal(h.state.goal,'');assert.equal(h.state.spendingLimit,'');
  h.state.page='setup';h.render();
  assert.match(h.html(),/id="business-name"/);assert.match(h.html(),/id="spending-limit"/);
  assert.doesNotMatch(h.html(),/Grandview Harbour|Spending ceiling \$20|Guardrails|Presenting to judges/);
  await h.start();assert.equal(h.requests.length,0);assert.ok(h.state.error);
});

test('general business preparation sends the explicit zero-dollar preference',async()=>{
  const h=await harness([{body:{session_id:'support',extracted_rules:{target_business:'Parcel support',opening_phrase:'Could you check my delivery?'}}},{body:{status:'AWAITING_APPROVAL',draft_id:'d1',staged_draft:{english_response:'Could you check my delivery?',translated_response:'¿Puede comprobar mi entrega?'}}}],{production:true});
  Object.assign(h.state,{goal:'Ask about my late parcel',businessName:'Parcel support',spendingLimit:'0'});
  await h.start();const body=JSON.parse(h.requests[0].options.body);
  assert.equal(body.business_name,'Parcel support');assert.equal(body.spending_limit,0);
  assert.doesNotMatch(h.html(),/Rehearsal cues|Presenting to judges|Hotel \/ receptionist/);
});

test('conversation preferences are included in session preparation',async()=>{
  const h=await harness([{body:{session_id:'prefs',extracted_rules:{opening_phrase:'Hello'}}},{body:{status:'AWAITING_APPROVAL',draft_id:'d',staged_draft:{english_response:'Hello',translated_response:'Hola'}}}],{production:true});
  Object.assign(h.state,{goal:'Ask about availability',replyTone:'friendly',replyLength:'detailed',extraInstructions:'Only afternoon appointments.'});
  await h.start();
  assert.deepEqual(JSON.parse(h.requests[0].options.body).preferences,{tone:'friendly',reply_length:'detailed',additional_instructions:'Only afternoon appointments.'});
});

test('saved conversation restores its reply preferences',async()=>{
  const h=await harness([{body:{status:'IN_PROGRESS',mode:'assist',language:'English',rules:{},transcript_history:[],decision_ledger:[],preferences:{tone:'direct',reply_length:'detailed',additional_instructions:'Ask for a refund first.'}}}]);
  h.state.page='setup';h.storage.set('delegate-active-session','prefs');await h.resumeSession();
  assert.equal(h.state.replyTone,'direct');assert.equal(h.state.replyLength,'detailed');assert.equal(h.state.extraInstructions,'Ask for a refund first.');
});

test('theme honors saved choice, remembers toggles, and follows system changes in system mode',async()=>{
  const storage=new Map([['delegate-theme','light']]);let onSystemChange;
  const root={dataset:{},style:{}};
  const media={matches:true,addEventListener(name,fn){onSystemChange=fn;}};
  const window={localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},matchMedia:()=>media,addEventListener(){},dispatchEvent(){}};
  const context=vm.createContext({window,document:{documentElement:root,querySelector:()=>null},Event:class{constructor(type){this.type=type;}}});
  vm.runInContext(await readFile('frontend/theme.js','utf8'),context);
  assert.equal(root.dataset.theme,'light');
  window.DelegateTheme.toggle();assert.equal(root.dataset.theme,'dark');assert.equal(storage.get('delegate-theme'),'dark');
  window.DelegateTheme.set('system');media.matches=false;onSystemChange();assert.equal(root.dataset.theme,'light');
  media.matches=true;onSystemChange();assert.equal(root.dataset.theme,'dark');
});

test('a live voice call silences the browser voice so the agent is not heard twice',async()=>{
  const h=await receptionistHarness();
  h.element('audio-toggle').listeners.click();
  await h.element('voice-call').listeners.click();
  assert.equal(h.element('voice-call').textContent,'End voice call');
  const count=h.spoken.length;
  h.display({status:'ready',control_revision:0,transcript:[{speaker:'agent',text:'Hello there'}]});
  await h.settle();
  assert.equal(h.spoken.length,count);
});

test('ending the live voice call restores the browser voice fallback',async()=>{
  const h=await receptionistHarness();
  h.element('audio-toggle').listeners.click();
  await h.element('voice-call').listeners.click();
  await h.element('voice-call').listeners.click();
  assert.equal(h.element('voice-call').textContent,'Start live voice call');
  h.display({status:'ready',control_revision:0,transcript:[{speaker:'agent',text:'Hello there'}]});
  await h.settle();
  assert.equal(h.spoken.at(-1),'Hello there');
});

test('the setup screen offers a female and male agent voice',async()=>{
  const h=await harness();
  h.state.page='setup';h.state.language='English';h.render();
  const html=h.html();
  assert.match(html,/Agent voice/);
  assert.match(html,/id="agent-voice"/);
  assert.match(html,/<option value="female"[^>]*>Female</);
  assert.match(html,/<option value="male"[^>]*>Male</);
});

test('the dropdown shows which voice is currently selected',async()=>{
  const h=await harness();
  h.state.page='setup';h.state.language='English';h.state.voiceChoice='male';h.render();
  assert.match(h.html(),/<option value="male" selected>/);
  h.state.voiceChoice='female';h.render();
  assert.match(h.html(),/<option value="female" selected>/);
});

test('the voice choice is sent with the call so both sides match',async()=>{
  const h=await harness([{body:{session_id:'call-1',extracted_rules:{target_business:'Hotel',opening_phrase:'Hello'}}},
                         {body:{status:'AWAITING_APPROVAL',draft_id:'d1',staged_draft:{english_response:'Hello',translated_response:'Hola'}}}]);
  h.state.source='live';h.state.goal='Early check-in, max $20';h.state.voiceChoice='male';
  await h.start();
  const created=JSON.parse(h.requests.find(row=>row.url.endsWith('session/create')).options.body);
  assert.equal(created.voice,'male');
});

test('every screen speaks through the natural voice, not the browser',async()=>{
  const h=await harness();
  h.state.source='demo';h.state.session=null;
  assert.equal(h.speechEndpoint(),'demo/speech','guided demo');
  // The setup screen has no call yet. It still must not fall back to the
  // browser voice -- Check audio lives there, and that is what it demonstrates.
  h.state.source='live';h.state.session=null;
  assert.equal(h.speechEndpoint(),'demo/speech','setup screen, before a call');
  h.state.source='live';h.state.session='call-1';
  assert.equal(h.speechEndpoint(),'session/call-1/speech','during a call');
});

test('the guided demo still works when the backend cannot be reached',async()=>{
  // Someone may open the demo with no server running. It must fall back to the
  // browser voice rather than throwing or going silent.
  const h=await harness();
  await h.start();
  await h.approve();
  assert.equal(h.state.phase,'booking');
  assert.ok(h.spoken.length>0,'the reply should still be spoken somehow');
});

test('listening is a main control, not hidden inside a panel',async()=>{
  const h=await harness();
  h.state.source='live';h.state.session='call-1';h.state.page='live';
  h.state.started=Date.now();h.state.status='IN_PROGRESS';h.render();
  const html=h.html();
  // It must be reachable without opening the collapsed manual-input panel.
  const listenBar=html.slice(html.indexOf('listen-bar'),html.indexOf('manual-input'));
  assert.ok(html.includes('listen-bar'),'the listening row should be on the page');
  assert.match(listenBar,/data-action="listen"/);
  assert.match(listenBar,/Listen uses this microphone/);
});

test('the logo animates while the microphone is open',async()=>{
  const h=await harness();
  h.state.source='live';h.state.session='call-1';h.state.page='live';
  h.state.started=Date.now();h.state.status='IN_PROGRESS';
  h.state.listening='idle';h.render();
  assert.doesNotMatch(h.html(),/brand-listening/,'idle: no listening animation');
  h.state.listening='live';h.render();
  const html=h.html();
  assert.match(html,/brand-listening/,'listening: the logo animates');
  assert.match(html,/Listening — just talk/);
  assert.match(html,/Stop listening/);
});

test('the sample conversation says why the microphone is not used, and offers the way out',async()=>{
  const h=await harness();
  h.state.source='demo';h.state.page='live';h.state.started=Date.now();h.state.status='IN_PROGRESS';h.render();
  const html=h.html();
  assert.match(html,/You are in the sample conversation/);
  assert.match(html,/microphone is not used here/);
  assert.match(html,/data-action="start-real"/);
});

test('start-real leaves the sample and returns to a blank real setup',async()=>{
  const h=await harness();
  h.state.source='demo';h.state.page='live';h.state.started=Date.now();
  h.state.transcript=[{speaker:'agent',text:'demo line'}];
  h.restart();h.state.source='live';
  assert.equal(h.state.source,'live');
  assert.equal(h.state.page,'setup');
  assert.equal(h.state.transcript.length,0);
  assert.equal(h.state.session,null);
});

test('speech heard while a decision is pending is held, not rejected',async()=>{
  // The microphone stays open, so the other person keeps talking whether or
  // not the human is ready. Their words must survive and must not look like
  // an error.
  const h=await harness();
  h.state.source='live';h.state.session='call-1';h.state.page='live';
  h.state.started=Date.now();h.state.status='AWAITING_APPROVAL';h.state.listening='live';
  await h.heardFromRepresentative('and there is a ninety dollar fee');
  assert.equal(h.requests.length,0,'nothing should be sent while a decision waits');
  assert.equal(h.state.error,'','holding is normal, not an error');
  assert.match(h.state.callerText,/ninety dollar fee/,'the words must be kept');
  assert.match(h.html(),/held until you approve/);
});

test('held speech is sent once the decision is resolved',async()=>{
  const h=await harness([{body:{status:'IN_PROGRESS',decision:{is_dealbreaker:false,draft_response:'Understood.',translated_response:'Entendido.'}}},{body:{decision_ledger:[]}}]);
  h.state.source='live';h.state.session='call-1';h.state.page='live';
  h.state.started=Date.now();h.state.status='AWAITING_APPROVAL';h.state.listening='live';
  await h.heardFromRepresentative('the fee is ninety dollars');
  h.state.status='IN_PROGRESS';
  await h.flushHeldSpeech();
  const sent=h.requests.find(row=>row.url.includes('evaluate-turn'));
  assert.ok(sent,'the held words should go through after approval');
  assert.match(JSON.parse(sent.options.body).caller_text,/ninety dollars/);
  assert.equal(h.state.callerText,'');
});

test('first-test onboarding explains the two screens and offers setup checks',async()=>{
  const h=await harness([], {production:true});h.state.page='setup';h.render();
  assert.match(h.html(),/Two people\. Two screens/);
  assert.match(h.html(),/Use a Portuguese test task/);
  assert.match(h.html(),/Check setup/);
});

test('participant holds speech while approval is pending and drains after approval',async()=>{
  const h=await receptionistHarness();
  h.display({status:'waiting',transcript:[]});
  await h.sendHeard('Thirty dollars');
  assert.match(h.element('room-status').textContent,/1 spoken reply is waiting/);
  h.display({status:'ready',transcript:[]});await h.settle();
  assert.doesNotMatch(h.element('room-status').textContent,/waiting for the current approval/);
  assert.equal(h.element('participant-text').value,'');
});

test('participant hides unavailable live voice and disables unconfigured transcription',async()=>{
  const h=await receptionistHarness();
  h.display({status:'ready',transcript:[],voice_live:false,live_listening_configured:false});
  assert.equal(h.element('voice-call').hidden,true);
  assert.equal(h.element('listen').disabled,true);
  assert.equal(h.element('participant-send').disabled,false);
});

test('speech that finishes loading after Stop never plays',async()=>{
  let finishFetch, plays=0;
  const context=vm.createContext({AbortController,setTimeout,clearTimeout,window:{speechSynthesis:{cancel(){}}},fetch:()=>new Promise(resolve=>finishFetch=resolve),Audio:class{play(){plays++;return Promise.resolve();}},URL,console});
  const mod=new vm.SourceTextModule(await readFile('frontend/voice_playback.js','utf8'),{context});
  await mod.link(()=>{});await mod.evaluate();
  const pending=mod.namespace.speakReply('session/test/speech','Do not play after stop');
  mod.namespace.stopSpeaking();
  finishFetch({ok:true,blob:async()=>new Blob(['x'.repeat(600)])});
  assert.equal(await pending,'none');assert.equal(plays,0);
});

test('live transcription sends a finalized turn once even if formatting repeats it',async()=>{
  const events=[], handlers={};let stopped=false;
  class Socket {
    constructor(){this.readyState=1;}
    static OPEN=1;
    addEventListener(name,fn){handlers[name]=fn;}
    send(){}
    close(){}
  }
  const context=vm.createContext({window:{AudioContext:class{constructor(){this.sampleRate=16000;}resume(){return Promise.resolve();}close(){return Promise.resolve();}}},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){stopped=true;}}]})}},WebSocket:Socket,encodeURIComponent,Number,JSON});
  const mod=new vm.SourceTextModule(await readFile('frontend/live_listen.js','utf8'),{context});
  await mod.link(()=>{});await mod.evaluate();
  const stop=await mod.namespace.startListening({websocket_url:'wss://example.test/?rate=16000',token:'test'},event=>events.push(event));
  const emit=message=>handlers.message({data:JSON.stringify({type:'Turn',...message})});
  emit({turn_order:0,end_of_turn:false,transcript:'thirty'});
  emit({turn_order:0,end_of_turn:true,transcript:'thirty dollars'});
  emit({turn_order:0,end_of_turn:true,turn_is_formatted:true,transcript:'Thirty dollars.'});
  emit({turn_order:1,end_of_turn:true,transcript:'Yes please.'});
  assert.equal(events.filter(event=>event.type==='final').length,2);
  stop();assert.equal(stopped,true);
});


test('starting another task preserves a shortcut to the saved conversation',async()=>{
  const h=await harness([], {production:true});
  Object.assign(h.state,{source:'live',session:'saved-call',rules:{target_business:'Saved Hotel'},started:Date.now()});
  h.rememberSession();h.restart();
  assert.equal(h.storage.get('delegate-active-session'),undefined);
  assert.equal(JSON.parse(h.localData.get('delegate-recent-conversations'))[0].id,'saved-call');
  assert.match(h.html(),/Saved Hotel/);
  assert.match(h.html(),/data-action="resume-call"/);
});
