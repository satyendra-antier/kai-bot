import { useEffect, useState } from "react";

const KIND_LABEL = {
  source: "Source",
  trigger: "Trigger",
  fetch: "Fetch",
  condition: "Condition",
  keep_matching: "Keep matching",
  ask: "Ask in chat",
  approval: "Approval",
  action: "Action",
  result: "Result",
  question: "Question",
  question_popup: "Question popup",
  user_response: "User response",
  user_response_popup: "User response popup",
  sumsub: "Sumsub",
  score: "Score",
  stop: "Stop",
  review: "Review",
  handoff: "Handoff",
  baas: "BaaS",
};

function JsonField({ label, value, onSave }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState("");

  useEffect(() => {
    setEditing(false);
    setError("");
    setText(JSON.stringify(value ?? {}, null, 2));
  }, [value]);

  function save() {
    try {
      const parsed = JSON.parse(text || "{}");
      setError("");
      setEditing(false);
      onSave(parsed);
    } catch {
      setError("That JSON is not finished.");
    }
  }

  const empty = !value || (typeof value === "object" && Object.keys(value).length === 0);

  return (
    <div className="json-field">
      <div className="json-label">
        <span>{label}</span>
        <button type="button" className="link" onClick={() => setEditing((open) => !open)}>edit as JSON</button>
      </div>
      {editing ? (
        <>
          <textarea value={text} onChange={(event) => setText(event.target.value)} />
          {error ? <div className="reason">{error}</div> : null}
          <div className="row"><button type="button" onClick={save}>Save</button></div>
        </>
      ) : empty ? (
        <p className="none">— none</p>
      ) : (
        <pre className="json-preview">{text}</pre>
      )}
    </div>
  );
}

function optionLabel(option) {
  return typeof option === "string" ? option : (option.label || "");
}

function optionPort(option, label) {
  if (option && typeof option === "object" && option.port) return option.port;
  return label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "answer";
}

function QuestionConfig({ input, onSave }) {
  const source = input && typeof input === "object" ? input : {};
  const [skip, setSkip] = useState(Boolean(source.skip));
  const [ask, setAsk] = useState(source.ask || "");
  const [answerType, setAnswerType] = useState(source.type || "free_text");
  const [optionsText, setOptionsText] = useState((source.options || []).map(optionLabel).join("\n"));
  const [fieldsText, setFieldsText] = useState((source.fields || []).map((field) => `${field.id || "answer"} | ${field.ask || ""} | ${field.type || "text"}`).join("\n"));

  useEffect(() => {
    const value = input && typeof input === "object" ? input : {};
    const fields = value.fields || [];
    setSkip(Boolean(value.skip));
    setAnswerType(value.type || "free_text");
    setAsk(value.ask || fields.map((field) => field.ask).filter(Boolean).join("\n"));
    setOptionsText((value.options || []).map(optionLabel).join("\n"));
    setFieldsText(fields.map((field) => `${field.id || "answer"} | ${field.ask || ""} | ${field.type || "text"}`).join("\n"));
  }, [JSON.stringify(input ?? {})]);

  function commit(patch) {
    onSave({ ...source, ...patch });
  }

  function commitOptions() {
    const current = source.options || [];
    commit({
      type: "simple_select",
      options: optionsText.split("\n").map((line) => line.trim()).filter(Boolean).map((label) => {
        const existing = current.find((option) => optionLabel(option) === label);
        return { label, port: optionPort(existing, label) };
      }),
    });
  }

  function commitFields() {
    commit({
      type: "free_text",
      fields: fieldsText.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
        const [id, askLine, type] = line.split("|").map((part) => (part || "").trim());
        return { id: id || "answer", ask: askLine || "", type: type || "text" };
      }),
    });
  }

  return (
    <div className="says">
      <div className="group">Question</div>
      <label className="check"><input type="checkbox" checked={skip} onChange={(event) => { setSkip(event.target.checked); commit({ skip: event.target.checked }); }} /> Skip this question</label>
      <label>Kai asks</label>
      <textarea value={ask} onChange={(event) => setAsk(event.target.value)} onBlur={() => commit({ ask })} />
      <label>Answer type</label>
      <select value={answerType} onChange={(event) => { setAnswerType(event.target.value); commit({ type: event.target.value }); }}>
        <option value="simple_select">One answer from a list</option>
        <option value="free_text">Free text</option>
      </select>
      {answerType === "simple_select" ? (
        <>
          <label>One answer per line</label>
          <textarea value={optionsText} onChange={(event) => setOptionsText(event.target.value)} onBlur={commitOptions} />
        </>
      ) : (
        <>
          <label>Fields, one id and question per line</label>
          <textarea value={fieldsText} onChange={(event) => setFieldsText(event.target.value)} onBlur={commitFields} />
        </>
      )}
    </div>
  );
}

function SourceChat({ contract, onSave }) {
  const saved = contract || {};
  const [description, setDescription] = useState(saved.description || "");
  const [examples, setExamples] = useState((saved.examples || []).join("\n"));
  const [exclusions, setExclusions] = useState((saved.exclusions || []).join("\n"));
  useEffect(() => {
    setDescription(saved.description || "");
    setExamples((saved.examples || []).join("\n"));
    setExclusions((saved.exclusions || []).join("\n"));
  }, [contract]);

  function lines(text) {
    return text.split("\n").map((item) => item.trim()).filter(Boolean);
  }

  return (
    <div className="says">
      <div className="group">Chat config</div>
      <p>This is the same chat contract as the Chat tab. A message that matches starts here, then follows the arrow.</p>
      <label>Description the classifier reads</label>
      <textarea value={description} onChange={(event) => setDescription(event.target.value)} onBlur={() => onSave({ description })} />
      <label>Examples, one per line</label>
      <textarea value={examples} onChange={(event) => setExamples(event.target.value)} onBlur={() => onSave({ examples: lines(examples) })} />
      <label>Not this</label>
      <textarea value={exclusions} onChange={(event) => setExclusions(event.target.value)} onBlur={() => onSave({ exclusions: lines(exclusions) })} />
    </div>
  );
}

