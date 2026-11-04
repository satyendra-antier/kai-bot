export const LANES = {
  source: { id: "source", title: "Source", x: 8, fill: "#f5edff" },
  continue: { id: "continue", title: "Continue / true", x: 284, fill: "#eef8f2" },
  question: { id: "question", title: "Questions", x: 284, fill: "#eef8f2" },
  approve: { id: "approve", title: "Approve", x: 560, fill: "#fff8e8" },
  response: { id: "response", title: "User response", x: 560, fill: "#fff8e8" },
  reject: { id: "reject", title: "False / reject", x: 836, fill: "#fff1f2" },
  sumsub: { id: "sumsub", title: "Sumsub", x: 836, fill: "#f3f0ff" },
  score: { id: "score", title: "Score", x: 1112, fill: "#eef6ff" },
  stop: { id: "stop", title: "Stops", x: 1388, fill: "#fff1f2" },
};

const ONBOARD_KINDS = new Set(["question", "question_popup", "user_response", "user_response_popup", "sumsub", "score"]);
const FAIL = new Set(["false", "no", "reject"]);

export function isOnboarding(steps) {
  return (steps || []).some((step) => ONBOARD_KINDS.has(step.kind || step.data?.kind));
}

export function laneOf(kind, incomingPorts = [], onboarding = false) {
  if (onboarding) {
    if (kind === "source" || kind === "trigger") return "source";
    if (kind === "question" || kind === "question_popup") return "question";
    if (kind === "user_response" || kind === "user_response_popup") return "response";
    if (kind === "sumsub") return "sumsub";
    if (kind === "score") return "score";
    if (kind === "stop") return "stop";
    return "question";
  }
  if (kind === "source" || kind === "trigger") return "source";
  if (kind === "approval") return "approve";
  if ((kind === "result" || kind === "stop") && incomingPorts.length && incomingPorts.every((port) => FAIL.has(port))) return "reject";
  return "continue";
}

export function lanesFor(nodes, edges) {
  const steps = (nodes || []).map((node) => ({ id: node.id, kind: node.data?.kind || node.kind }));
  const onboarding = isOnboarding(steps);
  const incoming = {};
  (edges || []).forEach((edge) => {
    incoming[edge.to] = incoming[edge.to] || [];
    incoming[edge.to].push(edge.port);
  });
  const used = new Set(steps.map((step) => laneOf(step.kind, incoming[step.id] || [], onboarding)));
  return [...used].map((id) => LANES[id]).filter(Boolean).sort((a, b) => a.x - b.x);
}

export function placeStep(steps, kind) {
  const onboarding = isOnboarding(steps);
  const lane = LANES[laneOf(kind, [], onboarding)] || LANES.continue;
  const x = lane.x + 16;
  const taken = (steps || []).filter((step) => Math.abs((step.position?.x || 0) - x) < 120);
  const y = taken.length ? Math.max(...taken.map((step) => step.position?.y || 0)) + 140 : 48;
  return { x, y };
}

export function chainStep(steps, edges, stepId, ports) {
  const next = (edges || []).map((edge) => ({ ...edge }));
  const outgoing = new Map();
  next.forEach((edge) => {
    const list = outgoing.get(edge.from) || [];
    list.push(edge);
    outgoing.set(edge.from, list);
  });
  const ends = new Set((steps || []).filter((step) => step.kind === "result" || step.kind === "stop").map((step) => step.id));
  const beforeEnd = [...(steps || [])].reverse().find((step) => {
    const outs = outgoing.get(step.id) || [];
    return outs.length === 1 && outs[0].port === "continue" && ends.has(outs[0].to);
  });
  const port = (ports || []).includes("continue") ? "continue" : (ports || [])[0] || "continue";
  if (beforeEnd) {
    const edge = outgoing.get(beforeEnd.id)[0];
    const oldTo = edge.to;
    edge.to = stepId;
    edge.id = `${beforeEnd.id}:continue:${stepId}`;
    next.push({ id: `${stepId}:${port}:${oldTo}`, from: stepId, to: oldTo, port });
    return next;
  }
  const open = [...(steps || [])].reverse().find((step) => !((outgoing.get(step.id) || []).some((edge) => edge.port === "continue")));
  if (open) next.push({ id: `${open.id}:continue:${stepId}`, from: open.id, to: stepId, port: "continue" });
  return next;
}

export function bindStep(graph, step, anchor, ports) {
  const steps = [...(graph?.steps || []), step];
  const edges = (graph?.edges || []).map((edge) => ({ ...edge }));
  const entries = { ...(graph?.entries || {}) };
  if (step.kind === "source") {
    if (step.type === "rule") {
      if (!entries.rule) entries.rule = step.id;
    } else if (!entries.chat) {
      entries.chat = step.id;
    }
    return { steps, edges, entries };
  }
  if (!anchor) return { steps, edges, entries };
  const outPort = (ports || []).includes("continue") ? "continue" : ((ports || [])[0] || "continue");
  const existing = edges.find((edge) => edge.from === anchor.id && edge.port === "continue");
  if (existing && existing.to !== step.id) {
    const oldTo = existing.to;
    existing.to = step.id;
    existing.id = `${anchor.id}:continue:${step.id}`;
    edges.push({ id: `${step.id}:${outPort}:${oldTo}`, from: step.id, to: oldTo, port: outPort });
  } else if (!existing) {
    edges.push({ id: `${anchor.id}:continue:${step.id}`, from: anchor.id, to: step.id, port: "continue" });
  }
  return { steps, edges, entries };
}
