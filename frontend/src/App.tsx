import { useState } from "react";
import Prepare from "./pages/Prepare";
import InterviewRoom from "./pages/InterviewRoom";
import Report from "./pages/Report";

type Page =
  | { name: "prepare" }
  | { name: "room"; iid: string; jdTitle: string }
  | { name: "report"; iid: string };

const STEP_ORDER: Array<Page["name"]> = ["prepare", "room", "report"];
const STEP_LABELS = ["准备", "面试", "报告"];

export default function App() {
  const [page, setPage] = useState<Page>({ name: "prepare" });
  const currentIdx = STEP_ORDER.indexOf(page.name);

  return (
    <div className="shell">
      <header className="masthead">
        <div className="brand">
          <span className="no">ITW / 面试评估</span>
          <h1>AI 面试评估台</h1>
        </div>
        <nav className="steps" aria-label="流程">
          {STEP_LABELS.map((label, i) => (
            <span key={label} className={i === currentIdx ? "now" : i < currentIdx ? "past" : ""}>
              {label}
            </span>
          ))}
        </nav>
      </header>

      <main>
        {page.name === "prepare" && (
          <Prepare
            onEnterRoom={(iid, jdTitle) => setPage({ name: "room", iid, jdTitle })}
          />
        )}
        {page.name === "room" && (
          <InterviewRoom
            key={page.iid}
            iid={page.iid}
            jdTitle={page.jdTitle}
            onFinished={(iid) => setPage({ name: "report", iid })}
          />
        )}
        {page.name === "report" && (
          <Report iid={page.iid} onRestart={() => setPage({ name: "prepare" })} />
        )}
      </main>

      <footer className="colophon">
        <span>评价闭环：追问由上一轮评价驱动，报告由全程证据汇总。</span>
        <span>演示环境数据均为虚构。</span>
      </footer>
    </div>
  );
}
