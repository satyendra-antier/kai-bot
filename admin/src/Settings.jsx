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
  source: "Where a run can start. A customer message, or a rule that fired.",
  trigger: "The same inputs, whichever source started the run.",
  fetch: "Reads a balance, a rate, a quote, or a list of accounts. Nothing moves.",
  keep_matching: "Walks a list and keeps the first item that meets the rule.",
  condition: "A yes or a no. Each answer is another path on the canvas.",
  ask: "Kai asks in the chat and waits. Nothing moves until the customer answers.",
  approval: "The customer agrees before money moves.",
  action: "Does the thing: moves money, sends a message, saves a rule, or hands the run on.",
  result: "Where the run ends.",
};

function templateText(value) {
  if (!value || (typeof value === "object" && Object.keys(value).length === 0)) return "— none";
  return JSON.stringify(value, null, 2);
}

export default function Settings({ catalog, onOpen }) {
  const types = catalog?.types || [];
  const kinds = [];
  types.forEach((item) => {
    if (!kinds.includes(item.kind)) kinds.push(item.kind);
  });
  return (
    <div className="panel settings">
      <p className="crumb">User intelligence / Workflows / Settings</p>
      <h1>Step types</h1>
      <p>Every step on every canvas is one of these. A type says what the step does, what it takes and what it gives back; the step fills it with that workflow’s values.</p>
      <p className="hint">{types.length} types in {kinds.length} kinds · {catalog?.steps || 0} steps in {catalog?.workflows || 0} workflows</p>
      <p className="hint">{(catalog?.untyped || []).length} without a type</p>
      {kinds.map((kind) => {
        const rows = types.filter((item) => item.kind === kind);
        return (
          <section key={kind} className={`kind-block kind-${kind}`}>
            <header>
              <h2>{KIND_LABEL[kind] || kind}</h2>
              <p>{KIND_ABOUT[kind]} Column: {LANES[laneOf(kind)]?.title || "Continue / true"}.</p>
              <span>{rows.length} {rows.length === 1 ? "type" : "types"}</span>
            </header>
            <table>
              <thead><tr><th>Type</th><th>What it does</th><th>Input template</th><th>Output template</th><th>Used as</th></tr></thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.type}>
                    <td><b>{item.title}</b><small>{item.slug}</small></td>
                    <td>{item.summary}</td>
                    <td>{templateText(item.input) === "— none" ? <span className="none">— none</span> : <pre>{templateText(item.input)}</pre>}</td>
                    <td>{templateText(item.output) === "— none" ? <span className="none">— none</span> : <pre>{templateText(item.output)}</pre>}</td>
                    <td className="used">
                      {(item.used_as || []).map((use) => (
                        <button key={`${use.workflow_id}-${use.id}`} type="button" className="chip" onClick={() => onOpen(use.workflow_id)}>
                          <b>{use.id}</b>
                          <small>{use.workflow}</small>
                        </button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
      {(catalog?.untyped || []).length ? (
        <section className="kind-block">
          <header>
            <h2>Without a type</h2>
            <p>Onboarding steps that are not one of the 26 types yet.</p>
            <span>{catalog.untyped.length}</span>
          </header>
          <ul className="untyped">
            {catalog.untyped.map((step) => <li key={`${step.workflow_id}-${step.id}`}>{step.id}<small>{step.workflow}</small></li>)}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
