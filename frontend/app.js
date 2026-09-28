import {SAMPLE_PROMPT, LANGUAGES, phrase, initialTranscript, demoSummary} from './demo.js';
import {request} from './api.js';
import {canDictate, dictate} from './speech.js';
import {speakReply, prepareSpeech, stopSpeaking} from './voice_playback.js';
import {canListenLive, startListening} from './live_listen.js';
import {translate, optionLabel, decisionOptions} from './i18n.js';

const root = document.querySelector('#app');
const modal = document.querySelector('#modal');
const state = {
  page: ['/app','/demo'].includes(location.pathname) || location.hash.startsWith('#demo') ? 'setup' : 'landing',
  mode: 'assist', language: 'English', source: 'live', goal: '', businessName: '', spendingLimit: '', participantConnected: false,
  session: null, status: 'READY_TO_START', transcript: [], ledger: [], draft: null,
  decision: null, summary: null, busy: false, error: '', phase: 'price', price: 0,
  custom: '', customDirty: false, interrupted: false, recording: false, rules: null, started: null,
  pilotProtected: false, participantExpires: null, liveListeningConfigured: null, participantPath: null, audioConfigured: false, callerText: '', lastSnapshot: '', speechError: '',
  connectionMessage: '', sessionGone: false, listening: 'idle', audioEnabled: true, voiceChoice: 'female', voiceLive: false, voiceConfigured: false, voiceHint: '', speechRate: 1, replyTone: 'professional', replyLength: 'concise', extraInstructions: '',
};
// Save presentation defaults only; private instructions remain per conversation.
try{
  const saved=JSON.parse(window.localStorage?.getItem('delegate-preferences')||'{}');
  if(['professional','friendly','direct'].includes(saved.replyTone))state.replyTone=saved.replyTone;
  if(['concise','detailed'].includes(saved.replyLength))state.replyLength=saved.replyLength;
  if([.8,1,1.2].includes(saved.speechRate))state.speechRate=saved.speechRate;
  if(typeof saved.audioEnabled==='boolean')state.audioEnabled=saved.audioEnabled;
  if(['female','male'].includes(saved.voiceChoice))state.voiceChoice=saved.voiceChoice;
}catch{}
function savePreferences(){try{window.localStorage?.setItem('delegate-preferences',JSON.stringify({replyTone:state.replyTone,replyLength:state.replyLength,speechRate:state.speechRate,audioEnabled:state.audioEnabled,voiceChoice:state.voiceChoice}));}catch{}}
let recorder, recordingStream, recordingTimeout, introTimer;
let stopListening = null;
// Speech heard while a decision is waiting for the human. The microphone
// stays open -- the other person keeps talking whether or not you are ready
// -- so their words are held and sent once the decision is resolved.
let heldSpeech = [];
let interruptPending = false, speechBlocked = false, cancelDictation = null, interruptInFlight = false;
let operationRevision = 0, polling = false;
const escape = (text = '') => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const t = (key, values={}) => translate(state.language,key,values);
const code = () => LANGUAGES.find(l => l[0] === state.language)?.[2] || 'EN';
const busy = () => state.busy ? 'disabled' : '';
const conversationLive = () => state.page==='live' && state.status!=='COMPLETED' && Boolean(state.started);
const logo = (tag = false) => `<a href="#" class="brand${conversationLive()?' brand-live':''}${state.listening==='live'?' brand-listening':''}" aria-label="Delegate home" data-action="home"><svg viewBox="0 0 40 28" fill="none" aria-hidden="true"><path d="M1 14h10l4-8 6 16 5-13 5 5h8" stroke="currentColor" stroke-width="2"/><path d="m11 14 4-8 6 16 5-13 5 5" stroke="#25ca83" stroke-width="2" stroke-linejoin="round"/></svg><span><span class="brand-name">Delegate</span>${tag ? '<span class="brand-tag">NOTHING AGREED WITHOUT YOU</span>' : ''}</span></a>`;
const button = (text, action, cls = 'btn', extra = '') => `<button class="${cls}" data-action="${action}" ${extra}>${text}</button>`;
function ceiling() {
  if (state.source === 'demo') return 20;
  if(!state.started&&state.spendingLimit!=='')return Number(state.spendingLimit);
  const rule = state.rules?.constraints?.find(r => r.operator === 'max' && /price|cost|budget|spend|fee|amount/i.test(r.parameter));
  return rule ? Number(String(rule.value).replace(/[^\d.]/g, '')) : null;
}
function landing() {
  return `<div class="landing view-enter" id="top"><header class="landing-header"><nav class="landing-container landing-nav">${logo()}<div class="nav-links"><a href="#how-it-works">How it works</a><a href="#modes">Modes</a><a href="#control-layer">Control layer</a>${button('Start a conversation','open-demo','btn btn-small')}${themeButton()}</div></nav></header>
  <main id="main"><section class="hero landing-container"><div class="live-badge"><span class="dot"></span> YOUR LANGUAGE · THEIR LANGUAGE · YOUR SAY</div><h1>Make the call<br>without speaking<br>the language.</h1><p class="hero-description">Type what you need in your own language. Invite someone to a browser conversation. Delegate helps you respond in English, with translations and replies you can review before they are spoken.</p><div class="hero-actions">${button('Try a live call','open-demo')}${button('Watch the 7s intro','intro','btn btn-outline')}</div>
  <div class="moment"><div class="eyebrow">An example of staying in control</div><div class="moment-grid"><div class="moment-quote"><div class="eyebrow">Receptionist</div><p>We can do 12 PM, but there is an early check-in fee of thirty dollars.</p><small>Podemos hacerlo a las 12 PM, pero hay un cargo de treinta dólares.</small></div><div class="moment-quote moment-decision"><div class="eyebrow">Delegate held the call</div><p>$30 is above your $20 limit. Nothing has been accepted.</p><div class="mini-actions">${button('NEGOTIATE TO $20','demo-moment','mini-pill')}${button('APPROVE ONCE','demo-moment','mini-pill')}${button('DECLINE','demo-moment','mini-pill')}</div></div></div></div></section>
  <section class="how" id="how-it-works"><div class="landing-container"><div class="eyebrow">How it works</div><h2 class="section-title">Four steps, and you hold<br>the last one.</h2><div class="steps">${[
    ['Say it your way','Type the goal in your language: early check-in at 12, up to $20 extra, do not confirm anything.'],['We turn it into a plan','Goal, spending ceiling, and approval rules become a call plan before the conversation starts.'],['We speak, you watch','Their words in English, the translation underneath, and the line we intend to say next.'],['You decide','Anything that costs money or commits you stops the call and comes to you as a card.'],
  ].map(([title, text], i) => `<article class="step"><div class="eyebrow">0${i+1}</div><h3>${title}</h3><p>${text}</p></article>`).join('')}</div>
  <div class="modes" id="modes"><article class="mode-card"><div class="eyebrow">Mode 01</div><h3>Assist</h3><p>Every single reply waits for you. Read it in both languages, edit a word, or reject it. Nothing is spoken until you say so.</p><div class="mode-best">BEST FOR FIRST-TIME USERS</div></article><article class="mode-card"><div class="eyebrow">Mode 02</div><h3>Delegate</h3><p>Routine questions are answered for you. Prices, bookings, personal details, and anything binding still stop and come back as a decision card.</p><div class="mode-best">BEST FOR LONGER CALLS</div></article></div></div></section>
  <section class="control landing-container" id="control-layer"><div class="eyebrow">The control layer</div><h2 class="section-title">The model talks. Our backend decides what it is allowed to agree to.</h2><div class="safety-grid">${[
    ['Spending ceiling','A number you set. Above it, the call pauses and nothing is accepted on your behalf.'],['Approval gates','Bookings, cancellations, and confirmations need an explicit yes from you.'],['Data withheld','Review requests for personal information before deciding what to share.'],['Interrupt any time','One button cuts the agent mid-sentence, and you can correct it while the call is open.'],
  ].map(([title,text]) => `<article class="safety-card"><i class="square"></i><h3>${title}</h3><p>${text}</p></article>`).join('')}</div><div class="pipeline">${[['Listens & speaks','AssemblyAI + browser voice'],['Understands','Managed conversational model'],['Checks the rules','Delegate backend'],['Decides','You']].map(([label,text]) => `<div><div class="eyebrow">${label}</div><b>${text}</b></div>`).join('')}</div></section></main>
  <footer class="landing-footer"><h2>Your next conversation.<br>In your own words.</h2><div class="hero-actions">${button('Start a conversation','open-demo')}<a class="btn btn-outline" href="#top">Back to top</a></div><p class="eyebrow">DELEGATE · PT-BR · ES · FR · DE · EN</p></footer></div>`;
}
function themeButton(){
  const dark=window.DelegateTheme?.current()==='dark';
  return button(`<span aria-hidden="true">${dark?'☀':'☾'}</span>`,'toggle-theme','theme-toggle',`aria-label="${escape(t('switchTheme'))}" title="${escape(t('switchTheme'))}" aria-pressed="${dark}"`);
}
function preferenceSelect(id,key,options,value,disabled=''){
  return `<div><label class="field-label" for="${id}">${escape(t(key))}</label><select class="text-field" id="${id}" ${disabled}>${options.map(([option,label])=>`<option value="${option}" ${String(value)===String(option)?'selected':''}>${escape(t(label))}</option>`).join('')}</select></div>`;
}
function extraPreferences(sample){
  const disabled=sample?'disabled':busy();
  return `<div class="form-section preference-section"><div class="field-label">${escape(t('replyStyle'))}</div><div class="preference-fields">${preferenceSelect('reply-tone','tone',[['professional','professional'],['friendly','friendly'],['direct','direct']],state.replyTone,disabled)}${preferenceSelect('reply-length','replyLength',[['concise','concise'],['detailed','detailed']],state.replyLength,disabled)}</div><p class="field-note">${escape(t(sample?'presetNote':'styleHint'))}</p><label class="field-label additional-label" for="extra-instructions">${escape(t('extraInstructions'))}<span>${escape(t('optional'))}</span></label><textarea id="extra-instructions" class="goal-input instructions-input" rows="3" maxlength="1200" placeholder="${escape(t('extraPlaceholder'))}" ${disabled}>${escape(state.extraInstructions)}</textarea></div>
  <div class="form-section preference-section"><div class="field-label">${escape(t('voiceAppearance'))}</div><label class="toggle-setting" for="auto-speak"><span><b>${escape(t('autoSpeak'))}</b><small>${escape(t('autoSpeakHelp'))}</small></span><input id="auto-speak" type="checkbox" role="switch" ${state.audioEnabled?'checked':''}/><i aria-hidden="true"></i></label><div class="preference-fields">${preferenceSelect('agent-voice','agentVoice',[['female','voiceFemale'],['male','voiceMale']],state.voiceChoice)}${preferenceSelect('speech-rate','speechSpeed',[[.8,'slower'],[1,'normal'],[1.2,'faster']],state.speechRate)}${preferenceSelect('appearance','appearance',[['system','system'],['light','light'],['dark','dark']],window.DelegateTheme?.preference()||'system')}</div></div>`;
}
function appHeader() {
  return `<header class="app-header">${logo(true)}<nav class="tabs" aria-label="Call steps">${[['setup','Setup'],['live','Live call'],['summary','Summary']].map(([page,label]) => `<button class="tab ${state.page===page?'active':''}" data-action="tab" data-value="${page}" ${state.page===page?'aria-current="step"':''} ${page==='live'&&!state.started||page==='summary'&&!state.summary||page==='setup'&&state.started&&!state.summary?'disabled':''}>${label}</button>`).join('')}</nav><div class="header-right"><span class="eyebrow">Mode</span><div class="mode-toggle" aria-label="Call mode">${['assist','delegate'].map(mode => `<button class="${state.mode===mode?'active':''}" data-action="mode" data-value="${mode}" aria-pressed="${state.mode===mode}" ${state.started||state.busy?'disabled':''}>${mode==='assist'?'Assist':'Delegate'}</button>`).join('')}</div><div class="language-indicator">${code()} → EN</div>${button('Help','test-guide','btn btn-white btn-small')}${state.pilotProtected?button('Lock','lock-workspace','btn btn-white btn-small'):''}${themeButton()}</div></header>`;
}
function setup() {
  const sample=state.source==='demo';
  return `<div class="page-heading"><div><div class="eyebrow">${escape(t('newConversation'))}</div><h1>${escape(t('workspaceTitle'))}</h1><p>${escape(t('workspaceSubtitle'))}</p></div><span class="channel-badge"><span class="dot"></span>${escape(t('connectionType'))}</span></div>
  <div class="workspace-grid setup-layout view-enter"><section class="panel conversation-form">
  ${sample?`<div class="sample-notice"><span>Sample conversation · Grandview Harbour Hotel</span>${button(escape(t('sampleExit')),'source-live','text-button')}</div>`:''}
  <div class="form-section"><label class="field-label" for="business-name">${escape(t('businessLabel'))}<span>${escape(t('optional'))}</span></label><input id="business-name" class="text-field" maxlength="160" autocomplete="organization" placeholder="${escape(t('businessPlaceholder'))}" value="${escape(sample?'Grandview Harbour Hotel':state.businessName)}" ${sample?'disabled':busy()}/>
  <label class="field-label" for="goal">${escape(t('goalLabel'))}</label><textarea id="goal" class="goal-input" rows="5" placeholder="${escape(t('goalPlaceholder'))}" ${sample?'readonly':''} maxlength="4000" ${busy()}>${escape(sample?t('samplePrompt'):state.goal)}</textarea><p class="field-note">${escape(t('privateInstructions'))}</p></div>
  <div class="form-section"><div class="field-label">${escape(t('languageLabel'))}</div><div class="language-options" aria-label="Your language">${LANGUAGES.map(([value,label])=>`<button class="language-option ${state.language===value?'active':''}" data-action="language" data-value="${value}" aria-pressed="${state.language===value}" ${busy()}>${label}</button>`).join('')}</div></div>
  <div class="form-section"><div class="field-label">${escape(t('preferences'))}</div><div class="preference-options">${['assist','delegate'].map(mode=>`<button class="preference-option ${state.mode===mode?'selected':''}" data-action="mode" data-value="${mode}" aria-pressed="${state.mode===mode}" ${busy()}><span class="selection-dot"></span><span><b>${escape(t(mode==='assist'?'reviewEvery':'reviewImportant'))}</b><small>${escape(t(mode==='assist'?'reviewEveryHelp':'reviewImportantHelp'))}</small></span></button>`).join('')}</div><div class="budget-field"><label class="field-label" for="spending-limit">${escape(t('limitLabel'))}<span>${escape(t('optional'))}</span></label><input id="spending-limit" class="text-field" type="number" min="0" max="1000000" step="0.01" inputmode="decimal" placeholder="${escape(t('optional'))}" value="${escape(sample?'20':state.spendingLimit)}" ${sample?'disabled':busy()}/></div></div>
  ${extraPreferences(sample)}<div class="form-actions"><p>${escape(t('joinHint'))}</p>${button(escape(t(state.busy?'working':'prepareConversation')),'start','btn btn-dark',busy())}</div></section>
  <aside class="setup-sidebar">${firstTestCard()}<section class="connection-card"><div class="connection-symbol" aria-hidden="true">↔</div><h2>${escape(t('connectionTitle'))}</h2><p>${escape(t('connectionHelp'))}</p><div class="connection-path"><span>${escape(LANGUAGES.find(item=>item[0]===state.language)?.[1]||'English')}</span><i aria-hidden="true">→</i><span>English</span></div></section><section class="quiet-guidance"><h3>${escape(t('reviewTitle'))}</h3><p>${escape(t('reviewHelp'))}</p><h3>${escape(t('privacyTitle'))}</h3><p>${escape(t('privacyHelp'))}</p>${button(escape(t('speakerAction')),'test-speaker','btn btn-white btn-small')}</section><div class="sample-entry">${button(escape(t(sample?'sampleExit':'sampleAction')),sample?'source-live':'source-demo','text-button',busy())}</div></aside></div>${recentConversations()}`;
}
function transcriptHTML() {
  return state.transcript.map(row => `<div class="transcript-row"><span class="timestamp">${escape(row.time || '00:00')}</span><div class="utterance ${row.speaker}"><div class="eyebrow">${row.speaker==='caller'?escape(t('representativeName')):row.speaker==='agent'?'Delegate':'System'}</div><p>${escape(row.text)}</p>${row.translation&&state.language!=='English'?`<p class="translation" lang="${code().toLowerCase()}">${escape(row.translation)}</p>`:''}</div></div>`).join('');
}
function staging() {
  const draft=state.draft;
  const bilingual=state.language!=='English'&&draft?.translation&&!(state.source==='demo'&&draft.key==='custom');
  const preview=draft?`<div class="draft-preview"><div class="eyebrow">${escape(t('yourLanguage'))} · ${code()}</div><p lang="${code().toLowerCase()}">${escape(bilingual?draft.translation:draft.text)}</p>${bilingual?`<div class="eyebrow spoken-label">${escape(t('spokenEnglish'))}</div><p class="translation" lang="en">${escape(draft.text)}</p>`:''}${state.source==='demo'&&draft.key==='custom'?`<p class="translation">${escape(draft.translation)}</p>`:''}</div>`:`<p class="translation">${escape(t('choose'))}</p>`;
  return `<section class="panel dark-panel staging"><div class="staging-top"><div class="eyebrow">${escape(t('draftTitle'))}</div><div class="eyebrow">${state.draft?escape(t('awaiting')):''}</div></div>${preview}<div class="staging-actions">${button(escape(t('approve')),'approve','btn btn-small',!state.draft||state.busy||state.customDirty?'disabled':'')}${button(escape(t('edit')),'edit','btn btn-outline btn-small',busy())}${button(escape(t('reject')),'reject','btn btn-outline btn-small',!state.draft||state.busy?'disabled':'')}</div><label class="reply-label" for="custom-response">${escape(t('inputLabel'))}</label><div class="custom-editor"><input id="custom-response" aria-describedby="draft-hint" placeholder="${escape(t('placeholder'))}" value="${escape(state.custom)}" maxlength="2000" ${busy()}/>${button(escape(t('prepare')),'custom','btn btn-outline btn-small',!state.custom.trim()||state.busy?'disabled':'')}</div><p class="draft-hint" id="draft-hint">${escape(t(state.customDirty?'dirtyHint':'hint'))}</p></section>`;
}
function receptionistInput() {
  if(state.source!=='live'||state.status==='COMPLETED')return '';
  const locked=state.busy||['AWAITING_APPROVAL','DECISION_REQUIRED'].includes(state.status);
  return `<details class="panel manual-input" ${state.callerText||state.recording||state.listening!=='idle'?'open':''}><summary>${escape(t('manualInput'))}</summary><form id="caller-form" class="caller-form"><input id="caller-input" name="caller" aria-label="${escape(t('representativeResponse'))}" placeholder="${escape(t('representativePlaceholder'))}" value="${escape(state.callerText)}" maxlength="4000" required ${locked?'disabled':''}/><button class="btn btn-dark btn-small" ${locked?'disabled':''}>${escape(t('sendResponse'))}</button>${button(state.listening==='live'?'Stop listening':state.listening==='connecting'?'Connecting…':'Listen live','listen',`btn btn-small ${state.listening==='live'?'btn-danger':'btn-dark'}`,state.listening==='connecting'||locked?'disabled':'')}${button(state.recording?'Stop microphone':state.audioConfigured?'Use microphone':'Dictate reply','record',`btn btn-small ${state.recording?'btn-danger':'btn-white'}`,locked&&!state.recording?'disabled':'')}</form></details>`;
}

