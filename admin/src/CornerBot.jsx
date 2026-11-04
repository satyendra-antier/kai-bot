import { useEffect, useRef, useState } from "react";

const KEY = "demo-key";

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Api-Key": KEY, ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error(await res.text());
  if (res.status === 204) return null;
  return res.json();
}

function freshId() {
  return "c-" + Date.now();
}

export default function CornerBot({ workflow, onTrace }) {
  const openId = workflow?.id && (workflow.channels || []).includes("chat") ? workflow.id : "";
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState(openId);
  const [conversationId, setConversationId] = useState(freshId);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [run, setRun] = useState({});
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState("");
  const logRef = useRef(null);
  const followed = useRef(openId);
  const onTraceRef = useRef(onTrace);
  const traced = useRef("");
  onTraceRef.current = onTrace;

  useEffect(() => {
    if (followed.current === openId) return;
    followed.current = openId;
    setPin(openId);
  }, [openId]);

  useEffect(() => {
    setConversationId(freshId());
    setRun({});
    setMessages([]);
    setError("");
    traced.current = "";
  }, [pin]);

  useEffect(() => {
    if (!open) return undefined;
    let stopped = false;
    async function pull() {
      try {
        const data = await api("/v1/conversations/" + conversationId);
        if (stopped || !data) return;
        setMessages(data.messages || []);
        setRun(data.run || {});
        const next = (data.run && data.run.trace) || [];
        const stamp = next.join(">");
        if (pin && stamp !== traced.current) {
          traced.current = stamp;
          if (onTraceRef.current) onTraceRef.current(pin, next);
        }
      } catch {
        if (!stopped) setError("");
      }
    }
    pull();
    const timer = setInterval(pull, 1000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [open, conversationId, pin]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, run, open]);

  const known = new Set(messages.filter((row) => row.role === "kai").map((row) => row.text));
  const spoken = (run.messages || []).map((row) => row.text).filter((line) => line && !known.has(line));
  const prompt = run.prompt && ((run.prompt.choices || []).length || (run.prompt.fields || []).length) ? run.prompt : null;

  async function send(value) {
    const body = (value ?? text).trim();
    if (!body || sending) return;
    setText("");
    setSending(true);
    setError("");
    try {
      await api("/v1/conversations/" + conversationId + "/messages", {
        method: "POST",
        body: JSON.stringify({ text: body, workflow_id: pin || null }),
      });
    } catch (err) {
      setError(err.message || "The message did not send.");
    } finally {
      setSending(false);
    }
  }

  function restart() {
    setConversationId(freshId());
    setRun({});
    setMessages([]);
    setError("");
    if (pin && onTrace) onTrace(pin, []);
  }

  return (
    <div className="corner">
      {open ? (
        <section className="corner-panel" aria-label="Kai chat">
          <header>
            <div>
              <strong>Kai</strong>
              <span>{pin && workflow?.id === pin ? workflow.name : "Online"}</span>
            </div>
            <div className="corner-actions">
              <button type="button" onClick={restart}>New chat</button>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close chat">×</button>
            </div>
          </header>
          <div className="corner-log" ref={logRef}>
            {messages.map((row, index) => (
              <div key={`${row.role}-${index}`} className={row.role === "user" ? "bubble user" : "bubble"}>
                <span className="who">{row.role === "user" ? "You" : "Kai"}</span>
                {row.text}
              </div>
            ))}
            {spoken.map((line, index) => (
              <div key={`run-${index}`} className="bubble">
                <span className="who">Kai</span>
                {line}
              </div>
            ))}
            {prompt ? (
              <div className="prompt-card">
                {(prompt.choices || []).map((choice) => (
                  <button key={choice.port} type="button" onClick={() => send(choice.label || choice.port)}>{choice.label || choice.port}</button>
                ))}
                {(prompt.fields || []).length ? (
                  <form onSubmit={(event) => {
                    event.preventDefault();
                    const body = [...event.currentTarget.querySelectorAll("input")].map((input) => input.value.trim()).filter(Boolean).join("\n");
                    if (body) send(body);
                  }}>
                    {(prompt.fields || []).map((field) => (
                      <input key={field.id} name={field.id} placeholder={field.ask || field.id} type={field.type === "password" ? "password" : "text"} />
                    ))}
                    <button type="submit">Send</button>
                  </form>
                ) : null}
              </div>
            ) : null}
            {!messages.length && !spoken.length ? (
              <p className="corner-empty">Ask Kai something. A new chat starts the published workflow.</p>
            ) : null}
          </div>
          {error ? <p className="corner-note">{error}</p> : null}
          <form onSubmit={(event) => { event.preventDefault(); send(); }}>
            <input value={text} placeholder="Top up my USD with €100 from EUR" onChange={(event) => setText(event.target.value)} />
            <button type="submit" disabled={sending}>Send</button>
          </form>
        </section>
      ) : null}
      <button type="button" className="corner-launcher" onClick={() => setOpen((value) => !value)}>
        {open ? "Close" : "Kai"}
      </button>
    </div>
  );
}
