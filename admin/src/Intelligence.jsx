import { useEffect, useState } from "react";

const KEY = "demo-key";

async function api(path) {
  const res = await fetch(path, { headers: { "X-Api-Key": KEY } });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const CHECKS = [
  ["intent", "Intent", "chat"],
  ["slots", "Slots", "chat"],
  ["turns", "Turns to confirm", "chat"],
  ["lines", "Lines as written", "chat"],
  ["guardrails", "Guardrails", "chat"],
  ["right_end", "Right end", "outcome"],
  ["speed", "Speed", "outcome"],
  ["finished", "Finished", "outcome"],
  ["no_errors", "No errors", "outcome"],
];

function Score({ value, reason }) {
  return (
    <div>
      <div>{value == null ? "—" : value}</div>
      {reason ? <div className="reason">{reason}</div> : null}
    </div>
  );
}

export default function Intelligence({ onOpenWorkflow, focusWorkflow }) {
  const [page, setPage] = useState("runs");
  const [workflowId, setWorkflowId] = useState("");
  const [ruleCode, setRuleCode] = useState("");
  const [origin, setOrigin] = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [runs, setRuns] = useState(null);
  const [rules, setRules] = useState(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState(null);

  useEffect(() => {
    if (!focusWorkflow) return;
    setPage("runs");
    setRuleCode("");
    setOrigin("");
    setFlaggedOnly(false);
    setWorkflowId(focusWorkflow);
  }, [focusWorkflow]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (workflowId) params.set("workflow_id", workflowId);
    if (ruleCode) params.set("rule", ruleCode);
    if (origin) params.set("origin", origin);
    if (flaggedOnly) params.set("flagged", "1");
    const query = params.toString();
    api("/v1/intelligence/runs" + (query ? "?" + query : ""))
      .then((data) => {
        setRuns(data);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [workflowId, ruleCode, origin, flaggedOnly]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (workflowId && page === "rules") params.set("workflow_id", workflowId);
    const query = params.toString();
    api("/v1/intelligence/rules" + (query ? "?" + query : ""))
      .then((data) => {
        setRules(data);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [workflowId, page]);

  function showRule(code) {
    setPage("runs");
    setWorkflowId("");
    setOrigin("");
    setFlaggedOnly(false);
    setRuleCode(code);
  }

  function showEveryRun() {
    setRuleCode("");
    setWorkflowId("");
    setOrigin("");
    setFlaggedOnly(false);
  }

  function pickWorkflow(id) {
    setRuleCode("");
    setWorkflowId(id);
  }

  const stats = runs?.stats;
  const headline = page === "runs" ? runs?.headline : rules?.headline;
  const sidebar = page === "runs" ? runs?.workflows || [] : rules?.workflows || [];

  return (
    <div className="intel">
      <header className="intel-head">
        <div className="crumb">User intelligence / {page === "runs" ? "Runs" : "Live rules"}</div>
        <div className="subtabs">
          <button className={page === "runs" ? "on" : ""} onClick={() => setPage("runs")}>Runs</button>
          <button className={page === "rules" ? "on" : ""} onClick={() => { setPage("rules"); setRuleCode(""); }}>Live rules</button>
        </div>
        {page === "runs" ? (
          <p>Every time a workflow ran for a customer — from a rule or a chat request — scored twice: how Kai handled the chat, and how the run ended.</p>
        ) : (
          <p>The rules customers have built — the workflow each one starts and their values in it. Every time a rule fires is on Runs.</p>
        )}
        {headline && page === "runs" ? (
          <div className="headline">
            <b>{headline.flagged} of {headline.total}</b>
            <span>flagged under each workflow's pass bar, or any guardrail broken</span>
          </div>
        ) : null}
        {headline && page === "rules" ? (
          <div className="headline">
            <b>{headline.attention} of {headline.total}</b>
            <span>{headline.on} on · {headline.paused} paused</span>
          </div>
        ) : null}
        {page === "runs" && runs?.focus ? (
          <div className="focus">
            <div>
              <strong>{runs.focus.code}</strong>
              <span>{runs.focus.runs} runs of this rule</span>
            </div>
            <button onClick={showEveryRun}>Show every run</button>
          </div>
        ) : null}
        {page === "runs" && stats ? (
          <div className="stats">
            <div>
              <span>Runs</span>
              <b>{stats.runs}</b>
              <small>{stats.chat_runs} chat · {stats.rule_runs} rule</small>
            </div>
            <div>
              <span>Chat score</span>
              <b>{stats.chat == null ? "—" : stats.chat}</b>
              <small>average over chat runs</small>
            </div>
            <div>
              <span>Outcome score</span>
              <b>{stats.outcome == null ? "—" : stats.outcome}</b>
              <small>average over all runs</small>
            </div>
            <div>
              <span>Flagged</span>
              <b>{stats.flagged}</b>
              <small>{stats.flagged ? "need a look" : "clear"}</small>
            </div>
          </div>
        ) : null}
        {error ? <p className="reason">{error}</p> : null}
      </header>
      <div className="intel-body">
        <aside>
          <div className="group">Workflow</div>
          <button className={!workflowId ? "item active" : "item"} onClick={() => pickWorkflow("")}>
            All <b>{page === "runs" ? headline?.total ?? "" : rules?.headline?.total ?? ""}</b>
          </button>
          {sidebar.map((row) => (
            <button key={row.id} className={workflowId === row.id ? "item active" : "item"} onClick={() => pickWorkflow(row.id)}>
              {row.name} <b>{row.count}</b>
            </button>
          ))}
        </aside>
        <section>
          {page === "runs" ? (
            <>
              <div className="filters">
                <label>
                  Source
                  <select value={origin} onChange={(event) => setOrigin(event.target.value)}>
                    <option value="">All</option>
                    <option value="chat">Chat</option>
                    <option value="rule">Rule</option>
                  </select>
                </label>
                <label className="check">
                  <input type="checkbox" checked={flaggedOnly} onChange={(event) => setFlaggedOnly(event.target.checked)} />
                  Flagged only
                </label>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Started</th>
                    <th>Customer</th>
                    <th>Workflow</th>
                    <th>Source</th>
                    <th>Ended on</th>
                    <th>Chat</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {(runs?.rows || []).map((row) => (
                    <tr key={row.id} className={row.flagged ? "flagged" : picked?.id === row.id ? "picked" : ""} onClick={() => setPicked(row)}>
                      <td>{row.code}</td>
                      <td>
                        {row.started_label}
                        {row.duration_label ? <small>{row.duration_label}</small> : null}
                      </td>
                      <td>
                        {row.customer_name || "—"}
                        {row.customer_handle ? <small>@{row.customer_handle}</small> : null}
                      </td>
                      <td>
                        <button className="link" onClick={() => onOpenWorkflow(row.workflow_id)}>{row.workflow_name}</button>
                      </td>
                      <td>
                        {row.origin === "rule" ? (
                          <button className="link" onClick={() => showRule(row.rule_code)}>Rule · {row.rule_code}</button>
                        ) : (
                          "Chat"
                        )}
                      </td>
                      <td>{row.ended_on}</td>
                      <td><Score value={row.chat} reason={row.reason_on === "chat" ? row.reason : ""} /></td>
                      <td><Score value={row.outcome} reason={row.reason_on === "outcome" ? row.reason : ""} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <table className="rules">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Workflow</th>
                  <th>Customer</th>
                  <th>Made</th>
                  <th>Their values</th>
                  <th>Rule state</th>
                </tr>
              </thead>
              <tbody>
                {(rules?.rows || []).map((row) => (
                  <tr key={row.id}>
                    <td>
                      <button className="link" onClick={() => showRule(row.code)}>{row.code}</button>
                      {row.since_label ? <small>since {row.since_label}</small> : null}
                    </td>
                    <td>
                      <button className="link" onClick={() => onOpenWorkflow(row.workflow_id)}>{row.workflow_name} ↗</button>
                    </td>
                    <td>
                      {row.customer_name}
                      {row.customer_handle ? <small>@{row.customer_handle}</small> : null}
                    </td>
                    <td>{row.made_in}</td>
                    <td>
                      <div className="values">
                        {(row.values || []).map((item) => (
                          <div key={item.label}>
                            <span>{item.label}</span>
                            <b>{item.value}</b>
                          </div>
                        ))}
                      </div>
                    </td>
                    <td className={row.state === "on" ? "on-state" : "paused-state"}>{row.state === "on" ? "On" : "Paused"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        {page === "runs" && picked ? (
          <aside className="run-drawer">
            <header>
              <div>
                <strong>{picked.customer_name || picked.code}</strong>
                <span>{picked.started_label}{picked.duration_label ? ` · ${picked.duration_label}` : ""}</span>
              </div>
              <button type="button" onClick={() => setPicked(null)} aria-label="Close">×</button>
            </header>
            <p>{picked.workflow_name}</p>
            <p className="hint">{picked.origin === "rule" ? `Rule · ${picked.rule_code}` : "Chat"}{picked.conversation_id ? ` · ${picked.conversation_id}` : ""}{picked.step_id ? ` · at ${picked.step_id}` : ""} · {picked.ended_on || picked.status}</p>
            {picked.confidence != null && picked.confidence !== "" ? (
              <p className="hint">Intent confidence {picked.confidence}{picked.intent_source ? ` · ${picked.intent_source}` : ""}{picked.checks?.chat != null ? ` · eval chat ${picked.checks.chat}` : ""}</p>
            ) : null}
            <h3>Scores</h3>
            <p className="hint">Chat checks (intent, slots, turns, lines, guardrails / Kai never) and outcome checks (right end, speed, finished, no errors). Guardrails 0 flags the run.</p>
            {CHECKS.filter((item) => picked.origin === "chat" || item[2] === "outcome").map(([key, label]) => {
              const value = picked.checks?.[key];
              if (value == null) return null;
              return (
                <div key={key} className="score-bar">
                  <div><span>{label}</span><b>{value}</b></div>
                  <i style={{ width: `${Math.max(0, Math.min(100, Number(value)))}%` }} />
                </div>
              );
            })}
            {(picked.reasons || []).length ? <p className="hint">Flagged · {(picked.reasons || []).join(", ")}</p> : null}
            <h3>Chat audit · each turn</h3>
            {(picked.turns || []).length ? (
              <ol className="llm-flow">
                {picked.turns.map((turn, index) => {
                  const seen = turn.model_input || "";
                  const same = seen === (turn.text || "");
                  const steps = turn.ai_steps || [];
                  const slotText = turn.slots && Object.keys(turn.slots).length
                    ? Object.entries(turn.slots).map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`).join(", ")
                    : "";
                  return (
                    <li key={index}>
                      <div className="llm-step"><span>1 · You typed</span><p>{turn.text}</p></div>
                      <div className="llm-step"><span>2 · Sanitized input</span><p>{seen || "—"}</p><small>{same ? "Nothing was removed." : "Email, phone, and card numbers were replaced."}</small></div>
                      <div className="llm-step"><span>3 · Held back</span><p>{(turn.pii || []).length ? turn.pii.join(", ") : "Nothing"}</p></div>
                      <div className="llm-step">
                        <span>4 · Outcome</span>
                        <p>{[turn.action || "chat", turn.source, turn.tool, turn.workflow_key, turn.step_id, turn.port].filter(Boolean).join(" · ")}</p>
                        <small>
                          {[
                            turn.confidence != null && turn.confidence !== "" ? `confidence ${turn.confidence}` : "",
                            turn.needs && turn.needs !== "none" ? `needs ${turn.needs}` : "",
                            turn.route ? `route ${turn.route}` : "",
                            slotText ? `slots ${slotText}` : "",
                          ].filter(Boolean).join(" · ") || "—"}
                        </small>
                      </div>
                      {steps.length ? (
                        <div className="llm-step">
                          <span>5 · Decision trail</span>
                          <ol className="ai-steps">
                            {steps.map((step, stepIndex) => (
                              <li key={`${step.id || "step"}-${stepIndex}`}>
                                <b>{step.label || step.id}</b>
                                <p>{step.detail}</p>
                              </li>
                            ))}
                          </ol>
                          {turn.strike ? <small>Objection strike {turn.strike} of 3</small> : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="hint">Live chat turns stamp redact → local/Gemini → route → guard → decision on each user message. Seeded history rows have scores only.</p>
            )}
            <h3>Run audit · graph hops</h3>
            <p className="hint">Every hop on the canvas: start, conditions, fetches, asks/replies, actions, results. Ports and tool payloads are kept on the event.</p>
            {(picked.flow || []).length ? (
              <ol className="run-flow">
                {picked.flow.map((step, index) => (
                  <li key={`${step.id}-${index}`}>
                    <span>{index + 1} · {step.id}</span>
                    <b>{step.kind_label} · {step.type_label}{step.port ? ` · ${step.port}` : ""}</b>
                    <p>{step.detail}</p>
                  </li>
                ))}
              </ol>
            ) : (picked.trace || []).length ? (
              <ol className="path">{picked.trace.map((step) => <li key={step}>{step}</li>)}</ol>
            ) : (
              <p className="hint">This history row keeps the scores. A live run stores the step path and RunEvent audit here.</p>
            )}
            {(picked.messages || []).length ? (
              <>
                <h3>What Kai said</h3>
                {picked.messages.map((message, index) => <p key={index}>{message.text}</p>)}
              </>
            ) : null}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
