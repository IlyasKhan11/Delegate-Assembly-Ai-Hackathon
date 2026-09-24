// Plays the agent's replies in a natural neural voice served by the backend.
//
// The browser's own speechSynthesis is kept only as a fallback, because on most
// machines it sounds robotic. The backend voice costs nothing and needs no key,
// but it takes a second or two to generate -- so a reply can be prepared while
// the human is still reading it, and then plays instantly on approval.

let generation = 0;
let finishPlaying = null;
let playing = null;       // the <audio> element currently speaking
let playingUrl = null;    // its object URL, revoked when finished
// Keyed by voice as well as text: switching voice must not replay the old one.
const prepared = new Map();  // "voice::text" -> Promise<Blob>, so approval is instant
const cacheKey = (voice, text) => `${voice || 'default'}::${text}`;

function remember(key, blobPromise) {
  prepared.set(key, blobPromise);
  // Keep only the last few replies; a long call should not grow forever.
  while (prepared.size > 8) prepared.delete(prepared.keys().next().value);
  return blobPromise;
}

async function fetchSpeech(endpoint, text, voice) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(`/api/${endpoint}`, {
      method: 'POST', signal: controller.signal,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, voice}),
    });
    if (!response.ok) throw new Error(`the server refused it (HTTP ${response.status})`);
    const blob = await response.blob();
    if (!blob || blob.size < 512) throw new Error('the server sent no audio');
    return blob;
  } finally { clearTimeout(timeout); }
}

// Falling back silently is what made "it still sounds robotic" so hard to
// diagnose. Every fallback now says which step failed, on screen and in the
// console, so the cause is visible instead of guessed at.
function reportFallback(reason, onError) {
  const message = `Natural voice unavailable (${reason}) — using the browser's built-in voice.`;
  console.warn('[voice]', message);
  onError?.(message);
}

/** Starts generating a reply's audio early, so approving it plays with no wait. */
export function prepareSpeech(endpoint, text, voice) {
  const key = cacheKey(voice, text);
  if (!endpoint || !text || prepared.has(key)) return;
  // A failure here is not worth reporting: the real attempt will retry and can
  // still fall back to the browser voice.
  remember(key, fetchSpeech(endpoint, text, voice).catch(() => null));
}

export function stopSpeaking() {
  generation += 1;
  const finish = finishPlaying; finishPlaying = null; finish?.();
  window.speechSynthesis?.cancel();
  if (playing) {
    playing.pause();
    playing.src = '';
    playing = null;
  }
  if (playingUrl) {
    URL.revokeObjectURL(playingUrl);
    playingUrl = null;
  }
}

// Resolves when the line has finished being spoken, so several lines can be
// queued without talking over each other.
function speakWithBrowser(text, {rate = 1, onError} = {}) {
  if (!('speechSynthesis' in window)) {
    onError?.('Speech playback is unavailable in this browser. The approved reply remains in the transcript.');
    return Promise.resolve();
  }
  return new Promise(resolve => {
    finishPlaying = resolve;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-US';
    utterance.rate = rate;
    utterance.onend = resolve;
    utterance.onerror = event => {
      if (!['canceled', 'interrupted'].includes(event.error)) {
        onError?.('Audio could not play. Check your browser sound settings; the approved text is available in the transcript.');
      }
      resolve();
    };
    speechSynthesis.speak(utterance);
  });
}

/**
 * Speaks one line and resolves once it has finished, so callers can queue
 * several. `endpoint` is the backend speech route for this conversation, or
 * null to use the browser voice. Resolves to 'neural', 'browser', or 'none'
 * so a caller can report which voice was actually heard.
 */
export async function speakReply(endpoint, text, {rate = 1, voice, onError} = {}) {
  if (!text) return 'none';
  stopSpeaking();
  const revision = generation;
  if (!endpoint) {
    await speakWithBrowser(text, {rate, onError});
    return 'browser';
  }
  const key = cacheKey(voice, text);
  let blob = null;
  let reason = '';
  try {
    blob = await (prepared.get(key) ?? remember(key, fetchSpeech(endpoint, text, voice)));
    if (!blob) reason = 'an earlier attempt for this line had already failed';
  } catch (error) {
    reason = error.message || 'the request failed';
  }
  if (revision !== generation) return 'none';
  if (!blob) {
    prepared.delete(key);
    reportFallback(reason || 'no audio was returned', onError);
    await speakWithBrowser(text, {rate});
    return 'browser';
  }
  const audio = new Audio();
  playingUrl = URL.createObjectURL(blob);
  audio.src = playingUrl;
  audio.playbackRate = rate;
  playing = audio;
  const finished = new Promise(resolve => {
    finishPlaying = resolve;
    audio.onended = () => { if (playing === audio) stopSpeaking(); resolve(); };
    audio.onerror = resolve;
  });
  try {
    await audio.play();
  } catch (error) {
    if (revision !== generation) return 'none';
    // Usually the browser blocking sound until the page has been clicked.
    stopSpeaking();
    reportFallback(error.name === 'NotAllowedError'
      ? 'the browser blocked audio until you interact with the page'
      : `playback failed: ${error.name || error.message}`, onError);
    await speakWithBrowser(text, {rate});
    return 'browser';
  }
  await finished;
  return 'neural';
}
