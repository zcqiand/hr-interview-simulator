import { useState } from "react";
import {
  createInterview,
  createJd,
  createMatch,
  createResumeText,
  startInterview,
  uploadResumePdf,
} from "../api";
import type { Candidate, Jd, MatchResult } from "../types";

const VERDICT_LABEL: Record<string, { text: string; mark: string }> = {
  match: { text: "符合", mark: "●" },
  partial: { text: "部分符合", mark: "◐" },
  miss: { text: "不符", mark: "○" },
};

interface Prepared {
  candidate: Candidate;
  jd: Jd;
  match: MatchResult;
}

export default function Prepare({ onEnterRoom }: { onEnterRoom: (iid: string, jdTitle: string) => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"text" | "pdf">("text");
  const [resumeText, setResumeText] = useState("");
  const [pdf, setPdf] = useState<File | null>(null);
  const [jdTitle, setJdTitle] = useState("");
  const [company, setCompany] = useState("");
  const [jdText, setJdText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Prepared | null>(null);

  async function run() {
    setError("");
    if (!name.trim()) return setError("请填写姓名。");
    if (mode === "text" && resumeText.trim().length < 20) return setError("简历文本太短（至少 20 字）。");
    if (mode === "pdf" && !pdf) return setError("请选择简历 PDF 文件。");
    if (!jdTitle.trim()) return setError("请填写岗位名称。");
    if (!jdText.trim()) return setError("请粘贴 JD 原文。");
    setBusy(true);
    try {
      const jd = await createJd(jdTitle.trim(), company.trim(), jdText.trim());
      const cand =
        mode === "pdf"
          ? await uploadResumePdf(pdf!, name.trim(), jd.id)
          : await createResumeText(name.trim(), resumeText.trim(), jd.id);
      const match = await createMatch(cand.id, jd.id);
      setResult({ candidate: cand, jd, match });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function enterRoom() {
    if (!result) return;
    setBusy(true);
    try {
      const iv = await createInterview(result.candidate.id, result.jd.id, result.match.id);
      await startInterview(iv.id); // 幂等：返回开场题
      onEnterRoom(iv.id, result.jd.title);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <>
      <section className="section">
        <div className="section-head">
          <span className="idx">01</span>
          <h2>候选人简历</h2>
          <span className="hint">解析在本地服务完成，不出本机</span>
        </div>
        <label className="field">姓名</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：张三（演示请用虚构姓名）"
        />

        <label className="field">简历</label>
        <div className="mode-toggle">
          <button className={mode === "text" ? "on" : ""} onClick={() => setMode("text")}>
            粘贴文本
          </button>
          <button className={mode === "pdf" ? "on" : ""} onClick={() => setMode("pdf")}>
            上传 PDF
          </button>
        </div>
        {mode === "text" ? (
          <textarea
            value={resumeText}
            onChange={(e) => setResumeText(e.target.value)}
            placeholder="把简历全文粘贴到这里：技能、经历、项目、教育……"
          />
        ) : (
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setPdf(e.target.files?.[0] ?? null)}
          />
        )}
        {mode === "pdf" && (
          <p className="note-strip">加密或扫描版 PDF（无文本层）无法解析，会明确报错——请改用文本版。</p>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <span className="idx">02</span>
          <h2>目标岗位 JD</h2>
        </div>
        <div className="form-grid">
          <div>
            <label className="field">岗位名称</label>
            <input
              type="text"
              value={jdTitle}
              onChange={(e) => setJdTitle(e.target.value)}
              placeholder="例如：Python 后端工程师"
            />
          </div>
          <div>
            <label className="field">公司（可选）</label>
            <input
              type="text"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="例如：虚构科技"
            />
          </div>
          <div className="full">
            <label className="field">JD 原文</label>
            <textarea
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
              placeholder="粘贴招聘 JD：职责、任职要求、加分项……"
            />
          </div>
        </div>
      </section>

      {error && <div className="error-strip">{error}</div>}

      <div className="actions">
        <button className="btn accent" disabled={busy} onClick={run}>
          {busy ? "解析中…" : "解析简历并匹配岗位"}
        </button>
      </div>

      {result && (
        <>
          <section className="section">
            <div className="section-head">
              <span className="idx">03</span>
              <h2>匹配结果</h2>
              <span className="hint">
                {result.jd.title}
                {result.jd.company ? ` · ${result.jd.company}` : ""}
              </span>
            </div>
            <div className="score-line">
              <span className="score">{result.match.total_score}</span>
              <span className="of">/ 100（必须项 90 + 加分项 10）</span>
            </div>
            <div className="chips">
              {result.candidate.profile.skills.map((s) => (
                <span key={s} className="chip">
                  {s}
                </span>
              ))}
            </div>
            <table className="sheet">
              <thead>
                <tr>
                  <th>要求</th>
                  <th>类别</th>
                  <th>判定</th>
                  <th>依据</th>
                </tr>
              </thead>
              <tbody>
                {result.match.items.map((it, i) => {
                  const v = VERDICT_LABEL[it.verdict] ?? VERDICT_LABEL.miss;
                  return (
                    <tr key={i}>
                      <td>{it.requirement}</td>
                      <td>
                        <span className="kind-tag">{it.kind === "nice" ? "加分" : "必须"}</span>
                      </td>
                      <td className={`verdict ${it.verdict}`}>
                        <span className="dot">{v.mark}</span> {v.text}
                      </td>
                      <td>{it.note}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          <div className="actions">
            <button className="btn accent" disabled={busy} onClick={enterRoom}>
              进入面试间
            </button>
          </div>
        </>
      )}
    </>
  );
}
