// Optional browser dictation. Recognition may use the browser vendor's service;
// it never calls the paid AI/ML model. Typed replies are always available.
export function canDictate() {
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}
export function dictate({onText, onError, onEnd}) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    onError('This browser does not support dictation. Type your response instead.');
    onEnd();
    return () => {};
  }
  const recognition = new Recognition();
  recognition.lang = 'en-US';
  recognition.continuous = false;
  recognition.interimResults = false;
  let finished = false;
  const timer = setTimeout(() => recognition.stop(), 30000);
  const finish = () => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    onEnd();
  };
  recognition.onresult = event => {
    const text = Array.from(event.results).filter(row => row.isFinal).map(row => row[0].transcript).join(' ').trim();
    if (text) onText(text);
  };
  recognition.onerror = event => {
    if (event.error === 'aborted') return;
    const errors = {
      'not-allowed': 'Microphone permission was denied. Allow it in your browser, or type your response.',
      'network': 'The browser speech service is unavailable. Type your response instead.',
      'no-speech': 'No speech was detected. Try again or type your response.',
      'audio-capture': 'No microphone is available. Type your response instead.',
    };
    onError(errors[event.error] || 'Dictation is unavailable. Type your response instead.');
    finish();
  };
  recognition.onend = finish;
  try { recognition.start(); }
  catch { onError('Could not start dictation. Type your response instead.'); finish(); }
  return () => { recognition.abort(); finish(); };
}
