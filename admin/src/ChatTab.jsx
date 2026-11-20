import { useEffect, useRef, useState } from "react";

function tokens(text) {
  return [...String(text || "").matchAll(/\{(\w+)\}/g)].map((match) => match[1]);
}

function previewBalances(ledger) {
  const accounts = (ledger && ledger.accounts) || {};
  const parts = Object.entries(accounts).map(([code, row]) => {
    const shown = String((row && row.display) || "");
    if (!shown) return code;
    return shown.toUpperCase().includes(String(code).toUpperCase()) ? shown : `${code} ${shown}`;
  });
  return parts.length ? parts.join(" · ") : "none on this draft yet";
}

function chipLabel(item) {
  return typeof item === "string" ? item : (item.text || item.key || item.name || "");
}

function Chips({ items, onRemove, onAdd, addLabel, placeholder }) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  function commit() {
    const value = draft.trim();
    if (!value) return;
    onAdd(value);
    setDraft("");
    setOpen(false);
  }

  return (
    <div className="chip-field">
      <div className="chips">
        {(items || []).map((item, index) => (
          <span key={`${chipLabel(item)}-${index}`} className="chip">
            <span>{chipLabel(item)}</span>
            <button type="button" aria-label={`Remove ${chipLabel(item)}`} onClick={() => onRemove(index)}>×</button>
          </span>
        ))}
        {!open ? (
          <button type="button" className="chip-add" onClick={() => setOpen(true)}>{addLabel}</button>
        ) : null}
      </div>
      {open ? (
        <form
          className="chip-compose"
          onSubmit={(event) => {
            event.preventDefault();
            commit();
          }}
        >
          <input
            ref={inputRef}
            value={draft}
            placeholder={placeholder || "Type and press Enter"}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setDraft("");
                setOpen(false);
              }
            }}
          />
          <button type="submit" disabled={!draft.trim()}>Add</button>
          <button
            type="button"
            className="chip-cancel"
            onClick={() => {
              setDraft("");
              setOpen(false);
            }}
          >
            Cancel
          </button>
        </form>
      ) : null}
    </div>
  );
}

