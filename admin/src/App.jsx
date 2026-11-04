import { useCallback, useEffect, useState } from "react";
import { applyNodeChanges, Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, useUpdateNodeInternals, ViewportPortal } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import AddStep from "./AddStep";
import ChatTab from "./ChatTab";
import CornerBot from "./CornerBot";
import EvalTab from "./EvalTab";
import Intelligence from "./Intelligence";
import Settings from "./Settings";
import StepInspector from "./StepInspector";
import { bindStep, lanesFor, placeStep } from "./lanes";

const KEY = "demo-key";
const GROUP_ORDER = ["", "move", "fiat", "cards", "onboarding"];
const GROUP_LABEL = { "": "All", move: "Move", fiat: "Fiat", cards: "Cards", onboarding: "Onboarding" };

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

function NodeCard({ id, data }) {
  const update = useUpdateNodeInternals();
  useEffect(() => {
    update(id);
  }, [id, update]);
  const className = ["node", `kind-${data.kind}`, data.on ? "on" : "", data.picked ? "picked" : ""].filter(Boolean).join(" ");
  return (
    <div className={className}>
      <Handle type="target" position={Position.Top} />
      <div className="kind">{KIND_LABEL[data.kind] || data.kind}</div>
      <strong>{data.id}</strong>
      {data.purpose ? <div className="line">{data.purpose}</div> : null}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { card: NodeCard };

function portColor(port) {
  if (["false", "no", "reject"].includes(port)) return "#e11d48";
  if (["true", "yes", "continue", "approve"].includes(port)) return "#0f6e56";
  return "#b45309";
}

function groupedEdges(edges, nodes) {
  const steps = Object.fromEntries((nodes || []).map((node) => [node.id, node.data || {}]));
  const groups = new Map();
  (edges || []).forEach((edge) => {
    const key = `${edge.from}>${edge.to}`;
    const group = groups.get(key) || { ...edge, labels: [] };
    const options = steps[edge.from]?.options || [];
    const option = options.find((item) => item.port === edge.port);
    const label = option?.label || (edge.port === "continue" ? "continue" : edge.port);
    if (!group.labels.includes(label)) group.labels.push(label);
    groups.set(key, group);
  });
  return [...groups.values()].map((group) => ({ ...group, label: group.labels.filter((label) => label !== "continue").join(", ") || "continue" }));
}

function arrowPath(from, to) {
  const width = 220;
  const height = 96;
  const sameColumn = Math.abs(from.x - to.x) < 40;
  if (sameColumn) {
    const down = to.y > from.y;
    const startY = down ? from.y + height : from.y;
    const endY = down ? to.y : to.y + height;
    const adjacent = Math.abs(to.y - from.y) < 190;
    const center = from.x + width / 2;
    if (adjacent) {
      return { d: `M ${center} ${startY} L ${center} ${endY}`, labelX: center + 8, labelY: (startY + endY) / 2 };
    }
    const gutter = from.x + width + 16;
    return {
      d: `M ${center} ${startY} L ${gutter} ${startY} L ${gutter} ${endY} L ${to.x + width / 2} ${endY}`,
      labelX: gutter + 6,
      labelY: (startY + endY) / 2,
    };
  }
  const forward = to.x > from.x;
  const leaveX = forward ? from.x + width : from.x;
  const enterX = forward ? to.x : to.x + width;
  const leaveY = from.y + height / 2;
  const enterY = to.y + height / 2;
  if (Math.abs(from.y - to.y) < 24) {
    return { d: `M ${leaveX} ${leaveY} L ${enterX} ${enterY}`, labelX: (leaveX + enterX) / 2, labelY: leaveY - 8 };
  }
  const channel = Math.min(from.y, to.y) + height + 18;
  return {
    d: `M ${leaveX} ${leaveY} L ${leaveX + (forward ? 16 : -16)} ${leaveY} L ${leaveX + (forward ? 16 : -16)} ${channel} L ${enterX + (forward ? -16 : 16)} ${channel} L ${enterX + (forward ? -16 : 16)} ${enterY} L ${enterX} ${enterY}`,
    labelX: (leaveX + enterX) / 2,
    labelY: channel - 6,
  };
}

function GraphArrows({ edges, nodes }) {
  const at = Object.fromEntries((nodes || []).map((node) => [node.id, node.position || { x: 0, y: 0 }]));
  const lanes = lanesFor(nodes, edges);
  const height = Math.max(900, ...Object.values(at).map((pos) => pos.y + 220));
  const width = Math.max(1200, ...lanes.map((lane) => lane.x + 280));
  return (
    <ViewportPortal>
      <svg className="graph-arrows" width={width} height={height} style={{ position: "absolute", zIndex: 0 }}>
        <defs>
          {["#0f6e56", "#e11d48", "#b45309"].map((color) => (
            <marker key={color} id={`kai-${color.slice(1)}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={color} />
            </marker>
          ))}
        </defs>
        {lanes.map((lane) => (
          <g key={lane.title}>
            <rect x={lane.x} y="0" width="268" height={height} rx="12" fill={lane.fill} />
            <text x={lane.x + 14} y="22" fontSize="12" fontWeight="700" fill="#6b645c" letterSpacing="0.08em">{lane.title.toUpperCase()}</text>
          </g>
        ))}
        {groupedEdges(edges, nodes).map((edge) => {
          const from = at[edge.from];
          const to = at[edge.to];
          if (!from || !to) return null;
          const color = portColor(edge.port);
          const drawn = arrowPath(from, to);
          return (
            <g key={edge.id}>
              <path d={drawn.d} fill="none" stroke={color} strokeWidth="1.8" markerEnd={`url(#kai-${color.slice(1)})`} />
              <text x={drawn.labelX} y={drawn.labelY} fontSize="12" fontWeight="600" fill={color} stroke="#f6f3ee" strokeWidth="4" paintOrder="stroke">{edge.label}</text>
            </g>
          );
        })}
      </svg>
    </ViewportPortal>
  );
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Api-Key": KEY, ...(options.headers || {}) },
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toNodes(graph, trace, selectedId) {
  return (graph?.steps || []).map((step) => ({
    id: step.id,
    type: "card",
    position: step.position || { x: 0, y: 0 },
    width: 220,
    height: 96,
    measured: { width: 220, height: 96 },
    sourcePosition: "bottom",
    targetPosition: "top",
    selected: step.id === selectedId,
    data: { id: step.id, title: step.title, kind: step.kind, purpose: step.purpose || step.title, line: step.line, options: (step.input && step.input.options) || [], on: trace.includes(step.id), picked: step.id === selectedId },
  }));
}

function toEdges(graph) {
  return (graph?.edges || []).map((edge) => ({
    id: edge.id,
    source: edge.from,
    target: edge.to,
    label: edge.port,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: "#1c1915" },
    style: { stroke: "#1c1915", strokeWidth: 1.5 },
  }));
}

export default function App() {
  const [list, setList] = useState([]);
  const [current, setCurrent] = useState(null);
  const [tab, setTab] = useState("workflow");
  const [trace, setTrace] = useState([]);
  const [storyNote, setStoryNote] = useState(null);
  const [contract, setContract] = useState(null);
  const [tryResult, setTryResult] = useState(null);
  const [evalData, setEvalData] = useState(null);
  const [catalog, setCatalog] = useState({ types: [], untyped: [], steps: 0, workflows: 0 });
  const [area, setArea] = useState("workflows");
  const [selectedId, setSelectedId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [adding, setAdding] = useState(false);
  const [utterance, setUtterance] = useState("");
  const [runNote, setRunNote] = useState("");
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [focusWorkflow, setFocusWorkflow] = useState("");
  const [settings, setSettings] = useState(false);

  const graph = current?.draft?.graph;

  useEffect(() => {
    api("/v1/workflows").then(setList).catch(() => setList([]));
    api("/v1/step-types").then((data) => setCatalog(Array.isArray(data) ? { types: data, untyped: [] } : data)).catch(() => setCatalog({ types: [], untyped: [] }));
  }, []);

  const layoutStamp = JSON.stringify({
    id: current?.id,
    steps: (graph?.steps || []).map((step) => [step.id, step.position, step.purpose, step.line, step.kind]),
    edges: graph?.edges || [],
    trace,
  });
  useEffect(() => {
    setNodes((currentNodes) => {
      const fresh = toNodes(graph, trace, selectedId);
      const byId = Object.fromEntries(currentNodes.map((node) => [node.id, node]));
      const sameGraph = fresh.length === currentNodes.length && fresh.every((node) => byId[node.id]);
      if (!sameGraph) return fresh;
      return fresh.map((node) => ({ ...node, position: byId[node.id].position }));
    });
    setEdges(toEdges(graph));
  }, [layoutStamp]);
  useEffect(() => {
    setNodes((currentNodes) => currentNodes.map((node) => ({
      ...node,
      selected: node.id === selectedId,
      data: { ...node.data, picked: node.id === selectedId },
    })));
  }, [selectedId]);

  async function open(id) {
    const row = await api("/v1/workflows/" + id);
    setCurrent(row);
    setContract(row.draft.chat_contract);
    setTrace([]);
    setStoryNote(null);
    setTryResult(null);
    setSelectedId(null);
    setRunNote("");
    setTab("workflow");
    setSettings(false);
    setArea("workflows");
  }

  async function showStory(story) {
    const result = await api(`/v1/workflows/${current.id}/stories/${story.id}/trace`, { method: "POST" });
    setTrace(result.trace || []);
    setStoryNote(story);
  }

  async function saveDraft(body) {
    const row = await api("/v1/workflows/" + current.id + "/draft", { method: "PUT", body: JSON.stringify(body) });
    setCurrent(row);
    setContract(row.draft.chat_contract);
    setList(await api("/v1/workflows"));
    return row;
  }

  async function saveContract() {
    const lines = { ...(contract.lines || {}) };
    const choiceMap = contract.choices || {};
    const steps = (graph?.steps || []).map((step) => {
      let next = Object.prototype.hasOwnProperty.call(lines, step.id) ? { ...step, line: lines[step.id] } : step;
      if (choiceMap[step.id]) next = { ...next, config: { ...(next.config || {}), choices: choiceMap[step.id] } };
      return next;
    });
    const saved = { ...contract };
    delete saved.choices;
    await saveDraft({ chat_contract: saved, graph: { ...graph, steps } });
  }

  async function publish() {
    await api("/v1/workflows/" + current.id + "/publish", { method: "POST" });
    open(current.id);
  }

  async function runTry(text) {
    setTryResult(await api("/v1/workflows/" + current.id + "/try", { method: "POST", body: JSON.stringify({ text }) }));
    setTrace([]);
  }

  async function createWorkflow(event) {
    event.preventDefault();
    if (!newName.trim()) return;
    const row = await api("/v1/workflows", { method: "POST", body: JSON.stringify({ name: newName, template_id: templateId || undefined }) });
    setList(await api("/v1/workflows"));
    setCreating(false);
    setNewName("");
    setCurrent(row);
    setContract(row.draft.chat_contract);
    setTrace([]);
    setSelectedId(null);
    setTab("workflow");
    setSettings(false);
    setArea("workflows");
  }

  async function removeWorkflow() {
    await api("/v1/workflows/" + current.id, { method: "DELETE" });
    setCurrent(null);
    setList(await api("/v1/workflows"));
  }

  async function runWorkflow() {
    try {
      const result = await api("/v1/workflows/" + current.id + "/run", {
        method: "POST",
        body: JSON.stringify({ text: utterance, origin: "chat" }),
      });
      setTrace(result.trace || []);
      setTryResult(result);
      setRunNote(result.run_id ? `Live run ${result.run_id}` : result.error || result.status || "");
    } catch (err) {
      setRunNote(err.message);
    }
  }

  function optionEdges(stepId, input, edges) {
    const ports = (input?.options || []).map((option) => (typeof option === "string" ? "" : option.port)).filter(Boolean);
    if (!ports.length) return edges || [];
    const outgoing = (edges || []).filter((edge) => edge.from === stepId);
    const fallback = outgoing.find((edge) => edge.port === "continue") || outgoing[0];
    const next = [...(edges || [])];
    if (!fallback?.to) return next;
    ports.forEach((port) => {
      if (next.some((edge) => edge.from === stepId && edge.port === port)) return;
      next.push({ id: `${stepId}:${port}:${fallback.to}`, from: stepId, to: fallback.to, port });
    });
    return next;
  }

  async function saveStep(patch) {
    const current = (graph.steps || []).find((step) => step.id === selectedId);
    let nextPatch = patch;
    if (current && (current.kind === "question" || current.kind === "question_popup") && Object.prototype.hasOwnProperty.call(patch, "line") && !patch.input) {
      nextPatch = { ...patch, input: { ...(current.input || {}), ask: patch.line || "" } };
    }
    const steps = graph.steps.map((step) => (step.id === selectedId ? { ...step, ...nextPatch } : step));
    const lines = { ...(contract?.lines || {}) };
    if (Object.prototype.hasOwnProperty.call(nextPatch, "line")) lines[selectedId] = nextPatch.line || "";
    const edges = nextPatch.input ? optionEdges(selectedId, nextPatch.input, graph.edges) : graph.edges;
    await saveDraft({ graph: { ...graph, steps, edges }, chat_contract: { ...contract, lines } });
  }

  async function savePositions(nextNodes) {
    const at = Object.fromEntries(nextNodes.map((node) => [node.id, node.position]));
    const steps = graph.steps.map((step) => ({ ...step, position: at[step.id] || step.position }));
    await saveDraft({ graph: { ...graph, steps } });
  }

  function connectPort(port, target) {
    const edgesNext = (graph.edges || []).filter((edge) => !(edge.from === selectedId && edge.port === port));
    if (target) edgesNext.push({ id: `${selectedId}:${port}:${target}`, from: selectedId, to: target, port });
    saveDraft({ graph: { ...graph, edges: edgesNext } });
  }

  function deleteStep() {
    const steps = graph.steps.filter((step) => step.id !== selectedId);
    const edgesNext = graph.edges.filter((edge) => edge.from !== selectedId && edge.to !== selectedId);
    const entries = Object.fromEntries(Object.entries(graph.entries || {}).filter(([, stepId]) => stepId !== selectedId && steps.some((step) => step.id === stepId)));
    setSelectedId(null);
    saveDraft({ graph: { ...graph, steps, edges: edgesNext, entries } });
  }

  function addFromTemplate(type) {
    if (!type || !graph) return;
    const steps = graph.steps || [];
    const id = `${type.slug}-${steps.length + 1}`;
    const step = {
      id,
      kind: type.kind,
      type: type.type,
      title: type.title,
      purpose: type.purpose,
      line: null,
      position: placeStep(steps, type.kind),
      config: { role: type.type },
      input: JSON.parse(JSON.stringify(type.input || {})),
      output: JSON.parse(JSON.stringify(type.output || {})),
    };
    const anchor = steps.find((item) => item.id === selectedId) || steps[steps.length - 1] || null;
    const bound = bindStep(graph, step, anchor, type.ports || []);
    saveDraft({ graph: { ...graph, ...bound } });
    setSelectedId(id);
    setAdding(false);
  }

  async function loadEval() {
    setTab("eval");
    setEvalData(await api("/v1/workflows/" + current.id + "/eval"));
  }

  const grouped = GROUP_ORDER.map((group) => ({
    group,
    items: list.filter((item) => (item.group || "") === group),
  })).filter((row) => row.items.length);
  const hasChat = (current?.channels || []).includes("chat");
  const selected = (graph?.steps || []).find((step) => step.id === selectedId);
  const stepType = (catalog.types || []).find((item) => item.type === selected?.type);
  const showLiveTrace = useCallback((workflowId, next) => {
    if (workflowId !== current?.id) return;
    const path = next || [];
    setTrace((currentTrace) => {
      const same = currentTrace.length === path.length && currentTrace.every((step, index) => step === path[index]);
      return same ? currentTrace : path;
    });
    setStoryNote(null);
  }, [current?.id]);

  return (
    <div className="app">
      <div className="product">
        <strong>Kai</strong>
        <button className={area === "workflows" ? "on" : ""} onClick={() => { setArea("workflows"); setSettings(false); }}>Workflows</button>
        <button className={area === "intelligence" ? "on" : ""} onClick={() => setArea("intelligence")}>User intelligence</button>
      </div>
      {area === "intelligence" ? (
        <Intelligence onOpenWorkflow={(id) => { setArea("workflows"); open(id); }} focusWorkflow={focusWorkflow} />
      ) : (
      <div className="workspace">
      <nav>
        <h1>Workflows</h1>
        <button className="item" onClick={() => setCreating((open) => !open)}>+ Add workflow</button>
        {creating ? (
          <form className="new-workflow" onSubmit={createWorkflow}>
            <input value={newName} placeholder="Name" onChange={(event) => setNewName(event.target.value)} />
            <select value={templateId} onChange={(event) => setTemplateId(event.target.value)}>
              <option value="">Four-lane template</option>
              {list.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <button type="submit">Create</button>
          </form>
        ) : null}
        <button className={current ? "item" : "item active"} onClick={() => { setCurrent(null); setSettings(false); }}>All · {list.length}</button>
        {grouped.map((row) => (
          <div key={row.group || "all"}>
            <div className="group">{GROUP_LABEL[row.group] || row.group}</div>
            {row.items.map((item) => (
              <button key={item.id} className={current?.id === item.id ? "item active" : "item"} onClick={() => open(item.id)}>
                {item.name}
                <small>{(item.channels || []).map((name) => name.charAt(0).toUpperCase() + name.slice(1)).join(" · ") || "Workflow"} · {item.steps} steps</small>
              </button>
            ))}
          </div>
        ))}
        <div className="group">Settings</div>
        <button className={settings ? "item active" : "item"} onClick={() => { setSettings(true); setCurrent(null); }}>
          Step types
          <small>{(catalog.types || []).length} types · {(catalog.kinds || []).length || 9} kinds</small>
        </button>
      </nav>
      <main>
        {settings ? <Settings catalog={catalog} onOpen={(id) => open(id)} /> : !current ? <div className="panel">Choose a workflow. The chat page at /chat runs the published version.</div> : (
          <>
            <header className="workflow-head">
              <p className="crumb">User intelligence / Workflows / {current.name}</p>
              <h1>{current.name}</h1>
              <p className="summary">{current.summary}</p>
              <p className="hint">{current.steps} steps · {current.built_from ? "built from the template" : `${current.stories} stories`}</p>
              {current.draft_ahead ? <p className="hint">Chat follows this draft, including the question you just edited. Start a New chat after you edit.</p> : null}
              <div className="head-actions">
                {hasChat ? <a href={`/chat?workflow=${current.id}`}>Open chat</a> : null}
                {current.built_from ? <button type="button" className="danger" onClick={removeWorkflow}>Delete workflow</button> : null}
              </div>
            </header>
            <div className="tabs">
              <button className={tab === "workflow" ? "on" : ""} onClick={() => setTab("workflow")}>Workflow</button>
              {hasChat ? <button className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>Chat</button> : null}
              <button className={tab === "eval" ? "on" : ""} onClick={loadEval}>Eval</button>
              <button onClick={publish}>Publish</button>
            </div>
            {tab === "workflow" && (
              <div className="canvas">
                <div className="canvas-main">
                  <div className="prompts runbar">
                    <button type="button" onClick={() => setAdding(true)}>Add step</button>
                    <input value={utterance} placeholder="What should this run hear?" onChange={(event) => setUtterance(event.target.value)} />
                    <button onClick={runWorkflow}>Run</button>
                  </div>
                  {runNote ? <div className="run-note">{runNote}</div> : null}
                  {tryResult?.messages?.length ? (
                    <div className="run-note">{tryResult.messages.map((message) => message.text).filter(Boolean).join(" ")}</div>
                  ) : null}
                  <div className="flow">
                    <ReactFlow
                      key={current.id}
                      nodes={nodes}
                      edges={edges}
                      nodeTypes={nodeTypes}
                      minZoom={0.25}
                      maxZoom={1.75}
                      zoomOnScroll
                      zoomOnPinch
                      zoomOnDoubleClick
                      preventScrolling
                      onlyRenderVisibleElements={false}
                      nodesDraggable
                      panOnDrag={[1, 2]}
                      onNodesChange={(changes) => {
                        setNodes((currentNodes) => {
                          const next = applyNodeChanges(changes, currentNodes);
                          if (changes.some((change) => change.type === "position" && change.dragging === false)) savePositions(next);
                          return next;
                        });
                      }}
                      onInit={(instance) => { requestAnimationFrame(() => instance.setViewport({ x: 16, y: 12, zoom: 0.85 })); }}
                      onNodeClick={(_, node) => setSelectedId(node.id)}
                      onPaneClick={() => setSelectedId(null)}
                    >
                      <GraphArrows edges={graph.edges} nodes={nodes} />
                      <Background />
                      <Controls position="bottom-left" />
                      <MiniMap position="bottom-right" pannable zoomable />
                    </ReactFlow>
                  </div>
                </div>
                {selected ? (
                  <StepInspector
                    step={selected}
                    stepType={stepType}
                    steps={graph?.steps || []}
                    edges={graph?.edges || []}
                    slots={contract?.slots || []}
                    contract={contract}
                    onClose={() => setSelectedId(null)}
                    onSave={saveStep}
                    onSaveContract={(patch) => saveDraft({ chat_contract: { ...contract, ...patch } })}
                    onEditChat={() => setTab(hasChat ? "chat" : "workflow")}
                    onConnect={connectPort}
                    onDelete={deleteStep}
                  />
                ) : !(graph?.steps || []).length ? (
                  <aside className="rail">
                    <h2>Build it on the canvas</h2>
                    <p>Use Add step. Start in the Source lane with a chat request or a rule. The next template lands after that step, with its input and output.</p>
                  </aside>
                ) : (
                  <aside className="rail">
                    <h2>Stories</h2>
                    <p className="hint">Every way this can end. Pick one to trace it on the canvas.</p>
                    {(current.draft.stories || []).map((story) => (
                      <button key={story.id} className={storyNote?.id === story.id ? "story on" : "story"} onClick={() => showStory(story)}>
                        <strong>{story.name}</strong>
                        <span>{story.blurb}</span>
                        <small>{story.expected_steps} steps</small>
                      </button>
                    ))}
                  </aside>
                )}
              </div>
            )}
            {tab === "chat" && hasChat ? (
              <ChatTab contract={contract} graph={graph} onChange={setContract} onSave={saveContract} onTry={runTry} tryResult={tryResult} />
            ) : null}
            {tab === "eval" ? (
              <EvalTab data={evalData} onOpenRuns={() => { setFocusWorkflow(current.id); setArea("intelligence"); }} />
            ) : null}
          </>
        )}
        {adding && current ? (
          <AddStep
            types={catalog.types || []}
            steps={graph?.steps || []}
            anchorId={selectedId}
            onAdd={addFromTemplate}
            onClose={() => setAdding(false)}
          />
        ) : null}
      </main>
      </div>
      )}
        <CornerBot
        workflow={current}
        onTrace={showLiveTrace}
      />
    </div>
  );
}
