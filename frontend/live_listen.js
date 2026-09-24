// Live speech-to-text through AssemblyAI's streaming API.
//
// You talk, and each sentence arrives the moment you finish it — no pressing
// record, no waiting for an upload. The browser opens the socket to AssemblyAI
// itself using a short-lived token from our server, so the API key never
// reaches this page and no tunnel is needed.

const SAMPLE_RATE = 16000;
// AssemblyAI accepts 50-1000 ms of audio per message. An AudioWorklet always
// hands over 128 samples at a time, which at 16 kHz is only 8 ms, so the
// samples are gathered into 100 ms frames before being sent.
const FRAME_SAMPLES = SAMPLE_RATE / 10;

export function canListenLive() {
  return Boolean(window.AudioContext || window.webkitAudioContext) &&
         Boolean(navigator.mediaDevices?.getUserMedia) &&
         'WebSocket' in window;
}

// The streaming API wants raw 16-bit PCM bytes, not base64 or JSON.
function toPcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return pcm.buffer;
}

// AudioWorklet is the supported way to read microphone samples, and it has to
// load from its own file — building that file in memory keeps the page
// dependency-free. ScriptProcessor covers older browsers.
const WORKLET_SOURCE = `
class MicrophoneTap extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.frame = new Float32Array((options && options.processorOptions
      && options.processorOptions.frameSamples) || 1600);
    this.filled = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i += 1) {
      this.frame[this.filled] = channel[i];
      this.filled += 1;
      if (this.filled === this.frame.length) {
        this.port.postMessage(this.frame.slice(0));
        this.filled = 0;
      }
    }
    return true;
  }
}
registerProcessor('listen-tap', MicrophoneTap);
`;

async function openMicrophone(context, stream, onSamples) {
  const source = context.createMediaStreamSource(stream);
  try {
    const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], {type: 'application/javascript'}));
    await context.audioWorklet.addModule(moduleUrl);
    URL.revokeObjectURL(moduleUrl);
    const tap = new AudioWorkletNode(context, 'listen-tap',
      {processorOptions: {frameSamples: FRAME_SAMPLES}});
    tap.port.onmessage = event => onSamples(event.data);
    source.connect(tap);
    return () => { tap.port.onmessage = null; tap.disconnect(); source.disconnect(); };
  } catch {
    // 4096 samples is 256 ms at 16 kHz, comfortably inside the accepted range.
    const processor = context.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = event => onSamples(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(processor);
    const silence = context.createGain();
    silence.gain.value = 0;
    processor.connect(silence);
    silence.connect(context.destination);
    return () => { processor.onaudioprocess = null; processor.disconnect(); silence.disconnect(); source.disconnect(); };
  }
}

/**
 * Starts listening. `credentials` comes from the backend and carries the
 * short-lived token. Calls back with:
 *   {type:'ready'}                      the microphone is live
 *   {type:'partial', text}              words so far, still being revised
 *   {type:'final', text}                a finished sentence, safe to send
 *   {type:'error', message}             something went wrong
 *   {type:'ended'}                      listening stopped
 * Returns a function that stops listening, or null if it could not start.
 */
export async function startListening(credentials, onEvent) {
  const notify = (type, detail = {}) => onEvent?.({type, ...detail});
  let stream, context, closeMicrophone, socket;
  let stopped = false;
  let lastFinalTurn = -1;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    try {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'Terminate'}));
    } catch {}
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
    notify('error', {message: 'Microphone permission was denied. Allow it, or type instead.'});
    return null;
  }

  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  context = new AudioContextClass({sampleRate: SAMPLE_RATE});
  await context.resume().catch(() => {});
  if (context.sampleRate !== SAMPLE_RATE) {
    stream.getTracks().forEach(track => track.stop());
    await context.close().catch(() => {});
    notify('error', {message: 'This browser cannot record at the rate the transcriber needs. Type instead.'});
    return null;
  }

  socket = new WebSocket(`${credentials.websocket_url}&token=${encodeURIComponent(credentials.token)}`);
  socket.binaryType = 'arraybuffer';

  socket.addEventListener('open', async () => {
    closeMicrophone = await openMicrophone(context, stream, samples => {
      if (socket.readyState === WebSocket.OPEN) socket.send(toPcm16(samples));
    });
  });

  socket.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'Begin') {
      notify('ready');
    } else if (message.type === 'Turn') {
      const text = (message.transcript || '').trim();
      if (!text) return;
      // Formatting updates can repeat the same finalized turn. Submit it once.
      if(Number.isInteger(message.turn_order)&&message.turn_order<=lastFinalTurn)return;
      if(message.end_of_turn&&Number.isInteger(message.turn_order))lastFinalTurn=message.turn_order;
      // Partials get revised as the speaker continues; only a finished turn is
      // worth acting on, so nothing half-heard is ever sent as a reply.
      notify(message.end_of_turn ? 'final' : 'partial', {text});
    } else if (message.type === 'Termination') {
      stop();
    } else if (message.error) {
      notify('error', {message: String(message.error)});
    }
  });

  socket.addEventListener('error', () => {
    notify('error', {message: 'The transcription connection failed. You can still type.'});
  });
  socket.addEventListener('close', stop);

  return stop;
}
