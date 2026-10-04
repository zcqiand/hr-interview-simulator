import { useEffect, useRef, useState } from "react";
import { fetchInterview, finishInterview, startInterview, streamReply } from "../api";
import { DIMENSIONS, PHASES } from "../types";
import type { EvalRow } from "../types";

interface Turn {
  kind: "q" | "a" | "sys";
  text: string;
  n?: number; // Q 编号（第几问）
}

export default function InterviewRoom({
  iid,
  jdTitle,
  onFinished,
}: {
  iid: string;
  jdTitle: string;
  onFinished: (iid: string) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [evals, setEvals] = useState<EvalRow[]>([]);
  const [phase, setPhase] = useState("opening");
  const [round, setRound] = useState(0);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const draftRef = useRef(""); // 正在流式累积的下一问（eval 前的过程话会被丢弃）
  const [draftView, setDraftView] = useState(""); // ref 的渲染镜像，驱动重渲染
  const qCountRef = useRef(0);
  const initedRef = useRef(false);

  useEffect(() => {
    if (initedRef.current) return;
    initedRef.current = true;
    (async () => {
      try {
        const detail = await fetchInterview(iid);
        if (detail.interview.status !== "live") {
          onFinished(iid);
          return;
        }
        const restored: Turn[] = [];
        for (const m of detail.messages) {
          if (m.role === "assistant") {
            qCountRef.current += 1;
            restored.push({ kind: "q", text: m.content, n: qCountRef.current });
          } else if (m.role === "user") {
            restored.push({ kind: "a", text: m.content });
          }
        }
        setTurns(restored);
        setEvals(detail.evals);
        setPhase(detail.interview.phase);
        setRound(detail.interview.round);
        if (!restored.some((t) => t.kind === "q")) {
          const first = await startInterview(iid);
          qCountRef.current += 1;
          setTurns((t) => [...t, { kind: "q", text: first.question, n: qCountRef.current }]);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [iid, onFinished]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError("");
    setBusy(true);
    draftRef.current = "";
    setTurns((t) => [...t, { kind: "a", text }]);
    try {
      await streamReply(iid, text, (ev) => {
        switch (ev.type) {
          case "session":
            setRound(ev.round);
            break;
          case "delta":
            draftRef.current += ev.text;
            setDraftView(draftRef.current);
            break;
          case "eval":
            draftRef.current = ""; // 评价前的过程话丢弃，只留下一问
            setDraftView("");
            setEvals((list) => [...list, ev.eval]);
            break;
          case "stage": {
            setPhase(ev.phase);
            const label = PHASES.find((p) => p.key === ev.phase)?.label ?? ev.phase;
            setTurns((t) => [
              ...t,
              { kind: "sys", text: `进入 ${label}${ev.forced ? "（本阶段问满，自动推进）" : ""}` },
            ]);
            break;
          }
          case "done":
            setBusy(false);
            if (ev.status === "ended") {
              if (draftRef.current.trim()) {
                setTurns((t) => [...t, { kind: "q", text: draftRef.current.trim() }]);
              }
              onFinished(iid);
            } else if (draftRef.current.trim()) {
              // 下一问固化
              qCountRef.current += 1;
              const q = draftRef.current.trim();
              const n = qCountRef.current;
              setTurns((t) => [...t, { kind: "q", text: q, n }]);
            }
            draftRef.current = "";
            setDraftView("");
            break;
          case "error":
            setBusy(false);
            setError(ev.message);
            draftRef.current = "";
            setDraftView("");
            break;
          default:
            break;
        }
      });
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function endNow() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await finishInterview(iid);
      onFinished(iid);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const phaseIdx = PHASES.findIndex((p) => p.key === phase);

  return (
    <>
      <div className="phase-rail" aria-label="面试阶段">
        {PHASES.map((p, i) => (
          <span key={p.key} className={i === phaseIdx ? "now" : i < phaseIdx ? "done" : ""}>
            {p.label}
          </span>
        ))}
      </div>

      <div className="room">
        <div>
          <div className="protocol">
            <p className="note-strip">
              第 {round} 轮 · {jdTitle || "面试进行中"} —— 面试官会根据你上一轮的表现追问。
            </p>
            {turns.map((t, i) => (
              <div key={i} className={`turn ${t.kind}`}>
                <div className="who">
                  {t.kind === "q" ? `INTERVIEWER · Q${t.n ?? ""}` : t.kind === "a" ? "CANDIDATE" : "STAGE"}
                </div>
                <div className="body">{t.text}</div>
              </div>
            ))}
            {busy && draftView.trim() && (
              <div className="turn q">
                <div className="who">INTERVIEWER</div>
                <div className="body typing">{draftView.trim()}</div>
              </div>
            )}
          </div>

          {error && <div className="error-strip">{error}</div>}

          <div className="composer">
            <textarea
              value={input}
              disabled={busy}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={busy ? "面试官正在处理…" : "回答当前问题（Enter 发送，Shift+Enter 换行）"}
            />
            <div className="row">
              <button className="btn ghost" disabled={busy} onClick={endNow}>
                结束并生成报告
              </button>
              <button className="btn accent" disabled={busy || !input.trim()} onClick={send}>
                发送回答
              </button>
            </div>
          </div>
        </div>

        <aside className="ledger">
          <h3>面试官记录</h3>
          {evals.length === 0 && (
            <p className="empty">每轮回答后，面试官的即时评价（四个维度 + 原话证据）会记在这里。</p>
          )}
          {evals.map((ev, i) => (
            <div key={i} className="eval-card">
              <div className="head">
                <span className="r">第 {ev.round} 轮</span>
                <span>{PHASES.find((p) => p.key === ev.phase)?.label ?? ev.phase}</span>
              </div>
              <div className="score-mini">
                {DIMENSIONS.map((d) => (
                  <span key={d.key}>
                    <span className="k">{d.label}</span>{" "}
                    <span className="v">{ev.scores[d.key] ?? "-"}</span>
                  </span>
                ))}
              </div>
              {ev.evidence && <div className="evidence">“{ev.evidence}”</div>}
              {ev.comment && <div className="comment">{ev.comment}</div>}
            </div>
          ))}
        </aside>
      </div>
    </>
  );
}
