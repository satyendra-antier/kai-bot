function whole(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return Math.abs(number - Math.round(number)) < 0.001 ? String(Math.round(number)) : String(number);
}

function CheckTable({ title, note, rows }) {
  const weight = (rows || []).reduce((sum, row) => sum + Number(row.weight || 0), 0);
  return (
    <section className="eval-group">
      <h2>{title}<small>weights {whole(weight)} / 100</small></h2>
      <p className="hint">{note}</p>
      <table>
        <thead>
          <tr><th>Check</th><th>Weight</th><th>How it’s measured</th><th>Target</th><th>Recent runs</th></tr>
        </thead>
        <tbody>
          {(rows || []).map((row) => (
            <tr key={row.key}>
              <td><b>{row.name}</b><small>{row.mode}</small></td>
              <td>{whole(row.weight)}</td>
              <td>{row.measure}</td>
              <td>{row.average == null ? "—" : `${row.average}%`}</td>
              <td>{row.of ? (row.lost ? `lost points on ${row.lost} of ${row.of}` : `full on all ${row.of}`) : "—"}{(row.runs || []).length ? <small>{row.runs.join(", ")}</small> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function EvalTab({ data, onOpenRuns }) {
  return (
    <div className="panel eval-tab">
      <h2>Pass bar</h2>
      <p>A run is flagged when either score is under the bar, or any guardrail is broken.</p>
      <div className="scores">
        <div className="score">Runs<b>{data?.runs ?? "—"}</b></div>
        <div className="score">Chat<b>{whole(data?.chat)}</b></div>
        <div className="score">Outcome<b>{whole(data?.outcome)}</b></div>
        <div className="score">Flagged<b>{data?.flagged ?? "—"}</b></div>
      </div>
      <button type="button" className="link" onClick={onOpenRuns}>Open in Runs ↗</button>
      <CheckTable title="Chat checks" note="Only on runs that started in chat. Did Kai understand, ask the right things, say the lines, and keep the guardrails?" rows={data?.chat_checks} />
      <CheckTable title="Outcome checks" note="On every run. Did it end where it should, quickly, with the customer still there and nothing retried?" rows={data?.outcome_checks} />
    </div>
  );
}
