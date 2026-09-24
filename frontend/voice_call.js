// Live voice call over the AssemblyAI Voice Agent API.
//
// This runs on the business participant's screen. Their microphone goes up to
// AssemblyAI, which transcribes it, asks CallBridge what to say, and sends the
// spoken reply back as audio. The AssemblyAI key never reaches this page: the
// backend mints a one-time token for each call.

const SAMPLE_RATE = 24000;

export function canPlaceVoiceCall() {
  return Boolean(window.AudioContext || window.webkitAudioContext) &&
         Boolean(navigator.mediaDevices?.getUserMedia) &&
         'WebSocket' in window;
}

// --- audio conversion -------------------------------------------------------
// The API speaks and listens in 16-bit PCM, base64 encoded inside JSON.

function encodePcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function decodePcm16(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const pcm = new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
  const samples = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i += 1) samples[i] = pcm[i] / 0x8000;
  return samples;
}

// --- microphone -------------------------------------------------------------

// AudioWorklet is the supported way to read raw microphone samples, but it
// must load from its own file. Building that file in memory keeps this page
// dependency-free. ScriptProcessor is the fallback for older browsers.
const WORKLET_SOURCE = `
class MicrophoneTap extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(new Float32Array(channel));
    return true;
  }
}
registerProcessor('microphone-tap', MicrophoneTap);
`;

async function openMicrophone(context, stream, onSamples) {
  const source = context.createMediaStreamSource(stream);
  try {
    const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], {type: 'application/javascript'}));
    await context.audioWorklet.addModule(moduleUrl);
    URL.revokeObjectURL(moduleUrl);
    const tap = new AudioWorkletNode(context, 'microphone-tap');
    tap.port.onmessage = event => onSamples(event.data);
    source.connect(tap);
    return () => { tap.port.onmessage = null; tap.disconnect(); source.disconnect(); };
  } catch {
    const processor = context.createScriptProcessor(2048, 1, 1);
    processor.onaudioprocess = event => onSamples(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(processor);
    // A muted destination keeps the deprecated node running without echoing.
    const silence = context.createGain();
    silence.gain.value = 0;
    processor.connect(silence);
    silence.connect(context.destination);
    return () => { processor.onaudioprocess = null; processor.disconnect(); silence.disconnect(); source.disconnect(); };
  }
}

// --- speaker ----------------------------------------------------------------

function createSpeaker(context) {
  let playAt = 0;
  let sources = [];
  return {
    play(samples) {
      if (!samples.length) return;
      const buffer = context.createBuffer(1, samples.length, SAMPLE_RATE);
      buffer.copyToChannel(samples, 0);
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      playAt = Math.max(playAt, context.currentTime);
      source.start(playAt);
      playAt += buffer.duration;
      sources.push(source);
      source.onended = () => { sources = sources.filter(item => item !== source); };
    },
    // Used when the receptionist talks over the agent: drop what is queued so
    // the interruption feels immediate instead of waiting out the sentence.
    stop() {
      for (const source of sources) { try { source.stop(); } catch {} }
      sources = [];
      playAt = 0;
    },
  };
}

// --- the call ---------------------------------------------------------------

/**
 * Opens the live voice call. `credentials` comes from the backend and carries
 * the one-time token. `onEvent` receives {type, ...} updates for the UI.
 * Returns a function that hangs up, or null if the call could not start.
 */
export async function startVoiceCall(credentials, onEvent) {
  const notify = (type, detail = {}) => onEvent?.({type, ...detail});
  let stream, context, closeMicrophone, socket, speaker;
  let stopped = false;

  const hangUp = () => {
    if (stopped) return;
    stopped = true;
    try { socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({type: 'session.end'})); } catch {}
    speaker?.stop();
    closeMicrophone?.();
    stream?.getTracks().forEach(track => track.stop());
    try { socket?.close(); } catch {}
    context?.close().catch(() => {});
    notify('ended');
  };

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true},
    });
  } catch {
    notify('error', {message: 'Microphone permission was denied. Allow it, or keep typing your replies.'});
    return null;
  }

  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  context = new AudioContextClass({sampleRate: SAMPLE_RATE});
  await context.resume().catch(() => {});
  if (context.sampleRate !== SAMPLE_RATE) {
    stream.getTracks().forEach(track => track.stop());
    await context.close().catch(() => {});
    notify('error', {message: 'This browser cannot record at the rate the voice service needs. Type your replies instead.'});
    return null;
  }
  speaker = createSpeaker(context);

  socket = new WebSocket(`${credentials.websocket_url}?token=${encodeURIComponent(credentials.token)}`);

  socket.addEventListener('open', async () => {
    socket.send(JSON.stringify({type: 'session.update', session: {agent_id: credentials.agent_id}}));
    closeMicrophone = await openMicrophone(context, stream, samples => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({type: 'input.audio', audio: encodePcm16(samples)}));
      }
    });
  });

  socket.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    switch (message.type) {
      case 'session.ready':
        notify('ready');
        break;
      case 'input.speech.started':
        speaker.stop();
        notify('listening');
        break;
      case 'transcript.user':
        notify('transcript', {speaker: 'caller', text: message.text});
        break;
      case 'reply.audio':
        speaker.play(decodePcm16(message.data));
        break;
      case 'transcript.agent':
        notify('transcript', {speaker: 'agent', text: message.text});
        break;
      case 'reply.done':
        notify('replied');
        break;
      case 'session.ended':
        hangUp();
        break;
      case 'error':
      case 'session.error':
        notify('error', {message: message.error || message.message || 'The voice service reported a problem.'});
        break;
    }
  });

  socket.addEventListener('error', () => {
    notify('error', {message: 'The voice connection failed. You can still type your replies.'});
  });
  socket.addEventListener('close', hangUp);

  return hangUp;
}
