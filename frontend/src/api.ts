/** API 封装：全部同源相对路径（vite proxy → 8802），无绝对 baseURL。 */
import type {
  Candidate,
  InterviewDetail,
  Jd,
  MatchResult,
  Report,
  SseEvent,
} from "./types";

async function readError(res: Response): Promise<string> {
  const detail = await res
    .json()
    .then((d) => d?.detail)
    .catch(() => null);
  return typeof detail === "string" ? detail : `请求失败 (${res.status})`;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

// ---- 准备页 ----

export function createJd(title: string, company: string, jdText: string): Promise<Jd> {
  return postJSON("/api/jds", { title, company, jd_text: jdText });
}

export function createResumeText(name: string, text: string, jdId?: string): Promise<Candidate> {
  return postJSON("/api/resumes/text", { name, text, jd_id: jdId ?? null });
}

export function uploadResumePdf(file: File, name: string, jdId?: string): Promise<Candidate> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  if (jdId) form.append("jd_id", jdId);
  return postForm("/api/resumes", form);
}

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", body: form });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export function createMatch(candidateId: string, jdId: string): Promise<MatchResult> {
  return postJSON("/api/matches", { candidate_id: candidateId, jd_id: jdId });
}

// ---- 面试间 ----

export function createInterview(
  candidateId: string,
  jdId: string,
  matchId?: string
): Promise<{ id: string; phase: string; status: string }> {
  return postJSON("/api/interviews", {
    candidate_id: candidateId,
    jd_id: jdId,
    match_id: matchId ?? null,
  });
}

export function startInterview(iid: string): Promise<{ question: string }> {
  return postJSON(`/api/interviews/${iid}/start`);
}

export function finishInterview(iid: string): Promise<Report> {
  return postJSON(`/api/interviews/${iid}/finish`);
}

/** SSE：POST 回答，逐事件回调（session/delta/eval/stage/done/error）。 */
export async function streamReply(
  iid: string,
  answer: string,
  onEvent: (ev: SseEvent) => void
): Promise<void> {
  const res = await fetch(`/api/interviews/${iid}/reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!res.ok || !res.body) throw new Error(await readError(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = block.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as SseEvent);
      } catch {
        // 半个 JSON 块，忽略
      }
    }
  }
}

// ---- 详情 / 报告 ----

export function fetchInterview(iid: string): Promise<InterviewDetail> {
  return getJSON(`/api/interviews/${iid}`);
}

export function fetchReport(iid: string): Promise<Report> {
  return getJSON(`/api/interviews/${iid}/report`);
}
