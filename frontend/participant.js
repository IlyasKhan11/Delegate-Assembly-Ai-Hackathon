import {request} from './api.js';
import {dictate} from './speech.js';
import {canPlaceVoiceCall, startVoiceCall} from './voice_call.js';
import {speakReply, stopSpeaking} from './voice_playback.js';
import {canListenLive, startListening} from './live_listen.js';
const token=location.pathname.split('/').filter(Boolean).at(-1);
const $=id=>document.getElementById(id);
const escape=text=>String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let hangUpVoiceCall=null, voiceState='idle';
let stopListening=null, listenState='idle', heardSoFar='';
let heldSpeech=[], speechFailed=false;
// Agent lines are read aloud one at a time, in the natural backend voice.
let speechQueue=Promise.resolve(), speechGeneration=0;
function cancelSpeech(){speechGeneration+=1;stopSpeaking();}
function cancelListening(){stopListening?.();}
function playAgentLines(lines){
  const generation=speechGeneration;
  for(const text of lines){
    speechQueue=speechQueue.then(()=>generation===speechGeneration
      ?speakReply(`participant/${token}/speech`,text,{voice:snapshot?.voice_choice,onError:error})
      :undefined);
  }
}
let busy=false, polling=false, ended=false, recording=false, lastSignature='', snapshot=null, seenTurns=0, audioEnabled=false, cancelDictation=null, operation=0, disconnected=false;
function error(message){$('room-error').textContent=message;$('room-error').hidden=!message;}
function controls(){
  const locked=disconnected||ended||busy||!snapshot||snapshot.status!=='ready';
  $('participant-text').disabled=locked||recording;
  $('participant-send').disabled=locked||recording;
  $('participant-send').textContent=busy?'Delegate is thinking…':'Send to Delegate';
  const listenButton=$('listen');
  if(listenButton){
    listenButton.textContent=listenState==='idle'?'Start talking':listenState==='connecting'?'Connecting…':'Stop talking';
    listenButton.disabled=disconnected||ended||!snapshot||!snapshot.live_listening_configured||listenState==='connecting'||voiceState!=='idle';
    listenButton.title=snapshot?.live_listening_configured?'Use your microphone to reply':'Live transcription is not configured. Type or dictate a reply instead.';
    listenButton.setAttribute('aria-pressed',String(listenState==='live'));
  }
  const voiceButton=$('voice-call');
  if(voiceButton){
    voiceButton.textContent=voiceState==='idle'?'Start live voice call':voiceState==='connecting'?'Connecting…':'End voice call';
    voiceButton.hidden=!snapshot?.voice_live&&voiceState==='idle';
    voiceButton.disabled=disconnected||ended||voiceState==='connecting';
    voiceButton.setAttribute('aria-pressed',String(voiceState==='live'));
  }
  $('dictation').disabled=(locked||listenState!=='idle'||voiceState!=='idle')&&!recording;
  $('dictation').textContent=recording?'Stop microphone':'Dictate reply';
  $('room-status').textContent=heldSpeech.length?`${heldSpeech.length} spoken ${heldSpeech.length===1?'reply is':'replies are'} waiting for the current approval. Nothing is lost.`:listenState==='live'?'Listening… just speak, and your words are sent when you pause.':disconnected?'Reconnecting… keep your reply here until the connection returns.':ended?'Call ended. Ask the other participant for a new link to start another.':busy?'Sending your response. The AI is evaluating it…':snapshot?.status==='ready'?'Your turn — respond to the conversation.':'The other participant is reviewing their reply. Approved words will appear here.';
}
function display(data){
  const prior=snapshot;
  if(data.paused||data.status==='ended'||(prior&&data.control_revision!==prior.control_revision)){cancelSpeech();cancelDictation?.();}
  disconnected=false;snapshot=data;ended=data.status==='ended';
  if(ended||data.paused){cancelListening();cancelDictation?.();heldSpeech=[];}
  if(ended){hangUpVoiceCall?.();}
  if(data.voice_live===false&&voiceState!=='idle'){hangUpVoiceCall?.();}
  $('business-name').textContent=data.business||'Conversation';
  const signature=JSON.stringify(data.transcript);
  if(signature!==lastSignature){
    const added=data.transcript.slice(seenTurns);
    if(prior&&!data.paused&&!ended&&audioEnabled&&voiceState==='idle'){
      playAgentLines(added.filter(row=>row.speaker==='agent').map(row=>row.text));
    }
    seenTurns=data.transcript.length;lastSignature=signature;
    $('participant-transcript').innerHTML=data.transcript.map(row=>`<article class="participant-turn ${row.speaker}"><div class="eyebrow">${row.speaker==='agent'?'Delegate':'You'} · ${escape(row.time||'')}</div><p>${escape(row.text)}</p></article>`).join('')||'<p class="empty-state">The other participant is preparing their opening. It will appear here after approval.</p>';
    $('participant-transcript').scrollTop=$('participant-transcript').scrollHeight;
  }
  controls();
  if(!busy&&!ended&&snapshot.status==='ready'&&heldSpeech.length&&!speechFailed){
    const next=heldSpeech.shift();sendHeard(next);
  }
}
async function poll(){
  if(polling||ended)return;polling=true;const revision=operation;
  try{const data=await request(`participant/${token}`,undefined,'GET');if(data.paused||data.status==='ended')cancelSpeech();if(!busy&&revision===operation){display(data);if($('room-error').textContent.startsWith('Connection lost'))error('');}}
  catch(e){disconnected=true;cancelSpeech();error('Connection lost. '+e.message);if([404,410].includes(e.status)){ended=true;cancelListening();cancelDictation?.();hangUpVoiceCall?.();heldSpeech=[];error(e.message);}controls();}
  finally{polling=false;}
}
$('participant-form').addEventListener('submit',async event=>{
  event.preventDefault();if(disconnected||ended||busy||snapshot?.status!=='ready')return;
  const text=$('participant-text').value.trim();if(!text)return;
  cancelSpeech();busy=true;operation++;error('');controls();
  try{const data=await request(`participant/${token}/turn`,{caller_text:text});$('participant-text').value='';speechFailed=false;display(data);}
  catch(e){error(e.message);}finally{busy=false;controls();}
});
async function toggleVoiceCall(){
  if(hangUpVoiceCall){hangUpVoiceCall();return;}
  if(!canPlaceVoiceCall()){error('This browser cannot place a live voice call. Type your replies instead.');return;}
  cancelListening();cancelDictation?.();heldSpeech=[];
  error('');voiceState='connecting';controls();
  let credentials;
  try{credentials=await request(`participant/${token}/voice`,undefined,'GET');}
  catch(e){voiceState='idle';error(e.message);controls();return;}
  if(!credentials.voice_live){voiceState='idle';error(credentials.hint||'The live voice call has not been started yet.');controls();return;}
  cancelSpeech();
  hangUpVoiceCall=await startVoiceCall(credentials,event=>{
    if(event.type==='ready'){voiceState='live';error('');}
    else if(event.type==='error'){error(event.message);}
    else if(event.type==='ended'){hangUpVoiceCall=null;voiceState='idle';}
    controls();
  });
  if(ended||disconnected)hangUpVoiceCall?.();
  if(!hangUpVoiceCall)voiceState='idle';
  controls();
}
async function toggleLiveListening(){
  if(stopListening){stopListening();return;}
  if(!canListenLive()){error('This browser cannot listen live. Type your reply instead.');return;}
  if(!snapshot?.live_listening_configured)return;
  cancelDictation?.();cancelSpeech();
  error('');listenState='connecting';heardSoFar='';controls();
  let credentials;
  try{credentials=await request(`participant/${token}/listen`,undefined,'GET');}
  catch(e){listenState='idle';error(e.message);controls();return;}
  stopListening=await startListening(credentials,event=>{
    if(event.type==='ready'){listenState='live';error('');}
    else if(event.type==='partial'){heardSoFar=event.text;$('participant-text').value=event.text;}
    else if(event.type==='final'){heardSoFar='';sendHeard(event.text);}
    else if(event.type==='error'){error(event.message);}
    else if(event.type==='ended'){stopListening=null;listenState='idle';heardSoFar='';}
    controls();
  });
  if(ended||disconnected||snapshot?.paused)cancelListening();
  if(!stopListening)listenState='idle';
  controls();
}
// A finished sentence goes straight to Delegate, so the conversation flows
// without anyone pressing send.
async function sendHeard(text){
  if(!text.trim()||ended||disconnected)return;
  if(busy||snapshot?.status!=='ready'||speechFailed){
    if(heldSpeech.length>=20){cancelListening();error('There are too many waiting replies. Finish the current decision before continuing.');$('participant-text').value=text;return;}
    heldSpeech.push(text);controls();return;
  }
  cancelSpeech();busy=true;operation++;controls();
  try{const data=await request(`participant/${token}/turn`,{caller_text:text.trim()});$('participant-text').value='';display(data);}
  catch(e){speechFailed=true;error(e.message+' Your reply is kept below; review it and press Send to retry.');$('participant-text').value=text;}
  finally{busy=false;controls();}
}
$('listen').addEventListener('click',toggleLiveListening);
$('voice-call').addEventListener('click',toggleVoiceCall);
$('dictation').addEventListener('click',()=>{
  if(recording){cancelDictation?.();return;}
  cancelSpeech();recording=true;error('');controls();
  cancelDictation=dictate({onText:text=>{$('participant-text').value=text;},onError:error,onEnd:()=>{recording=false;cancelDictation=null;controls();}});
});
$('audio-toggle').addEventListener('click',()=>{
  audioEnabled=!audioEnabled;
  $('audio-toggle').textContent=audioEnabled?'Mute agent audio here':'Enable agent audio here';
  $('audio-toggle').setAttribute('aria-pressed',String(audioEnabled));
  if(!audioEnabled){cancelSpeech();return;}
  // Replay a real line rather than a canned phrase: only words already in
  // the conversation can be spoken, and it shows the actual agent voice.
  const lastAgentLine=[...(snapshot?.transcript||[])].reverse().find(row=>row.speaker==='agent');
  if(lastAgentLine)playAgentLines([lastAgentLine.text]);
  else $('room-status').textContent='Audio is on. You will hear the agent when it next speaks.';
});
window.addEventListener('beforeunload',()=>{cancelDictation?.();hangUpVoiceCall?.();cancelListening();cancelSpeech();});
setInterval(poll,1500);poll();

$('theme-toggle').addEventListener('click',()=>window.DelegateTheme?.toggle());
