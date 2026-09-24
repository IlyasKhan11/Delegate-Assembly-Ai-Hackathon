import messages from './locales.json' with {type: 'json'};
export function translate(language, key, values={}) {
  if(language==='Portuguese')language='Portuguese (Brazil)';
  return (messages[language]?.[key] || messages.English[key] || key).replace(/\{(\w+)\}/g, (_, name)=>String(values[name]??`{${name}}`));
}
// Display labels are separate from the canonical actions sent to the backend.
export function optionLabel(language, action) {
  const exact={'Decline the offer':'decline','Approve this booking':'confirm','Ask for more details before deciding':'details','Ask for an alternative':'alternative','Ask the receptionist to wait':'wait','Decline and end the call':'end'};
  if(exact[action])return translate(language,exact[action]);
  const negotiate=action.match(/^Negotiate down to (.+)$/);
  if(negotiate)return translate(language,'negotiate',{limit:negotiate[1]});
  const accept=action.match(/^Approve (.+) — this time only$/);
  if(accept)return translate(language,'accept',{amount:accept[1]});
  return action;
}
export function decisionOptions(decision,language) {
  const actions=decision.suggested_user_options?.length?decision.suggested_user_options:['Ask for an alternative','Decline the offer'];
  const translated=decision.translated_user_options||[];
  return actions.map((value,index)=>({value,label:translated.length===actions.length&&translated[index]?translated[index]:optionLabel(language,value)}));
}
