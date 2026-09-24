// Retain a request ID across an ambiguous failure, so a manual retry cannot
// charge for or append the same receptionist turn / draft twice.
const pendingRequests = new Map();
export function requestId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
export async function request(path, body, method = 'POST') {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), method === 'GET' ? 5000 : 90000);
  const form = body instanceof FormData;
  const deduplicated = method === 'POST' && !form && body && /(?:evaluate-turn|user-action|participant\/[^/]+\/turn)$/.test(path);
  const signature = deduplicated ? `${path}:${JSON.stringify(body)}` : null;
  if (signature) {
    if (!pendingRequests.has(signature)) pendingRequests.set(signature, requestId());
    body = {...body, request_id: body.request_id || pendingRequests.get(signature)};
    while (pendingRequests.size > 32) pendingRequests.delete(pendingRequests.keys().next().value);
  }
  try {
    const response = await fetch(`/api/${path}`, {
      method, signal: controller.signal, cache: 'no-store',
      ...(form ? {body} : body !== undefined ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
    });
    let data;
    try { data = await response.json(); }
    catch { const error = new Error('The server returned an unexpected response. Your text is kept here; check the connection before retrying.'); error.status = response.status; throw error; }
    if (!response.ok) {
      if (signature && response.status < 500) pendingRequests.delete(signature);
      const detail = data.detail;
      const error = new Error(typeof detail === 'string' ? detail : detail?.error || 'The request could not be completed. Please try again.');
      error.status = response.status;
      throw error;
    }
    if (signature) pendingRequests.delete(signature);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Connection timed out. Reconnect to check the call state before trying again.');
    if (error instanceof TypeError) throw new Error('Cannot reach the backend. Reconnect to continue.');
    throw error;
  } finally { clearTimeout(timeout); }
}
