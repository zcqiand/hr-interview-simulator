import { useEffect, useRef, useState } from "react";
import { fetchReport } from "../api";
import { DIMENSIONS } from "../types";
import type { Report } from "../types";

const GRADE_CLASS: Record<string, string> = { 强: "good", 中: "mid", 弱: "weak" };

export default function Report({ iid, onRestart }: { iid: string; onRestart: () => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const initedRef = useRef(false);

  useEffect(() => {
    if (initedRef.current) return;
    initedRef.current = true;
    fetchReport(iid)
      .then(setReport)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [iid]);

  if (error) {
    return (
      <>
        <div className="error-strip">{error}</div>
        <div className="actions">
          <button className="btn ghost" onClick={onRestart}>
            返回准备页
          </button>
        </div>
      </>
    );
  }

  if (!report) return <p className="note-strip">报告生成中…</p>;

  const today = new Date().toLocaleDateString("zh-CN");

  return (
    <>
      <div className="doc-head">
        <div className="cell">
          <div className="k">评估编号</div>
          <div className="v mono">{iid}</div>
        </div>
        <div className="cell">
          <div className="k">候选人</div>
          <div className="v">{report.candidate_name}</div>
        </div>
        <div className="cell">
          <div className="k">作答轮次</div>
          <div className="v mono">{report.rounds}</div>
        </div>
        <div className="cell">
          <div className="k">日期</div>
          <div className="v mono">{today}</div>
        </div>
      </div>

      <section className="section">
        <div className="section-head">
          <span className="idx">01</span>
          <h2>总评</h2>
        </div>
        <blockquote className="summary-quote">{report.summary}</blockquote>
        <div>
          {report.dimensions.map((d) => (
            <div key={d.key} className="dim-row">
              <span>{d.label}</span>
              <span className="avg">
                {d.avg.toFixed(1)}
                <small> /5</small>
              </span>
              <span className={`dim-bar grade-${GRADE_CLASS[d.verdict] ?? "mid"}`}>
                <i style={{ width: `${(d.avg / 5) * 100}%` }} />
              </span>
              <span className="grade">
                {d.verdict === "-" ? (
                  <span className="stamp" style={{ color: "var(--ink-3)" }}>
                    无数据
                  </span>
                ) : (
                  <span className={`stamp ${GRADE_CLASS[d.verdict] ?? ""}`}>{d.verdict}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <span className="idx">02</span>
          <h2>改进建议</h2>
        </div>
        <ol className="suggestions">
          {report.suggestions.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      </section>

      <section className="section">
        <div className="section-head">
          <span className="idx">03</span>
          <h2>逐轮明细</h2>
          <span className="hint">判定均可回指到当轮证据</span>
        </div>
        {report.detail.map((d, i) => (
          <div key={i} className="round-detail">
            <div className="rd-head">
              <span>ROUND {String(d.round).padStart(2, "0")}</span>
              <span>{DIMENSIONS.map((dim) => `${dim.label.slice(0, 2)} ${d.scores[dim.key] ?? "-"}`).join(" · ")}</span>
            </div>
            <div className="rd-q">「{d.question || "（题面未记录）"}」</div>
            <div className="rd-a">{d.answer}</div>
            {d.evidence && (
              <div className="rd-evidence">
                <span className="tag">EVIDENCE · 原话</span>“{d.evidence}”
              </div>
            )}
            {d.comment && <p className="rd-comment">{d.comment}</p>}
          </div>
        ))}
      </section>

      <div className="actions">
        <button className="btn accent" onClick={onRestart}>
          再练一场
        </button>
      </div>
    </>
  );
}