export default function StepInspector({ step, stepType, steps, edges, slots, contract, onClose, onSave, onSaveContract, onEditChat, onConnect, onDelete }) {
  const [purpose, setPurpose] = useState("");
  const [line, setLine] = useState("");
  useEffect(() => {
    setPurpose(step?.purpose || step?.title || "");
    const fromContract = contract?.lines && Object.prototype.hasOwnProperty.call(contract.lines, step?.id) ? contract.lines[step.id] : null;
    setLine(fromContract != null ? fromContract : (step?.line || ""));
  }, [step, contract]);
  if (!step) return null;
  const kind = KIND_LABEL[step.kind] || step.kind;
  const input = step.input !== undefined && step.input !== null ? step.input : (stepType?.input || {});
  const output = step.output !== undefined && step.output !== null ? step.output : (stepType?.output || {});
  const incoming = (edges || []).filter((edge) => edge.to === step.id).map((edge) => (steps || []).find((item) => item.id === edge.from)).filter(Boolean);
  const optionPorts = ((step.input && step.input.options) || []).map((option) => (typeof option === "string" ? "" : option.port)).filter(Boolean);
  const ports = [...new Set([...(stepType?.ports || []), ...(edges || []).filter((edge) => edge.from === step.id).map((edge) => edge.port), ...optionPorts])];

  return (
    <aside className="inspector">
      <div className="inspector-title">
        <strong>{kind}</strong>
        <button type="button" className="link" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="inspector-id">{step.id}</div>
      <label>Purpose</label>
      <input value={purpose} onChange={(event) => setPurpose(event.target.value)} onBlur={() => onSave({ purpose, title: purpose })} />
      <div className="group">Step type</div>
      {stepType ? (
        <>
          <div className="type-line">
            <span>{stepType.title}</span>
            <button type="button" className="link" onClick={onEditChat}>{stepType.slug} ↗</button>
          </div>
          {stepType.summary ? <p>{stepType.summary}</p> : null}
        </>
      ) : (
        <p>This step has no type yet.</p>
      )}
      {step.kind === "trigger" && incoming.length ? (
        <div className="says">
          <div className="group">Started by</div>
          {incoming.map((item) => <p key={item.id}>{item.purpose || item.title || item.id}</p>)}
          {incoming.every((item) => item.type === "rule") ? <p>Only the rule starts this. Chat sets up the rule, then ends.</p> : null}
        </div>
      ) : null}
      {step.type === "chat_request" || step.id === "src_chat" ? (
        <SourceChat contract={contract} onSave={onSaveContract} />
      ) : null}
      <div className="says">
        <div className="group">{step.kind === "source" ? "Kai says when this chat starts" : "Kai says"}</div>
        <textarea value={line} onChange={(event) => setLine(event.target.value)} onBlur={() => onSave({ line: line || null })} />
        <button type="button" className="link" onClick={onEditChat}>Edit in the Chat tab →</button>
      </div>
      {ports.length ? (
        <div className="says">
          <div className="group">Then goes to</div>
          <p>{step.kind === "question" || step.kind === "question_popup" ? "Each answer can go to a different step. A new answer starts on the same step until you change it." : "Then is the next step. Nowhere ends the chat on this step."}</p>
          {ports.map((port) => {
            const own = (edges || []).find((edge) => edge.from === step.id && edge.port === port);
            const sameAsContinue = (edges || []).find((edge) => edge.from === step.id && edge.port === "continue");
            const current = own || (optionPorts.includes(port) ? sameAsContinue : null);
            const option = ((step.input && step.input.options) || []).find((item) => item && item.port === port);
            const name = option?.label || (port === "continue" ? (step.kind === "question" || step.kind === "question_popup" ? "Any other answer" : "Then") : port);
            return (
              <label key={port}>
                {name}
                <select value={current?.to || ""} onChange={(event) => onConnect(port, event.target.value)}>
                  <option value="">Nowhere</option>
                  {(steps || []).filter((item) => item.id !== step.id).map((item) => (
                    <option key={item.id} value={item.id}>{item.id}</option>
                  ))}
                </select>
              </label>
            );
          })}
        </div>
      ) : null}
      {step.kind === "question" || step.kind === "question_popup" ? (
        <QuestionConfig input={input} onSave={(next) => onSave({ input: next, line: next.ask || (next.fields || []).map((field) => field.ask).filter(Boolean).join(" ") || step.line })} />
      ) : null}
      <JsonField label="Input" value={input} onSave={(next) => onSave({ input: next })} />
      <JsonField label="Output" value={output} onSave={(next) => onSave({ output: next })} />
      {step.type === "chat_request" && (slots || []).length ? (
        <div className="says">
          <div className="group">Slots, in fill order</div>
          <ol className="slot-order">
            {(slots || []).map((slot, index) => {
              const label = typeof slot === "string" ? slot : (slot.key || slot.name || "");
              return <li key={`${label}-${index}`}>{label}</li>;
            })}
          </ol>
        </div>
      ) : null}
      <div className="row"><button type="button" className="danger" onClick={onDelete}>Remove step</button></div>
    </aside>
  );
}
