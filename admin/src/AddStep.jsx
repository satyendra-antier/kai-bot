import { useState } from "react";
import { laneOf, LANES } from "./lanes";

const KIND_LABEL = {
  source: "Source",
  trigger: "Trigger",
  fetch: "Fetch",
  keep_matching: "Keep matching",
  condition: "Condition",
  ask: "Ask in chat",
  approval: "Approval",
  action: "Action",
  result: "Result",
};

const KIND_ABOUT = {
  source: "Where a run comes from. It hands its inputs to the trigger and plays no part after.",
  trigger: "The same inputs, whichever source started the run.",
  fetch: "Reads a balance, a rate, a quote, or a list of accounts. Nothing moves.",
  keep_matching: "Walks a list and keeps the first item that meets the rule.",
  condition: "A yes or a no. Each answer is another path on the canvas.",
  ask: "Kai asks in the chat and waits. Nothing moves until the customer answers.",
  approval: "The customer agrees before money moves.",
  action: "Does something outside the run: moves money, sends a message, saves a rule, starts another workflow.",
  result: "Where the run ends.",
};

function templateText(value) {
  if (!value || (typeof value === "object" && Object.keys(value).length === 0)) return "";
  return JSON.stringify(value, null, 2);
}

export default function AddStep({ types, steps, anchorId, onAdd, onClose }) {
  const kinds = [];
  (types || []).forEach((item) => {
    if (!kinds.includes(item.kind)) kinds.push(item.kind);
  });
  const [kind, setKind] = useState(steps?.length ? "action" : "source");
  const templates = (types || []).filter((item) => item.kind === kind);
  const [typeName, setTypeName] = useState(templates[0]?.type || "");
  const chosen = templates.find((item) => item.type === typeName) || templates[0];
  const anchor = (steps || []).find((step) => step.id === anchorId);
  const title = !steps?.length ? "Add to Source" : `After ${anchor?.id || (steps || [])[steps.length - 1]?.id || "the last step"}`;
  const lane = LANES[laneOf(chosen?.kind || kind, [], false)];
  const input = templateText(chosen?.input);
  const output = templateText(chosen?.output);

  function pickKind(next) {
    setKind(next);
    const first = (types || []).find((item) => item.kind === next);
    setTypeName(first?.type || "");
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-label={title}>
        <header>
          <div>
            <strong>{title}</strong>
            <span>From the shared step-type registry (same list as Settings → Step types)</span>
          </div>
          <button type="button" className="link" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="modal-body">
          <div className="modal-kinds">
            {kinds.map((item) => (
              <button key={item} type="button" className={item === kind ? "on" : ""} onClick={() => pickKind(item)}>
                <span>{KIND_LABEL[item] || item}</span>
                <small>{(types || []).filter((row) => row.kind === item).length}</small>
              </button>
            ))}
          </div>
          <div className="modal-templates">
            <p>{KIND_ABOUT[kind]}</p>
            <p className="hint">Each card is a step type template — not a whole workflow. Adding it places one node on this canvas.</p>
            {templates.map((item) => (
              <button key={item.type} type="button" className={chosen?.type === item.type ? "on" : ""} onClick={() => setTypeName(item.type)}>
                <strong>{item.title}</strong>
                <span>{item.summary}</span>
                {(item.usage_count ?? (item.used_as || []).length) ? (
                  <small>Used on {item.usage_count ?? (item.used_as || []).length} step{(item.usage_count ?? (item.used_as || []).length) === 1 ? "" : "s"}</small>
                ) : (
                  <small>Not used on a canvas yet</small>
                )}
              </button>
            ))}
          </div>
          {chosen ? (
            <div className="modal-detail">
              <div className="kind">{KIND_LABEL[chosen.kind] || chosen.kind}</div>
              <strong>{chosen.slug}</strong>
              <p>{chosen.summary}</p>
              <label>Lands in</label>
              <p>{lane?.title || "Continue / true"}</p>
              <label>Input shape</label>
              <pre>{input || "— none"}</pre>
              <label>Output shape</label>
              <pre>{output || "— none"}</pre>
              <button type="button" className="add-step" onClick={() => onAdd(chosen)}>Add this step</button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