function decisions() {
  if (!state.decision) return `<section class="panel"><div class="eyebrow"><span class="dot"></span> ${state.status==='COMPLETED'?'Call complete':'Conversation ready'}</div><h2 style="font-size:20px;margin-top:12px">${state.status==='AWAITING_APPROVAL'?'Your next reply is ready.':state.status==='COMPLETED'?'The conversation has ended.':'Nothing agreed without you.'}</h2><p class="pending-note">${state.status==='AWAITING_APPROVAL'?'Review both languages, then approve or edit the draft.':'Review the transcript and pause whenever you need to change direction.'}</p></section>`;
  const quoted = state.decision.price;
  return `<section class="panel decision-panel" aria-labelledby="decision-title"><div class="eyebrow">● ${escape(t('decisionTitle'))}</div><h2 id="decision-title">${escape(state.decision.translatedReason||state.decision.reason)}</h2>${quoted?`<div class="decision-metrics"><div class="metric"><div class="eyebrow">Asked</div><b>$${quoted}</b></div><div class="metric"><div class="eyebrow">Your limit</div><b>${ceiling()!==null?'$'+ceiling():'—'}</b></div><div class="metric"><div class="eyebrow">Status</div><b>Not accepted</b></div></div>`:'<div style="height:15px"></div>'}<div class="decision-actions">${state.decision.options.map((option,i) => button(escape(option.label||optionLabel(state.language,option)),'decision',`btn ${i===0?'btn-dark':'btn-white'}`,`data-value="${escape(option.value||option)}" ${busy()}`)).join('')}${button(escape(t('custom')),'edit','btn btn-white',busy())}</div></section>`;
}
function terms() {
  const demo = state.source==='demo';
  const limit = ceiling();
  return `<section class="panel"><div class="eyebrow">Agreed terms · ${state.summary?'Final':'Live'}</div><div class="terms-list">${(demo?[
    ['Early check-in','12:00 PM · available','green-text'],
    ['Price',state.price?`$${state.price} approved`: '$30 requested — blocked',state.price?'green-text':'red-text'],
    ['Conditions','Paid at front desk',''],
    ['Confirmation',state.phase==='confirmed'?'Confirmed by you':'Nothing confirmed',''],
  ]:[['Call target',state.rules?.target_business||'Business',''],['Spending ceiling',limit!==null?'$'+limit:'Not set',''],['Confirmation',state.summary?'See call summary':'Review decisions before agreeing','']]).map(([label,value,cls])=>`<div class="term"><span>${label}</span><b class="${cls}">${escape(value)}</b></div>`).join('')}</div><div class="spend"><div class="eyebrow">${demo?'Spend approved':'Approval control'}</div><div class="progress"><i style="width:${demo&&state.price?Math.min(100,state.price/20*100):0}%"></i></div><div class="spend-labels"><span>${demo?'$'+state.price+' approved':'Human review enabled'}</span><span>${limit!==null?'limit $'+limit:'Your rules apply'}</span></div></div></section>`;
}
function listenBar(){
  if(state.status==='COMPLETED')return '';
  if(state.source==='demo')return `<section class="panel listen-bar sample-note">
    <div class="listen-copy"><div class="listen-status"><b>You are in the sample conversation.</b></div>
    <p class="listen-heard">It replays a fixed script, so your microphone is not used here. Start a real conversation to talk out loud.</p></div>
    ${button('Start a real conversation','start-real','btn btn-large btn-dark',busy())}
  </section>`;
  if(!state.session)return '';
  const live=state.listening==='live';
  const connecting=state.listening==='connecting';
  const label=live?'Stop listening':connecting?'Connecting…':'Listen';
  const holding=live&&heldSpeech.length>0;
  const hint=holding
    ? 'Still listening — held until you approve the reply below.'
    : live ? 'Listening — just talk. Each sentence is sent when you pause.'
    : connecting ? 'Turning on your microphone…'
    : state.liveListeningConfigured===false ? 'Live transcription is not configured. Your friend can type in their invitation screen.' : 'Using this computer as the representative? Listen uses this microphone. Your remote friend should use their own invitation screen.';
  const heard=live&&state.callerText?`<p class="listen-heard">\u201c${escape(state.callerText)}\u201d</p>`:'';
  return `<section class="panel listen-bar${live?' listening':''}">
    <div class="listen-copy"><div class="listen-status">${live?'<span class="listening-dot" aria-hidden="true"></span>':''}<b>${escape(hint)}</b></div>${heard}</div>
    ${button(escape(label),'listen',`btn btn-large ${live?'btn-danger':'btn-dark'}`,connecting||state.busy||state.liveListeningConfigured===false?'disabled':'')}
  </section>`;
}
function audit() {
  return `<section class="panel"><div class="eyebrow">Control layer</div>${state.source==='demo'?`<div class="audit-row"><span class="eyebrow red-text">Blocked</span><span>$30 offer paused by spend rule (ceiling $20).</span></div><div class="audit-row"><span class="eyebrow green-text">Allowed</span><span>Asking about availability and fees — information only, no commitment.</span></div><div class="audit-row"><span class="eyebrow">Tracked</span><span>Hotel confirmed a 12:00 PM slot exists. Recorded as a hotel statement, not an agreement.</span></div>`:state.ledger.slice(-3).map(row=>`<div class="audit-row"><span class="eyebrow ${row.approved?'green-text':'red-text'}">${row.approved?'Approved':'Paused'}</span><span>${escape(row.summary)}</span></div>`).join('')||'<p class="pending-note">Your decisions will appear here as the call progresses.</p>'}</section>`;
}
function live() {
  return `<div class="conversation-connection"><div><span class="dot ${state.participantConnected?'':'waiting-dot'}"></span><span>${state.source==='demo'?'Sample conversation':escape(t(state.participantConnected?'connected':'waitingRepresentative'))}</span><small>${escape(t('connectionType'))}</small></div>${state.source==='live'&&state.session?button(escape(t('inviteRepresentative')),'participant','btn btn-dark btn-small'):''}</div><div class="workspace-grid view-enter"><div class="stack"><section class="panel call-bar"><span class="dot"></span><div><b>${escape(state.source==='demo'?'Grandview Harbour Hotel — front desk':state.rules?.target_business||'Business representative')}</b><small>${state.source==='demo'?'GUIDED DEMO':'BROWSER CONVERSATION'} · <span id="call-timer">${elapsed()}</span> · ${state.mode.toUpperCase()} MODE</small></div><div class="call-controls">${state.source==='live'&&(state.voiceConfigured||state.voiceLive)?button(state.voiceLive?'End voice call':'Live voice call','voice-call',`btn btn-small ${state.voiceLive?'btn-danger':'btn-white'}`,busy()):''}${button(state.audioEnabled?'Mute audio here':'Enable audio here','toggle-audio','btn btn-white btn-small',`aria-pressed="${state.audioEnabled}"`)}${button('Correct agent','edit','btn btn-white btn-small',busy())}${button(state.interrupted?'Agent paused':'Stop / pause agent','interrupt','btn btn-danger btn-small',state.status==='COMPLETED'||state.interrupted?'disabled':'')}</div></section>${listenBar()}
  ${receptionistInput()}<section class="panel transcript" id="transcript"><div class="transcript-top"><div class="eyebrow">${state.source==='demo'?'Demo':'Live'} transcript · EN → ${code()}</div><div class="wave" aria-hidden="true"><i></i><i></i><i></i><i></i></div></div>${transcriptHTML()||'<p class="empty-state">Review your opening below. Use the invitation link to connect with the other person.</p>'}</section>${state.status!=='COMPLETED'?staging():''}
  ${button(state.busy?'Preparing summary…':state.status==='COMPLETED'?'View call summary':'End call & view summary','end','btn btn-white',busy())}</div><aside class="stack">${decisions()}${terms()}${audit()}</aside></div>`;
}