export default function ChatTab({ contract, graph, onChange, onSave, onTry, tryResult }) {
  const [draft, setDraft] = useState("");
  if (!contract) return null;
  const lines = contract.lines || {};
  const speaking = (graph?.steps || []).filter((step) => Object.prototype.hasOwnProperty.call(lines, step.id) || step.line);
  const lineText = (step) => (Object.prototype.hasOwnProperty.call(lines, step.id) ? lines[step.id] : step.line) || "";

  function setLines(next) {
    onChange({ ...contract, lines: next });
  }

  return (
    <div className="chat-layout">
    <div className="panel chat-tab">
      <label>Setup</label>
      <p className="hint">Said to the customer on the ask band — Kai thinks this might be the request but is not sure enough to start. Prefer a short yes/no. This field wins over the chat-entry line for that ask.</p>
      <textarea value={contract.setup || ""} onChange={(event) => onChange({ ...contract, setup: event.target.value })} />
      <label>When nothing matches</label>
      <p className="hint">Said when the score is below “Not this”, the phrase is an exclusion, or Kai has no workflow to start. Also used after a plain greeting. Try-it shows this line for exclusion chips.</p>
      <textarea value={contract.idle || ""} onChange={(event) => onChange({ ...contract, idle: event.target.value })} />
      <h2>When is it this request</h2>
      <label>Description the classifier reads</label>
      <p className="hint">Written for the model, not the customer. Say what the customer wants, not which words they use.</p>
      <textarea value={contract.description || ""} onChange={(event) => onChange({ ...contract, description: event.target.value })} />
      <label>Examples</label>
      <p className="hint">Phrases that should start this workflow. Keep them short and specific.</p>
      <Chips
        items={contract.examples || []}
        addLabel="+ example"
        placeholder="e.g. Tell me when EUR goes under 50"
        onAdd={(value) => value && onChange({ ...contract, examples: [...(contract.examples || []), value] })}
        onRemove={(index) => onChange({ ...contract, examples: (contract.examples || []).filter((_, item) => item !== index) })}
      />
      <label>Not this — send it elsewhere</label>
      <p className="hint">Phrases that look similar but belong to another workflow.</p>
      <Chips
        items={contract.exclusions || []}
        addLabel="+ exclusion"
        placeholder="e.g. Top up my USD"
        onAdd={(value) => value && onChange({ ...contract, exclusions: [...(contract.exclusions || []), value] })}
        onRemove={(index) => onChange({ ...contract, exclusions: (contract.exclusions || []).filter((_, item) => item !== index) })}
      />
      <h2>How sure Kai must be</h2>
      <div className="bands">
        <label>Start at or above<input value={contract.bands?.start_at ?? 0.75} onChange={(event) => onChange({ ...contract, bands: { ...contract.bands, start_at: Number(event.target.value) } })} /></label>
        <label>Ask what they meant at or above<input value={contract.bands?.ask_at ?? 0.5} onChange={(event) => onChange({ ...contract, bands: { ...contract.bands, ask_at: Number(event.target.value) } })} /></label>
        <label>Not this below<input value={contract.bands?.not_this_below ?? 0.5} onChange={(event) => onChange({ ...contract, bands: { ...contract.bands, not_this_below: Number(event.target.value) } })} /></label>
      </div>
      <p className="hint">The score is about the intent only. What runs is decided by the slots, never by the score.</p>
      <h2>What Kai fills, or asks</h2>
      <p className="hint">A slot is asked before the steps only when its fill rule is set. The message fills it when it says so; otherwise Kai asks, one at a time.</p>
      {(contract.slots || []).map((slot, index) => (
        <div className="slot-row" key={slot.key || index}>
          <input value={slot.key || ""} onChange={(event) => {
            const slots = [...contract.slots];
            slots[index] = { ...slot, key: event.target.value };
            onChange({ ...contract, slots });
          }} />
          <input value={slot.ask || ""} placeholder="What Kai asks" onChange={(event) => {
            const slots = [...contract.slots];
            slots[index] = { ...slot, ask: event.target.value };
            onChange({ ...contract, slots });
          }} />
          <input value={slot.fill_from || ""} onChange={(event) => {
            const slots = [...contract.slots];
            slots[index] = { ...slot, fill_from: event.target.value, order: index + 1 };
            onChange({ ...contract, slots });
          }} />
          <button type="button" className="link" onClick={() => onChange({ ...contract, slots: contract.slots.filter((_, item) => item !== index) })}>×</button>
        </div>
      ))}
      <button type="button" className="link" onClick={() => onChange({ ...contract, slots: [...(contract.slots || []), { key: "slot", order: (contract.slots || []).length + 1, fill_from: "" }] })}>+ slot</button>
      <h2>What Kai says</h2>
      <p className="hint">One line per moment in the workflow. Words in braces are filled from the run. The same line shows on that step on the canvas.</p>
      {speaking.map((step) => (
        <div key={step.id} className="line-row">
          <div className="inspector-id">{step.id}</div>
          <textarea value={lineText(step)} onChange={(event) => setLines({ ...lines, [step.id]: event.target.value })} />
          <div className="chips">{tokens(lineText(step)).map((token) => <span key={token} className="chip">{`{${token}}`}</span>)}</div>
          {step.kind === "approval" || step.type === "confirm" ? (
            <div className="slot-row">
              {((contract.choices && contract.choices[step.id]) || (step.config && step.config.choices) || (step.kind === "approval" ? [{ port: "approve", label: "Approve" }, { port: "reject", label: "Reject" }] : [{ port: "yes", label: "Yes" }, { port: "no", label: "No" }])).map((choice, index, current) => (
                <input key={choice.port || index} value={choice.label || ""} onChange={(event) => {
                  const choices = current.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item);
                  onChange({ ...contract, choices: { ...(contract.choices || {}), [step.id]: choices } });
                }} />
              ))}
            </div>
          ) : null}
        </div>
      ))}
      <button type="button" className="link" onClick={() => {
        const quiet = (graph?.steps || []).find((step) => !lineText(step));
        if (quiet) setLines({ ...lines, [quiet.id]: "" });
      }}>+ line</button>
      <h2>Kai never</h2>
      <p className="hint">Guardrails for this workflow. Judged rules flag the run and block the phrase live if Kai tries to say it. Order rules are checked against the step timeline when the run is scored.</p>
      <Chips
        items={(contract.never || []).map((rule) => rule.text || rule.id)}
        addLabel="+ rule"
        placeholder="Something Kai must never say or invent"
        onAdd={(value) => value && onChange({ ...contract, never: [...(contract.never || []), { id: `rule-${(contract.never || []).length + 1}`, mode: "judged", text: value, forbidden: [value] }] })}
        onRemove={(index) => onChange({ ...contract, never: (contract.never || []).filter((_, item) => item !== index) })}
      />
      <div className="row"><button type="button" onClick={onSave}>Save chat contract</button></div>
      <p className="hint">Save updates the draft immediately — live chat loads the draft, so classifier examples, slots, Setup, idle, and Kai never take effect without publishing. Publish still snapshots a version for rules and eval history.</p>
    </div>
    <aside className="try-rail">
      <h2>Try it</h2>
      <form onSubmit={(event) => { event.preventDefault(); if (draft.trim() && !tryResult?.loading) onTry(draft.trim()); }}>
        <textarea value={draft} placeholder="Tell me when EUR to USD goes above 1.10" onChange={(event) => setDraft(event.target.value)} disabled={!!tryResult?.loading} />
        <button type="submit" disabled={!draft.trim() || !!tryResult?.loading}>{tryResult?.loading ? "Running…" : "Run"}</button>
      </form>
      <div className="prompts">
        {(contract.try_prompts || contract.examples || []).map((prompt, index) => (
          <button key={typeof prompt === "string" ? prompt : index} type="button" disabled={!!tryResult?.loading} onClick={() => { const text = typeof prompt === "string" ? prompt : (prompt.text || ""); setDraft(text); onTry(text); }}>{typeof prompt === "string" ? prompt : (prompt.text || "")}</button>
        ))}
      </div>
      <p className="hint">Balances in this preview: {previewBalances(contract.preview_ledger)}</p>
      {tryResult?.loading ? (
        <div className="run-note try-loading" aria-live="polite" aria-busy="true">
          <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
          <span>Scoring “{tryResult.text}”…</span>
        </div>
      ) : null}
      {tryResult?.error ? (
        <div className="run-note try-error">{tryResult.error}</div>
      ) : null}
      {tryResult && !tryResult.loading && !tryResult.error ? (
        <div className="run-note">
          <p>{tryResult.band} · {tryResult.confidence}</p>
          <p>{(tryResult.trace || []).join(" → ")}</p>
          {(tryResult.messages || []).map((message, index) => <p key={index}>{message.text}</p>)}
        </div>
      ) : null}
    </aside>
    </div>
  );
}
