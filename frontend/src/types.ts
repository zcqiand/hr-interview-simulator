/** 与后端 API 契约对应的类型（src/hr_interview/api/routes.py）。 */

export interface Requirements {
  must: string[];
  nice: string[];
}

export interface Jd {
  id: string;
  title: string;
  company: string;
  requirements: Requirements;
  parsed: boolean;
}

export interface Profile {
  skills: string[];
  experiences: string[];
  education: string[];
  highlights: string[];
  parsed: boolean;
}

export interface Candidate {
  id: string;
  name: string;
  profile: Profile;
}

export interface MatchItem {
  requirement: string;
  kind: string;
  verdict: string;
  note: string;
}

export interface MatchResult {
  id: string;
  candidate_id: string;
  jd_id: string;
  total_score: number;
  items: MatchItem[];
  strengths: string[];
  gaps: Array<{ requirement: string; kind: string; note: string }>;
  parsed: boolean;
}

export interface Scores {
  tech_depth: number;
  clarity: number;
  evidence: number;
  fit: number;
}

export interface EvalRow {
  round: number;
  phase: string;
  question: string;
  answer: string;
  scores: Scores;
  evidence: string;
  comment: string;
}

export interface DimScore {
  key: string;
  label: string;
  avg: number;
  verdict: string;
}

export interface Report {
  candidate_name: string;
  rounds: number;
  dimensions: DimScore[];
  summary: string;
  suggestions: string[];
  detail: EvalRow[];
}

export interface InterviewRow {
  id: string;
  candidate_id: string;
  jd_id: string | null;
  phase: string;
  question_count: number;
  round: number;
  status: string;
}

export interface MessageRow {
  role: string;
  content: string;
  phase: string;
}

export interface InterviewDetail {
  interview: InterviewRow;
  messages: MessageRow[];
  evals: EvalRow[];
}

/** SSE 事件（session/delta/reasoning/eval/stage/done/error） */
export type SseEvent =
  | { type: "session"; interview_id: string; round: number; phase: string }
  | { type: "delta"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "eval"; eval: EvalRow }
  | { type: "stage"; phase: string; forced?: boolean }
  | { type: "done"; status: string; report?: Report }
  | { type: "error"; message: string };

export const PHASES: Array<{ key: string; label: string }> = [
  { key: "opening", label: "开场" },
  { key: "tech", label: "技术考察" },
  { key: "project", label: "项目深挖" },
  { key: "reverse", label: "候选人反问" },
  { key: "closing", label: "收尾" },
];

export const DIMENSIONS: Array<{ key: keyof Scores; label: string }> = [
  { key: "tech_depth", label: "技术深度" },
  { key: "clarity", label: "表达清晰" },
  { key: "evidence", label: "证据可信" },
  { key: "fit", label: "岗位匹配" },
];
