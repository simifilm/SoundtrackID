// Reading a run's progress events. The server streams them (SSE) as the answer
// to a POST, which EventSource can't send, so the answer's body is read here.

// Call onEvent with each event of `response`, in order; an event onEvent can't
// handle goes to onEventError(err, text), and the reading goes on. Resolves
// once the stream ends; rejects when the connection breaks off (the server
// killed, the network gone).
async function readEvents(response, onEvent, onEventError) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop();
    for (const part of parts) {
      if (!part.startsWith('data: ')) continue;
      try {
        onEvent(JSON.parse(part.slice(6)));
      } catch (err) {
        onEventError(err, part);
      }
    }
  }
}
