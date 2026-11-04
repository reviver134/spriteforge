import type { AgentReport } from "./api";

const STAGE_TEXT: Record<AgentReport["stage"], string> = {
  entry: "Plan · before I start",
  exit: "Report · after my work",
};

/** An agent's plan before it starts (entry gate, steps, exit gate) or its report after it
 * worked (what it did, its exit gate checked by code), point by point. */
export function ReportCard({ report }: { report: AgentReport }) {
  return (
    <section className={`report-card report-${report.stage}`} aria-label={report.title}>
      <header>
        <h3>{report.title}</h3>
        <span className="report-kind">{STAGE_TEXT[report.stage]}</span>
      </header>
      {report.sections.map((s) => {
        const List = s.numbered ? "ol" : "ul";
        return (
          <div key={s.title} className="report-section">
            <h4>{s.title}</h4>
            <List>
              {s.points.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </List>
          </div>
        );
      })}
    </section>
  );
}