function summary() {
  const s = state.summary;
  return `<div class="workspace-grid summary-grid view-enter"><section class="panel summary-panel"><div class="eyebrow">Call ended · ${elapsed()} · ${escape(state.source==='demo'?'Grandview Harbour Hotel':state.rules?.target_business||'Business')}</div><h1>${escape(s.outcome_headline)}</h1><p>${escape(s.outcome_subtext)}</p><div class="summary-section"><div class="eyebrow">Confirmed</div><ul class="summary-list">${s.confirmed_items.map(item=>`<li>${escape(item)}</li>`).join('')||'<li>No agreements confirmed.</li>'}</ul></div>${s.unresolved_items.length?`<div class="summary-section unresolved"><div class="eyebrow">Still unresolved</div><ul class="summary-list">${s.unresolved_items.map(item=>`<li>${escape(item)}</li>`).join('')}</ul></div>`:''}</section><aside class="stack"><section class="panel ledger"><div class="eyebrow">Decision ledger</div>${state.ledger.map(row=>`<div class="ledger-entry"><div class="eyebrow">${escape(row.time||'—')}</div><b>${escape(row.action_chosen)}</b><p>${escape(row.summary)}</p></div>`).join('')||'<p class="pending-note" style="color:#b6d9c8">No decisions were approved during this call.</p>'}</section><section class="panel"><div class="eyebrow">Take it with you</div><div class="export-actions">${button(`Download summary (${code()} + EN)`,'download','btn btn-dark')}${button('Full transcript','transcript','btn btn-white')}${button('Start another call','restart','btn btn-white')}${state.source==='live'?button('Delete saved conversation','delete-call','text-button'):''}</div></section></aside></div>`;
}
// Start generating a draft's audio as soon as it appears on screen, so
// pressing Approve speaks it immediately instead of pausing to synthesize.
let preparedDraft='';
function prepareDraftAudio(){
  const text=state.draft?.text;
  if(!text||text===preparedDraft||!state.audioEnabled||state.voiceLive)return;
  preparedDraft=text;
  prepareSpeech(speechEndpoint(),text,state.voiceChoice);
}
function render() {
  prepareDraftAudio();
  const active=document.activeElement;
  const focus=active&&['goal','business-name','extra-instructions','caller-input','custom-response'].includes(active.id)?{id:active.id,start:active.selectionStart,end:active.selectionEnd}:null;
  document.title = state.page === 'landing' ? 'Delegate — Nothing agreed without you' : `Delegate — ${{setup:'Set up your call',live:'Live call',summary:'Call summary'}[state.page]}`;
  root.innerHTML = state.page==='landing' ? landing() : `<div class="workspace">${appHeader()}<main id="main">${state.error?`<div class="error" role="alert"><span>${escape(state.error)}</span>${button('×','dismiss','', 'aria-label="Dismiss error"')}</div>`:''}${state.connectionMessage?`<div class="connection-banner" role="status">${escape(state.connectionMessage)}${state.sessionGone?button('Start a new call','restart','btn btn-white btn-small'):''}</div>`:''}${state.speechError?`<div class="error" role="status">${escape(state.speechError)}</div>`:''}${state.page==='setup'?setup():state.page==='live'?live():summary()}</main><footer class="app-footer"><span>DELEGATE · NOTHING AGREED WITHOUT YOU</span><span>${state.source==='demo'?'GUIDED DEMO · SAMPLE CONVERSATION':'BROWSER CONVERSATION · ENGLISH SPEECH'}</span></footer></div>`;
  if(focus){const field=document.querySelector('#'+focus.id);if(field&&!field.disabled){field.focus?.({preventScroll:true});field.setSelectionRange?.(focus.start,focus.end);}}
}
function elapsed() {
  const seconds = state.started ? Math.max(0,Math.floor(((state.ended||Date.now())-state.started)/1000)) : 0;
  return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
}
setInterval(()=>{const timer=document.querySelector('#call-timer');if(timer) timer.textContent=elapsed();},1000);
function navigate(page) {
  state.page=page; state.error='';
  if(page!=='landing') history.replaceState(null,'','/app'); else history.replaceState(null,'','/');
  render(); window.scrollTo({top:0,behavior:'instant'});
}
function announce(message) {document.querySelector('#announcer').textContent=message;}
async function perform(fn) {
  if(state.busy) return;
  state.busy=true;operationRevision++;state.error='';render();
  try {await fn();} catch(error){if(!(state.interrupted&&error.status===409)){state.error=error.message;announce(error.message);if(error.status===401)showModal('Sign in to continue',`<p class="modal-copy">Your conversation is saved. Sign in to unlock the workspace, then return to your call.</p><a class="btn btn-dark" href="/login">Sign in</a>`);}} finally {
    state.busy=false;render();
    if(interruptPending){interruptPending=false;await interrupt();}
  }
}
// The voice lives on the call, not just this browser, so the receptionist
// hears whichever one was chosen here.
function chooseVoice(choice){
  state.voiceChoice=choice;preparedDraft='';stopSpeech();
  if(state.source==='live'&&state.session){
    request(`session/${state.session}/voice-choice`,{voice:choice}).catch(error=>{state.error=error.message;render();});
  }
}
// The guided demo has its own route: it runs with no call and no API key.
// Says out loud which voice is actually being used, so "it still sounds
// robotic" can be diagnosed in one click instead of by guesswork.
async function testSpeaker(){
  speechBlocked=false;state.speechError='';render();
  const used=await speakReply(speechEndpoint(),'Delegate is ready. You are always in control.',
    {rate:state.speechRate,voice:state.voiceChoice,onError:message=>{state.speechError=message;render();}});
  if(used==='neural')state.speechError=`Natural voice working — you just heard ${state.voiceChoice==='male'?'Andrew':'Ava'}.`;
  render();
}
function speechEndpoint(){
  // Once a call exists, its own route is used so only words already in the
  // conversation can be spoken. Before that -- the setup screen's Check audio
  // button, and the guided demo -- there is no transcript to check against, so
  // the free demo route does the speaking. Returning null here meant Check
  // audio silently used the browser's robotic voice, which is the opposite of
  // what that button is for.
  return state.source!=='demo'&&state.session?`session/${state.session}/speech`:'demo/speech';
}
function speak(text) {
  if(speechBlocked || state.voiceLive || !state.audioEnabled || !text)return;
  state.speechError='';
  speakReply(speechEndpoint(),text,{rate:state.speechRate,voice:state.voiceChoice,onError:message=>{state.speechError=message;render();}});
}
function stopSpeech(){stopSpeaking();}
function append(speaker, text, translation='') {state.transcript.push({speaker,text,translation,time:elapsed()});}
function addPhrase(speaker,key){const p=phrase(key,state.language);append(speaker,p.text,p.translation);}
function ledger(action_chosen,summary,approved=false){state.ledger.push({action_chosen,summary,approved,time:elapsed()});}
function priceDecision(){return {translatedReason:t('priceReason',{amount:'$30',limit:'$20'}),reason:'The hotel is asking for $30, which is above your $20 limit. Nothing has been accepted.',price:30,options:[{label:t('negotiate',{limit:'$20'}),value:'negotiate'},{label:t('accept',{amount:'$30'}),value:'accept'},{label:t('decline'),value:'decline'}]};}
function stageDemo(key){state.draft={...phrase(key,state.language),key};state.status='AWAITING_APPROVAL';announce('Response drafted. Review it before approving.');}
async function start() {
  speechBlocked=false;
  await perform(async()=>{
    if(state.source==='live'&&!state.goal.trim())throw new Error(t('goalPlaceholder'));
    if(state.source==='live'&&state.spendingLimit!==''&&(!Number.isFinite(Number(state.spendingLimit))||Number(state.spendingLimit)<0||Number(state.spendingLimit)>1000000))throw new Error('Enter a spending limit between 0 and 1,000,000 USD.');
    state.transcript=[];state.ledger=[];state.summary=null;state.ended=null;state.price=0;state.phase='price';state.custom='';state.customDirty=false;state.interrupted=false;state.callerText='';state.lastSnapshot='';state.draft=null;state.decision=null;
    if(state.source==='demo') {
      state.started=Date.now();state.transcript=initialTranscript(state.language);state.decision=priceDecision();
      ledger('$30 request blocked','Above your limit. Nothing accepted; call paused for you.');stageDemo('negotiate');
    } else {
      const response=await request('session/create',{raw_prompt:state.goal,user_language:state.language,voice:state.voiceChoice,mode:state.mode,business_name:state.businessName||null,spending_limit:state.spendingLimit===''?null:Number(state.spendingLimit),preferences:{tone:state.replyTone,reply_length:state.replyLength,additional_instructions:state.extraInstructions}});
      state.session=response.session_id;state.rules=response.extracted_rules;state.status='READY_TO_START';state.participantPath=response.participant_path;state.audioConfigured=Boolean(response.flags?.audio_configured);state.started=Date.now();rememberSession();navigate('live');state.custom=state.rules.opening_phrase||state.goal;state.customDirty=true;
      // Opening is staged through the same approval gate as every other response.
      const staged=await request(`session/${state.session}/user-action`,{chosen_action:'Introduce the call',custom_instruction:state.rules.opening_phrase||state.goal,response_mode:'verbatim'});
      state.draft={id:staged.draft_id,text:staged.staged_draft.english_response,translation:staged.staged_draft.translated_response};state.status=staged.status;state.custom='';state.customDirty=false;
    }
    navigate('live');announce('Call started. Your approval controls are ready.');
  });
}
async function syncLedger(){
  const snapshot=await request(`session/${state.session}/status`,undefined,'GET');
  state.ledger=snapshot.decision_ledger.map(row=>({...row,time:row.timestamp?new Date(row.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):elapsed()}));
}
async function choose(value){
  await perform(async()=>{
    state.custom='';state.customDirty=false;
    if(state.source==='demo'){stageDemo(value);return;}
    const result=await request(`session/${state.session}/user-action`,{chosen_action:value});
    state.draft={id:result.draft_id,text:result.staged_draft.english_response,translation:result.staged_draft.translated_response};state.status=result.status;
  });
}
async function approve(){
  if(!state.draft||state.customDirty)return;
  if(state.busy)return;
  speechBlocked=false;
  await perform(async()=>{
    const draft=state.draft;state.interrupted=false;
    if(state.source==='demo'){
      append('agent',draft.text,draft.translation);speak(draft.text);state.draft=null;state.decision=null;state.status='IN_PROGRESS';
      if(draft.key==='negotiate'||draft.key==='accept'){
        state.price=draft.key==='negotiate'?20:30;state.phase='booking';
        ledger(draft.key==='negotiate'?'You chose: negotiate to $20':'You approved $30 this time',draft.key==='negotiate'?'Agent countered at $20 and the hotel accepted.':'The one-time exception was explicitly approved.',true);
        addPhrase('caller',draft.key==='negotiate'?'accepted20':'accepted30');
        state.decision={translatedReason:t('demoBooking',{amount:`$${state.price}`}),reason:`The hotel can hold Friday at 12 PM for $${state.price}. Approve the booking before anything is confirmed.`,options:[{label:t('confirm'),value:'confirm'},{label:t('decline'),value:'decline'}]};
        state.status='DECISION_REQUIRED';ledger('Booking confirmation withheld','Agent asked before recording the hold.');
      }else if(draft.key==='confirm'){
        state.phase='confirmed';state.status='COMPLETED';addPhrase('caller','confirmed');ledger('You confirmed the booking',`Friday at 12:00 PM for $${state.price}, payable at the front desk.`,true);
      }else if(draft.key==='decline'){
        state.phase='declined';state.price=0;state.status='COMPLETED';addPhrase('caller','goodbye');ledger('You declined the offer','Original check-in kept; no additional fee agreed.',true);
      }else{
        ledger('You approved a custom response','The agent spoke your words. No booking was confirmed.',true);
        state.decision=state.phase==='booking'?{translatedReason:t('demoBooking',{amount:`$${state.price}`}),reason:'Your custom response was spoken. Choose whether to confirm or decline the demo booking.',options:[{label:t('confirm'),value:'confirm'},{label:t('decline'),value:'decline'}]}:priceDecision();state.status='DECISION_REQUIRED';
      }
    }else{
      const result=await request(`session/${state.session}/approve`,{draft_id:draft.id});append('agent',result.speak_text,draft.translation);speak(result.speak_text);state.status=result.session_status;state.draft=null;state.decision=null;await syncLedger();
    }
    state.custom='';state.customDirty=false;announce(state.status==='COMPLETED'?'Call complete. View your summary.':'Response approved and spoken.');
  });
}
async function custom(){
  if(!state.custom.trim())return;
  await perform(async()=>{
    if(state.source==='demo'){
      state.draft={text:state.custom.trim(),translation:'Custom demo text is spoken as written. Automatic translation is available in AI roleplay.',key:'custom'};state.status='AWAITING_APPROVAL';
    }else{
      const result=await request(`session/${state.session}/user-action`,{chosen_action:'Custom response',custom_instruction:state.custom.trim(),response_mode:'verbatim'});
      state.draft={id:result.draft_id,text:result.staged_draft.english_response,translation:result.staged_draft.translated_response};state.status=result.status;
    }
    state.customDirty=false;
  });
}
async function reject(){
  await perform(async()=>{
    if(state.source==='live'){const result=await request(`session/${state.session}/reject`);state.status=result.status;}else state.status='DECISION_REQUIRED';
    state.draft=null;state.custom='';state.customDirty=false;announce('Draft rejected. Nothing was spoken.');
  });
}
async function interrupt(){
  if(interruptInFlight||state.interrupted)return;
  speechBlocked=true;cancelDictation?.();cancelDictation=null;stopSpeech();
  if(recorder?.state==='recording'){recorder.onstop=null;recorder.stop();cleanupRecording();}
  if(state.source==='demo'&&state.busy){interruptPending=true;return;}
  if(state.recording&&recorder){recorder.onstop=null;recorder.stop();cleanupRecording();}
  interruptInFlight=true;operationRevision++;
  state.interrupted=true;state.draft=null;state.custom='';state.customDirty=false;
  state.status='DECISION_REQUIRED';
  state.decision={translatedReason:t('paused'),reason:'You stopped the agent. Nothing else will be spoken until you approve a new reply.',options:state.source==='demo'?(state.phase==='booking'?[{label:t('confirm'),value:'confirm'},{label:t('decline'),value:'decline'}]:priceDecision().options):['Ask the receptionist to wait','Decline and end the call']};
  render();
  try{
    if(state.source==='live')await request(`session/${state.session}/interrupt`);
    append('system','Agent interrupted. Waiting for your correction.');
    announce('Agent stopped. You are in control.');
  }catch(error){state.error='Audio stopped on this screen, but the server pause was not confirmed. Reconnect and pause again. '+error.message;state.interrupted=false;}
  finally{interruptInFlight=false;render();}
}

