import { useMemo, useState } from "react";
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
  const sync = catalog?.sync || {};
  const [query, setQuery] = useState("");
  const [onlyUsed, setOnlyUsed] = useState(false);
  const [kindFilter, setKindFilter] = useState("");

  const kinds = useMemo(() => {
    const found = [];
    types.forEach((item) => {
      if (!found.includes(item.kind)) found.push(item.kind);
    });
    return found;
  }, [types]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return types.filter((item) => {
      if (kindFilter && item.kind !== kindFilter) return false;
      if (onlyUsed && !(item.used_as || []).length) return false;
      if (!needle) return true;
      const hay = `${item.title} ${item.slug} ${item.summary} ${(item.used_as || []).map((use) => use.workflow).join(" ")}`.toLowerCase();
      return hay.includes(needle);
    });
  }, [types, query, onlyUsed, kindFilter]);

  const kindsShown = kinds.filter((kind) => filtered.some((item) => item.kind === kind));

  return (
    <div className="panel settings">
      <p className="crumb">User intelligence / Workflows / Settings</p>
      <h1>Step types</h1>
      <p>
        The shared registry of canvas building blocks. When you click <b>Add step</b> on a workflow, you pick one of these types —
        that stamps the step’s kind, ports, and input/output shape. They are <b>not</b> the same as “+ Add workflow” templates
        (those copy a whole draft graph from an existing workflow or the four-lane stub).
      </p>

      <div className="sync-board">
        <div className="sync-card">
          <strong>{sync.registry_types ?? types.length}</strong>
          <span>Registry types</span>
        </div>
        <div className="sync-card">
          <strong>{sync.typed_steps ?? 0}</strong>
          <span>Steps using a registry type</span>
        </div>
        <div className="sync-card">
          <strong>{sync.custom_kind_steps ?? (catalog?.custom_kind || []).length}</strong>
          <span>Custom-kind steps (onboarding)</span>
        </div>
        <div className={`sync-card ${sync.in_sync === false || (catalog?.untyped || []).length ? "warn" : "ok"}`}>
          <strong>{sync.missing_type_steps ?? (catalog?.untyped || []).length}</strong>
          <span>Broken · no type and no kind</span>
        </div>
      </div>
      <p className="hint">
        {types.length} types in {kinds.length} kinds · {catalog?.steps || 0} steps across {catalog?.workflows || 0} workflows.
        Money and demo canvases use registry types (Add step stays in sync). Onboarding uses kind-only steps on purpose — they show under Custom kinds, not as errors.
      </p>
      {(sync.unused_types || []).length ? (
        <p className="hint">Unused registry types (available in Add step, not on any canvas yet): {(sync.unused_types || []).join(", ")}</p>
      ) : null}

      <div className="settings-toolbar">
        <input value={query} placeholder="Search type, slug, or workflow…" onChange={(event) => setQuery(event.target.value)} />
        <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
          <option value="">All kinds</option>
          {kinds.map((kind) => (
            <option key={kind} value={kind}>{KIND_LABEL[kind] || kind}</option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" checked={onlyUsed} onChange={(event) => setOnlyUsed(event.target.checked)} />
          Only types in use
        </label>
      </div>

      {kindsShown.map((kind) => {
        const rows = filtered.filter((item) => item.kind === kind);
        return (
          <section key={kind} className={`kind-block kind-${kind}`}>
            <header>
              <h2>{KIND_LABEL[kind] || kind}</h2>
              <p>{KIND_ABOUT[kind]} Column: {LANES[laneOf(kind)]?.title || "Continue / true"}.</p>
              <span>{rows.length} {rows.length === 1 ? "type" : "types"}</span>
            </header>
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>What it does</th>
                  <th>Input template</th>
                  <th>Output template</th>
                  <th>Used on canvases</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.type}>
                    <td>
                      <b>{item.title}</b>
                      <small>{item.slug}</small>
                      <small className="usage-count">{(item.usage_count ?? (item.used_as || []).length)} uses</small>
                    </td>
                    <td>{item.summary}</td>
                    <td>{templateText(item.input) === "— none" ? <span className="none">— none</span> : <pre>{templateText(item.input)}</pre>}</td>
                    <td>{templateText(item.output) === "— none" ? <span className="none">— none</span> : <pre>{templateText(item.output)}</pre>}</td>
                    <td className="used">
                      {(item.used_as || []).length ? (
                        (item.used_as || []).map((use) => (
                          <button key={`${use.workflow_id}-${use.id}`} type="button" className="chip" onClick={() => onOpen(use.workflow_id)} title={use.purpose || use.id}>
                            <b>{use.id}</b>
                            <small>{use.workflow}</small>
                          </button>
                        ))
                      ) : (
                        <span className="none">Not on any canvas yet · still offered in Add step</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}

      {(catalog?.custom_kind || []).length ? (
        <section className="kind-block kind-custom">
          <header>
            <h2>Custom kinds (onboarding)</h2>
            <p>These steps use a kind (question, review, sumsub…) without a registry type. They are intentional for the POC compliance trees — not Add-step templates.</p>
            <span>{catalog.custom_kind.length}</span>
          </header>
          <ul className="untyped">
            {catalog.custom_kind.slice(0, 80).map((step) => (
              <li key={`${step.workflow_id}-${step.id}`}>
                <button type="button" className="link" onClick={() => onOpen(step.workflow_id)}>{step.id}</button>
                <small>{step.workflow} · {step.kind || "kind"}</small>
              </li>
            ))}
          </ul>
          {catalog.custom_kind.length > 80 ? <p className="hint">Showing 80 of {catalog.custom_kind.length}.</p> : null}
        </section>
      ) : null}

      {(catalog?.untyped || []).length ? (
        <section className="kind-block kind-broken">
          <header>
            <h2>Broken steps</h2>
            <p>No type and no kind — fix these on the canvas or delete them.</p>
            <span>{catalog.untyped.length}</span>
          </header>
          <ul className="untyped">
            {catalog.untyped.map((step) => (
              <li key={`${step.workflow_id}-${step.id}`}>
                <button type="button" className="link" onClick={() => onOpen(step.workflow_id)}>{step.id}</button>
                <small>{step.workflow}</small>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