async function processTurn(result, callerText){
  const d=result.decision;state.status=result.status;append('caller',callerText,d.translated_caller_text);
  if(d.is_dealbreaker){
    state.decision={reason:d.violation_reason,translatedReason:d.translated_violation_reason,price:d.violation_parameter==='price'?Math.max(0,...((d.violation_reason||'').match(/\$(\d+(?:\.\d+)?)/g)||[]).map(x=>Number(x.slice(1)))):null,options:decisionOptions(d,state.language)};
    if(d.immediate_stalling_phrase){append('agent',d.immediate_stalling_phrase);speak(d.immediate_stalling_phrase);}
    append('system',d.violation_reason,d.translated_violation_reason);state.draft=null;announce('Decision required. The call is paused.');
  }else if(result.status==='AWAITING_APPROVAL'){
    state.draft={id:result.draft_id,text:d.draft_response,translation:d.translated_response};state.decision=null;
  }else{
    if(d.draft_response){append('agent',d.draft_response,d.translated_response);speak(d.draft_response);}state.decision=null;state.draft=null;
  }
  await syncLedger();
}
async function sendTurn(text){await perform(async()=>{if(state.speaking)stopSpeech();const result=await request(`session/${state.session}/evaluate-turn`,{caller_text:text});await processTurn(result,text);state.callerText='';});}
function cleanupRecording(){clearTimeout(recordingTimeout);recordingStream?.getTracks().forEach(t=>t.stop());state.recording=false;}
async function toggleLiveListening(){
  if(stopListening){stopListening();return;}
  if(!canListenLive()){state.error='This browser cannot listen live. Use the microphone button or type instead.';render();return;}
  const listeningSession=state.session;
  state.listening='connecting';state.error='';render();
  let credentials;
  try{credentials=await request(`session/${state.session}/listen`,undefined,'GET');}
  catch(error){state.listening='idle';state.error=error.message;render();return;}
  stopListening=await startListening(credentials,event=>{
    if(event.type==='ready'){state.listening='live';state.error='';}
    else if(event.type==='partial'){state.callerText=event.text;}
    else if(event.type==='final'){state.callerText='';heardFromRepresentative(event.text);}
    else if(event.type==='error'){state.error=event.message;}
    else if(event.type==='ended'){stopListening=null;state.listening='idle';}
    render();
  });
  if(state.session!==listeningSession||state.status==='COMPLETED'||state.page!=='live')stopListening?.();
  if(!stopListening)state.listening='idle';
  render();
}
// A finished sentence goes through the same evaluation as a typed one, so the
// guardrails and the approval gate behave identically.
async function heardFromRepresentative(text){
  const spoken=text.trim();
  if(!spoken||state.status==='COMPLETED')return;
  if(heldSpeech.length>=20){stopListening?.();state.error='Finish the current decision before recording more replies.';render();return;}
  if(decisionPending()||state.busy){
    // Not an error: the backend refuses a new turn until the pending reply is
    // resolved, so hold the words rather than losing them or alarming anyone.
    heldSpeech.push(spoken);
    state.callerText=heldSpeech.join(' ');
    render();
    return;
  }
  await perform(async()=>{
    const result=await request(`session/${state.session}/evaluate-turn`,{caller_text:spoken});
    await processTurn(result,spoken);
  });
}
function decisionPending(){return ['AWAITING_APPROVAL','DECISION_REQUIRED'].includes(state.status);}
// Once the human has decided, whatever was said meanwhile goes through as one turn.
async function flushHeldSpeech(){
  if(!heldSpeech.length||state.busy||decisionPending())return;
  const spoken=heldSpeech.join(' ');
  heldSpeech=[];state.callerText='';
  await heardFromRepresentative(spoken);
}
async function record(){
  if(state.recording){if(cancelDictation){cancelDictation();cancelDictation=null;}else recorder?.stop();return;}
  if(!state.audioConfigured){
    state.recording=true;render();
    cancelDictation=dictate({onText:text=>{state.callerText=text;},onError:message=>{state.error=message;},onEnd:()=>{state.recording=false;cancelDictation=null;render();}});
    return;
  }
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){state.error='Microphone recording requires localhost or HTTPS and a supported browser. You can type responses instead.';render();return;}
  try{
    recordingStream=await navigator.mediaDevices.getUserMedia({audio:true});
    recorder=new MediaRecorder(recordingStream);const chunks=[];
    recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    recorder.onstop=async()=>{cleanupRecording();const blob=new Blob(chunks,{type:recorder.mimeType});const form=new FormData();form.append('file',blob,recorder.mimeType.includes('mp4')?'recording.mp4':'recording.webm');await perform(async()=>{const result=await request(`session/${state.session}/audio-turn`,form);await processTurn(result,result.transcribed_text);});};
    recorder.onerror=()=>{cleanupRecording();state.error='Recording failed. Please try again or type your response.';render();};
    recorder.start();state.recording=true;recordingTimeout=setTimeout(()=>{if(recorder.state==='recording')recorder.stop();},60000);render();
  }catch(error){cleanupRecording();state.error=error.name==='NotAllowedError'?'Microphone permission was denied. Allow it in your browser, or type the receptionist’s response.':'No microphone is available. You can type the receptionist’s response.';render();}
}
async function end(){
  stopListening?.();heldSpeech=[];stopSpeech();
  cancelDictation?.();cancelDictation=null;
  if(state.recording&&recorder){recorder.onstop=null;recorder.stop();cleanupRecording();}
  await perform(async()=>{
    if(state.source==='demo')state.summary=demoSummary(state.phase,state.price);
    else {if(state.voiceLive){await request(`session/${state.session}/voice/stop`);state.voiceLive=false;}const result=await request(`session/${state.session}/complete`);state.summary=result.summary;await syncLedger();}
    state.status='COMPLETED';state.ended=Date.now();state.draft=null;state.decision=null;rememberSession();navigate('summary');
  });
}
function showModal(title,body){modal.innerHTML=`<div class="modal-head"><h2>${title}</h2><button data-action="close-modal" aria-label="Close dialog">×</button></div>${body}`;modal.showModal();}
function intro(){
  let step=0;
  const slides=[['Say it your way.','“Ask for an early check-in. Up to $20 extra. Don’t confirm without me.”'],['We speak. You watch.','Every word in English, with a translation in your language.'],['Your limit. Your decision.','The hotel asks for $30. Delegate pauses. You choose what happens next.']];
  const update=()=>{const [title,text]=slides[step];modal.innerHTML=`<div class="modal-head"><div class="eyebrow">DELEGATE · THE 7-SECOND INTRO</div><button data-action="close-modal" aria-label="Close dialog">×</button></div><div class="eyebrow">0${step+1} / 03</div><h2 style="font-size:35px;margin:16px 0">${title}</h2><p class="modal-copy">${text}</p><div class="progress" style="margin-top:25px"><i style="width:${(step+1)/3*100}%"></i></div>${step===2?button('Try it yourself','intro-demo','btn','style="margin-top:15px"'):''}`;};
  update();modal.showModal();introTimer=setInterval(()=>{step++;if(step>=3){clearInterval(introTimer);return;}update();},2300);
}
function exportSummary(){
  const s=state.summary;
  const body=[`DELEGATE — CALL SUMMARY (${state.source==='demo'?'SIMULATED DEMO':'AI ROLEPLAY'})`,'',s.outcome_headline,s.outcome_subtext,'','CONFIRMED',...s.confirmed_items.map(x=>'• '+x),'','UNRESOLVED',...s.unresolved_items.map(x=>'• '+x),'','DECISION LEDGER',...state.ledger.map(x=>`${x.time} — ${x.action_chosen}: ${x.summary}`),'',`BILINGUAL TRANSCRIPT — EN / ${code()}`,...state.transcript.flatMap(x=>[`${x.time} ${x.speaker.toUpperCase()}: ${x.text}`,x.translation||'',''])].join('\n');
  const url=URL.createObjectURL(new Blob([body],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`delegate-summary-${code().toLowerCase()}-en.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);announce('Summary and bilingual transcript downloaded.');
}
function restart(){forgetSession();stopListening?.();heldSpeech=[];cancelDictation?.();cancelDictation=null;stopSpeech();Object.assign(state,{page:'setup',session:null,status:'READY_TO_START',transcript:[],ledger:[],draft:null,decision:null,summary:null,started:null,ended:null,error:'',custom:'',customDirty:false,interrupted:false,participantPath:null,participantExpires:null,voiceLive:false,lastSnapshot:'',callerText:'',speechError:'',connectionMessage:'',sessionGone:false,price:0,phase:'price',rules:null,participantConnected:false,extraInstructions:''});navigate('setup');}
root.addEventListener('click',async event=>{
  const target=event.target.closest('[data-action]');if(!target||target.disabled)return;
  const action=target.dataset.action;const value=target.dataset.value;
  if(target.tagName==='A')event.preventDefault();
  if(action==='home'){
    if(state.started&&!state.summary){showModal('Leave this call?',`<p class="modal-copy">End the call and save your summary before returning home.</p><div class="hero-actions">${button('End & save summary','modal-end')}${button('Keep calling','close-modal','btn btn-white')}</div>`);return;}
    navigate('landing');
  }else if(action==='open-demo'){if(state.started)navigate(state.summary?'summary':'live');else navigate('setup');}
  else if(action==='demo-moment'){if(state.started)navigate(state.summary?'summary':'live');else{state.source='demo';await start();}}
  else if(action==='intro')intro();
  else if(action==='test-guide')testGuide();
  else if(action==='lock-workspace'){stopSpeech();stopListening?.();await request('access/logout');location.href='/login';}
  else if(action==='check-setup')await checkSetup();
  else if(action==='test-template'){state.language='Portuguese (Brazil)';state.goal='Quero fazer check-in no hotel ao meio-dia. Posso pagar no máximo 20 dólares. Peça minha aprovação antes de confirmar a reserva.';state.businessName='Hotel reception';state.spendingLimit='20';state.mode='assist';state.source='live';render();}
  else if(action==='delete-call')confirmDelete();
  else if(action==='resume-call'){try{window.sessionStorage?.setItem('delegate-active-session',value);}catch{}await resumeSession();}
  else if(action==='tab'){if(value==='setup'&&state.started)restart();else navigate(value);}
  else if(action==='mode'){state.mode=value;render();}
  else if(action==='language'){state.language=value;render();}
  else if(action==='source-demo'){state.source='demo';render();}
  else if(action==='source-live'){state.source='live';render();}
  else if(action==='start-real'){restart();state.source='live';render();}
  else if(action==='start')await start();
  else if(action==='decision')await choose(value);
  else if(action==='approve'){await approve();await flushHeldSpeech();}
  else if(action==='reject'){await reject();await flushHeldSpeech();}
  else if(action==='interrupt')await interrupt();
  else if(action==='voice-call')await toggleVoiceCall();
  else if(action==='toggle-audio'){state.audioEnabled=!state.audioEnabled;if(!state.audioEnabled)stopSpeech();savePreferences();render();}
  else if(action==='toggle-theme'){window.DelegateTheme?.toggle();render();}
  else if(action==='custom')await custom();
  else if(action==='edit'){
    editReply();
  }else if(action==='participant')openParticipant();
  else if(action==='presenter-guide')presenterGuide();
  else if(action==='test-speaker')await testSpeaker();
  else if(action==='caller-cue'){state.callerText=value;render();document.querySelector('#caller-input')?.focus();}
  else if(action==='listen')await toggleLiveListening();
  else if(action==='record')await record();
  else if(action==='end')await end();
  else if(action==='download')exportSummary();
  else if(action==='transcript')showModal('Full call transcript',transcriptHTML());
  else if(action==='restart')restart();
  else if(action==='dismiss'){state.error='';render();}
});
async function toggleVoiceCall() {
  if(!state.session)return;
  if(state.voiceLive){
    await perform(async()=>{await request(`session/${state.session}/voice/stop`);state.voiceLive=false;});
    return;
  }
  await perform(async()=>{
    const result=await request(`session/${state.session}/voice/start`);
    state.voiceLive=true;state.status=result.status;stopSpeech();
    showModal('Live voice call ready',`<p class="modal-copy">The agent will speak in the <b>${escape(result.voice)}</b> voice through AssemblyAI. Open the participant screen and press <b>Start live voice call</b> there to begin talking.</p><p class="modal-copy">Its opening line is: \u201c${escape(result.greeting)}\u201d</p><p class="connection-note">Approvals still work exactly as before. While you decide, the agent keeps the line warm with a holding phrase instead of going silent.</p>${button('Show the participant link','participant','btn btn-dark')}`);
  });
}
function editReply(){
  if(state.draft)state.custom=state.language!=='English'&&state.draft.translation&&!(state.source==='demo'&&state.draft.key==='custom')?state.draft.translation:state.draft.text;
  state.customDirty=Boolean(state.custom);render();const input=document.querySelector('#custom-response');input?.focus();input?.select();
}
function updateCustom(value) {
  state.custom=value;state.customDirty=Boolean(value.trim());
  const approveButton=document.querySelector('[data-action="approve"]');
  if(approveButton)approveButton.disabled=state.busy||!state.draft||state.customDirty;
  const prepareButton=document.querySelector('[data-action="custom"]');
  if(prepareButton)prepareButton.disabled=state.busy||!value.trim();
  const hint=document.querySelector('#draft-hint');
  if(hint)hint.textContent=t(state.customDirty?'dirtyHint':'hint');
}
root.addEventListener('input',event=>{
  if(event.target.id==='goal')state.goal=event.target.value;
  if(event.target.id==='business-name')state.businessName=event.target.value;
  if(event.target.id==='spending-limit')state.spendingLimit=event.target.value;
  if(event.target.id==='extra-instructions')state.extraInstructions=event.target.value;
  if(event.target.id==='caller-input')state.callerText=event.target.value;
  if(event.target.id==='custom-response')updateCustom(event.target.value);
});
root.addEventListener('change',event=>{
  const {id,value,checked}=event.target;
  if(id==='reply-tone'&&['professional','friendly','direct'].includes(value))state.replyTone=value;
  if(id==='reply-length'&&['concise','detailed'].includes(value))state.replyLength=value;
  if(id==='agent-voice'&&['female','male'].includes(value))chooseVoice(value);
  if(id==='speech-rate'&&[.8,1,1.2].includes(Number(value)))state.speechRate=Number(value);
  if(id==='auto-speak'){state.audioEnabled=checked;if(!checked)stopSpeech();}
  if(id==='appearance')window.DelegateTheme?.set(value);
  savePreferences();
});
window.addEventListener('delegate-theme-change',()=>render());
root.addEventListener('keydown' ,event=>{if(event.target.id==='custom-response'&&event.key==='Enter'){event.preventDefault();custom();}});
root.addEventListener('submit',event=>{if(event.target.id==='caller-form'){event.preventDefault();const data=new FormData(event.target);const text=String(data.get('caller')||'').trim();if(text)sendTurn(text);}});
modal.addEventListener('click',async event=>{
  const action=event.target.closest('[data-action]')?.dataset.action;
  if(action==='copy-invite'){const input=document.querySelector('#participant-link');try{await navigator.clipboard.writeText(input.value);event.target.textContent='Copied';}catch{input?.focus();input?.select();event.target.textContent='Select and copy the link';}}
  if(action==='test-guide')testGuide();
  if(action==='check-setup')await checkSetup();
  if(action==='participant'){modal.close();openParticipant();}
  if(action==='renew-invite'||action==='revoke-invite'){
    event.target.disabled=true;
    try{const result=await request(`session/${state.session}/invitation/${action==='renew-invite'?'renew':'revoke'}`);state.participantPath=result.participant_path||null;state.participantExpires=result.participant_expires_at||null;state.participantConnected=false;openParticipant();render();}
    catch(error){showModal('Invitation unchanged',`<p class="modal-copy">${escape(error.message)}</p>`);}
  }
  if(action==='confirm-delete'){
    event.target.disabled=true;
    try{await request(`session/${state.session}/delete`);removeRecent(state.session);modal.close();restart();}
    catch(error){showModal('Could not delete conversation',`<p class="modal-copy">${escape(error.message)}</p>`);}
  }
  if(action==='close-modal'){modal.close();clearInterval(introTimer);}
  if(action==='intro-demo'){modal.close();clearInterval(introTimer);navigate('setup');}
  if(action==='modal-end'){modal.close();await end();}
  if(event.target===modal){const r=modal.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)modal.close();}
});
modal.addEventListener('close',()=>clearInterval(introTimer));
window.addEventListener('beforeunload',()=>{stopListening?.();stopSpeech();cleanupRecording();cancelDictation?.();});
render();

function openParticipant() {
  const url=state.participantPath?new URL(state.participantPath,window.location?.origin||location.origin).href:null;
  const local=url&&['localhost','127.0.0.1','[::1]'].includes(new URL(url).hostname);
  showModal('Invite your business representative',`<p class="modal-copy">You control the conversation here. Your friend opens this link and replies in <b>English</b>. Your Portuguese translations, private instructions, limits, and unapproved drafts stay on your screen.</p>${url?`<label class="reply-label" for="participant-link">Private invitation · expires ${state.participantExpires?escape(new Date(state.participantExpires*1000).toLocaleString()):'after 24 hours'}</label><input id="participant-link" class="text-field" readonly value="${escape(url)}"/><div class="hero-actions"><button class="btn" data-action="copy-invite">Copy link</button><a class="btn btn-white" href="${escape(url)}" target="_blank" rel="noopener noreferrer">Open participant screen</a></div>`:'<p class="connection-banner">Invitations are turned off. Create a new link when you are ready.</p>'}${local?'<p class="connection-banner"><b>This link only works on your computer.</b> For your friend in another location, open the workspace through your public HTTPS address, then copy the invitation there.</p>':''}<p class="connection-note">Start with typed replies. Then your friend can enable agent audio and dictate replies, or use live transcription when available. Use headphones; Discord is only for discussing the test.</p><div class="hero-actions">${button(url?'Replace invitation link':'Create invitation link','renew-invite','btn btn-white btn-small')}${url?button('Revoke invitation','revoke-invite','text-button'):''}</div><p class="connection-note">Replacing or revoking a link disconnects the old invitation. Anyone holding a valid link can read the shared conversation.</p>`);
}
function firstTestCard(){
  return `<section class="panel first-test-card"><div class="eyebrow">YOUR FIRST CONVERSATION</div><h2>Two people. Two screens.</h2><ol><li><b>You set the task.</b> Choose Portuguese and review the opening.</li><li><b>Your friend joins.</b> Send the invitation; they reply in English.</li><li><b>You stay in control.</b> Read the translation, approve, edit, or decline.</li></ol><div class="test-actions">${button('Use a Portuguese test task','test-template','btn btn-dark btn-small',busy())}${button('Check setup','check-setup','btn btn-white btn-small')}${button('How to test together','test-guide','text-button')}</div></section>`;
}
function testGuide(){
  showModal('Test together in 10 minutes',`<p class="modal-copy">Your friend plays the business representative in English. You use Delegate in Portuguese. This is a browser conversation: no phone number or Discord audio connection is needed.</p>${[
    ['1','Get connected','Open the workspace over HTTPS when testing remotely. Prepare your task, approve the opening, and send your friend the private participant invitation.'],
    ['2','Try chat first','Ask your friend to type: “Early check-in costs 30 dollars.” With a $20 limit, Delegate should pause for your decision. Counter at $20, then ask your friend to accept and request booking confirmation.'],
    ['3','Add speech','On the participant screen, enable agent audio. Dictate a reply and send it, or enable live transcription if the setup check shows it is configured. Wear headphones and mute Discord while speaking to Delegate.'],
    ['4','Check the result','Try rejecting a draft and pausing the agent. End the conversation, download the summary, and check that it only records agreements you actually approved.'],
  ].map(([n,title,body])=>`<div class="modal-step"><span>${n}</span><div><b>${title}</b><p>${body}</p></div></div>`).join('')}<p class="connection-note">Ask your friend: Was it clear when to reply? Did any translation change the meaning? Did the agent say anything unexpected? Where did you wait or get stuck?</p>${button('Check this setup','check-setup','btn btn-dark')}`);
}
async function checkSetup(){
  showModal('Checking your setup…','<p class="modal-copy" role="status">Checking the server and this browser. No microphone is opened and no AI credits are used.</p>');
  try{
    const health=await request('health',undefined,'GET');
    state.pilotProtected=Boolean(health.pilot_protected);
    state.liveListeningConfigured=Boolean(health.live_listening_configured);state.voiceConfigured=Boolean(health.voice_call_configured);
    const secure=globalThis.isSecureContext!==false;
    const local=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
    const checks=[
      ['AI conversation',health.ai_configured?'Configured · try one real exchange to verify the provider.':'Needs a text-provider API key on the server. The sample still works.'],
      ['Microphone',secure&&canDictate()?'Browser dictation is available. Permission is requested only when you start.':secure?'Type replies, or use configured live transcription in a supported browser.':'Microphone access needs HTTPS. Typed chat still works.'],
      ['Live transcription',health.live_listening_configured?'Configured · your friend can use Start talking.':'Optional · add an AssemblyAI key, or use typed / dictated replies.'],
      ['Live voice call',health.voice_call_configured?'Configured · start the call here, then join from the participant screen.':'Optional · typed replies and agent audio do not require this.'],
      ['Remote sharing',local?'Local computer only. Open your public HTTPS workspace to invite a remote friend.':'Copy an invitation from this address. Your friend must be able to reach this server.'],
      ['Workspace access',health.pilot_protected?'Private pilot passcode enabled. Participant invitations do not need it.':'Local development access. Configure a pilot passcode before sharing the server publicly.'],
    ];
    showModal('Your setup',`<p class="modal-copy">Start with chat. Add audio after one successful exchange. “Configured” checks server settings, not provider connectivity or remaining credits.</p><dl class="readiness-list">${checks.map(([label,detail])=>`<div><dt>${escape(label)}</dt><dd>${escape(detail)}</dd></div>`).join('')}</dl>${button('Test together','test-guide','btn btn-dark')}`);
  }catch(error){showModal('Server unavailable',`<p class="modal-copy">${escape(error.message)}</p>${button('Try again','check-setup','btn btn-dark')}`);}
}
function confirmDelete(){
  showModal('Delete this saved conversation?',`<p class="modal-copy">This removes the instructions, transcript, approvals, and summary from this server and revokes the invitation. Download your summary first if you need a copy. This cannot be undone.</p><div class="hero-actions">${button('Delete conversation','confirm-delete','btn btn-danger')}${button('Keep conversation','close-modal','btn btn-white')}</div>`);
}

function presenterGuide() {
  showModal('Your 5-minute demo',`<p class="modal-copy">One presenter controls Delegate. A teammate plays the hotel receptionist. Use AI roleplay for real responses, or Guided demo to rehearse without API credits.</p>${[
    ['0:00–0:45','The problem','“A language barrier should not mean giving up control of your money or decisions.”'],
    ['0:45–1:30','Set the rules','Choose Spanish or French, set a $20 ceiling, and require booking approval. Open the teammate screen.'],
    ['1:30–3:30','Show the decision','Your teammate offers noon check-in for $30. Show the paused call, counter at $20, then approve the booking separately.'],
    ['3:30–4:30','Prove what happened','Show the bilingual transcript and decision ledger. Explain what is confirmed and what remains unresolved.'],
    ['4:30–5:00','Close honestly','“This is a live AI conversation with a human acting as the business. Browser transport works today; phone-number dialing is the next integration.”'],
  ].map(([time,title,text])=>`<div class="modal-step"><span>${time}</span><div><b>${title}</b><p>${text}</p></div></div>`).join('')}<a class="btn btn-dark" style="margin-top:20px" href="/assets/PRESENTER_GUIDE.md" download>Download the presenter script</a>`);
}
function applySnapshot(snapshot, {playAudio=true}={}) {
  const previousCallers=state.transcript.filter(row=>row.speaker==='caller').length;
  const history=snapshot.transcript_history||[];
  const remoteReply=history.filter(row=>row.speaker==='caller').length>previousCallers;
  if(snapshot.pilot_protected!==undefined)state.pilotProtected=Boolean(snapshot.pilot_protected);
  state.status=snapshot.status;state.participantConnected=Boolean(snapshot.participant_connected);
  if('participant_path' in snapshot)state.participantPath=snapshot.participant_path;
  state.participantExpires=snapshot.participant_expires_at;
  if('live_listening_configured' in snapshot)state.liveListeningConfigured=Boolean(snapshot.live_listening_configured);
  state.voiceLive=Boolean(snapshot.voice_live);if(state.voiceLive)stopSpeech();state.voiceConfigured=Boolean(snapshot.voice_call_configured);state.voiceHint=snapshot.voice_call_hint||'';
  if(snapshot.paused){state.interrupted=true;speechBlocked=true;stopSpeech();state.decision={translatedReason:t('paused'),reason:'You stopped the agent. Nothing else will be spoken until you approve a new reply.',options:['Ask the receptionist to wait','Decline and end the call']};}
  if(snapshot.ended_at)state.ended=snapshot.ended_at*1000;
  state.transcript=history.map(row=>({...row,time:row.time||elapsed()}));
  state.ledger=(snapshot.decision_ledger||[]).map(row=>({...row,time:row.timestamp?new Date(row.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):elapsed()}));
  const decision=snapshot.last_decision;
  if(!snapshot.paused&&['DECISION_REQUIRED','AWAITING_APPROVAL'].includes(snapshot.status)&&decision){
    state.decision={reason:decision.violation_reason,translatedReason:decision.translated_violation_reason,options:decisionOptions(decision,state.language)};
    if(decision.violation_parameter==='price')state.decision.price=Math.max(0,...(decision.violation_reason?.match(/\$(\d+(?:\.\d+)?)/g)||[]).map(v=>Number(v.slice(1))));
  }else if(['IN_PROGRESS','COMPLETED'].includes(snapshot.status)){state.decision=null;state.interrupted=false;}
  if(snapshot.pending_draft){
    state.draft={id:snapshot.pending_draft_id,text:snapshot.pending_draft,translation:snapshot.pending_translation};
  }else state.draft=null;
  if(snapshot.status==='DECISION_REQUIRED'&&!snapshot.paused&&decision?.violation_reason)append('system',decision.violation_reason,decision.translated_violation_reason);
  const last=history.at(-1);
  if(playAudio&&remoteReply&&last?.speaker==='agent')speak(last.text);
  if(snapshot.summary)state.summary=snapshot.summary;
}
async function pollSession() {
  if(polling||state.busy||state.recording||state.source!=='live'||state.page!=='live'||!state.session)return;
  polling=true;
  const session=state.session, revision=operationRevision;
  try{
    const snapshot=await request(`session/${session}/status`,undefined,'GET');
    if(state.busy||state.recording||session!==state.session||revision!==operationRevision)return;
    const signature=JSON.stringify(snapshot);
    const wasDisconnected=Boolean(state.connectionMessage);state.connectionMessage='';state.sessionGone=false;
    if(signature===state.lastSnapshot){if(wasDisconnected)render();return;}
    state.lastSnapshot=signature;applySnapshot(snapshot);render();
  }catch(error){
    state.sessionGone=error.status===404||/Session not found/i.test(error.message);
    state.connectionMessage=state.sessionGone?'This call is no longer available. Start a new call.':'Reconnecting… your conversation is saved. Your last confirmed state is shown below.';
    render();
  }finally{polling=false;}
}
setInterval(pollSession,1500);


function readRecent(){
  try{
    const recent=JSON.parse(window.localStorage?.getItem('delegate-recent-conversations')||'[]');
    return Array.isArray(recent)?recent.filter(row=>row&&typeof row.id==='string'&&/^[a-zA-Z0-9_-]{1,128}$/.test(row.id)).slice(0,12):[];
  }catch{return [];}
}
function removeRecent(id){try{window.localStorage?.setItem('delegate-recent-conversations',JSON.stringify(readRecent().filter(row=>row.id!==id)));}catch{}}
function recentConversations(){
  const recent=readRecent();
  if(!recent.length||state.source==='demo')return '';
  return `<section class="recent-section"><div class="eyebrow">RECENT ON THIS BROWSER</div><h2>Pick up where you left off.</h2><div class="recent-grid">${recent.map(row=>`<button class="panel recent-card" data-action="resume-call" data-value="${escape(row.id)}" ${busy()}><b>${escape(row.business||'Conversation')}</b><span>${escape(row.language||'English')} → English</span><small>${row.completed?'Ended · view summary':'Open conversation'}${Number.isFinite(row.started)?' · '+escape(new Date(row.started).toLocaleDateString()):''}</small></button>`).join('')}</div><p class="connection-note">These shortcuts are saved only in this browser. Conversations remain on the server until you delete them from their summary.</p></section>`;
}
function rememberSession(){
  try{
    if(state.source!=='live'||!state.session)return;
    window.sessionStorage?.setItem('delegate-active-session',state.session);
    const record={id:state.session,business:state.rules?.target_business||state.businessName||'Conversation',language:state.language,started:state.started,completed:state.status==='COMPLETED'};
    window.localStorage?.setItem('delegate-recent-conversations',JSON.stringify([record,...readRecent().filter(row=>row.id!==state.session)].slice(0,12)));
  }catch{}
}

function forgetSession(){try{window.sessionStorage?.removeItem('delegate-active-session');}catch{}}
async function resumeSession(){
  if(state.page==='landing')return;
  let session;
  try{session=window.sessionStorage?.getItem('delegate-active-session');}catch{return;}
  if(!session)return;
  state.busy=true;render();
  try{
    const snapshot=await request(`session/${session}/status`,undefined,'GET');
    Object.assign(state,{session,source:'live',businessName:snapshot.rules?.target_business||'',replyTone:snapshot.preferences?.tone||'professional',replyLength:snapshot.preferences?.reply_length||'concise',extraInstructions:snapshot.preferences?.additional_instructions||'',mode:snapshot.mode,language:snapshot.language==='Portuguese'?'Portuguese (Brazil)':snapshot.language||'English',goal:snapshot.raw_prompt||SAMPLE_PROMPT,rules:snapshot.rules,participantPath:snapshot.participant_path,audioConfigured:Boolean(snapshot.audio_configured),started:(snapshot.created_at||Date.now()/1000)*1000,lastSnapshot:JSON.stringify(snapshot)});
    applySnapshot(snapshot,{playAudio:false});
    state.page=snapshot.summary?'summary':'live';rememberSession();
  }catch(error){
    if(error.status===404){forgetSession();removeRecent(session);}
    state.error='Could not restore the saved call. '+error.message;
  }finally{state.busy=false;render();}
}
resumeSession();
