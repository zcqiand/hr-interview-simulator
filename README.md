# AI 面试评估台（hr-interview-simulator）

AI 面试官：**会追问、记得住**的 HR 面试模拟 Agent。

求职者上传简历 + 录入目标岗位 JD → 逐条对照打分出差距 → 面试官按差距定制出题、
像真人一样追问（含糊要证据、亮点深挖、跑题拉回）→ 每轮回答按 4 维度即时评价入档
→ 结束出结构化总评报告。

突出点只有一个：**评价闭环**——追问由上一轮评价驱动，报告由全程证据汇总。

> 参照《图解 AI Agent》卷四实战章（xr-know-015 第 10 章）实现的教学示例。
> 本项目用于演示与技术练习，**不是真实招聘工具**；随仓示例数据的姓名、公司、电话
> 一律虚构。

## 快速开始

```bash
# 后端（Python 3.10+）
pip install -e ".[dev]"
pytest -q                          # 全绿：无 Key、无网（mock 模式测试）

# 复制 .env.example 为 .env 并填写（LLM_MODE 必填，无默认兜底）
#   mock = 离线演示（不需要 Key / 网络）
#   live = 真实调用 OpenAI 兼容接口（MiniMax-M3 等，密钥只放 .env，不入库）
python -m hr_interview             # 127.0.0.1:8802

# 前端（另开终端）
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run dev                        # http://localhost:5802（/api 代理到 8802）
```

live 模式下 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` 缺一即拒绝启动（fail-fast，
禁止 env 默认值兜底）。

## 功能

| 页面 | 做什么 |
| --- | --- |
| 准备页 | 简历（粘贴文本 / 上传 PDF）+ JD 录入 → 画像抽取 → 逐条对照打分（必须项 90 + 加分项 10）、强项/缺口清单 |
| 面试间 | SSE 流式对话 + 阶段进度条（开场→技术→项目深挖→候选人反问→收尾）+ 右栏「面试官记录」逐轮评价卡（4 维分 + 原话证据） |
| 报告页 | 总评引言 + 四维度均分与判级（强/中/弱）+ 编号改进建议 + 逐轮明细（每条判定可回指当轮原话证据） |

## 评价闭环怎么转

1. **出题定制**：系统提示词注入候选人画像、简历节选、JD 要求清单、匹配缺口（出题侧重）。
2. **评价驱动追问**：每轮回答后面试官必须调用 `record_answer_eval` 工具打分
   （技术深度 / 表达清晰 / 证据可信 / 岗位匹配，各 1-5 分）；最近 3 轮评价回注到
   下一轮的系统提示词，驱动追问策略——某维 ≤2 分要证据，≥4 分往深挖，跑题拉回。
3. **证据铁纪律**：评价里的证据必须摘回答**原话**（归一化后子串校验），不符即剔除
   并标注，宁可无证据不脑补；缺失维度记中性 3 分。
4. **收尾出报告**：`finish_interview` 工具（或「结束」按钮）触发总评——每条判定
   必须能回指某轮证据，汇总为维度均分/判级 + 改进建议 + 逐轮明细。

## 护栏（书第 10 章工程要求）

- PDF 解析失败（加密 / 扫描件无文本层）→ 明确 400 报错带原因，不硬猜内容。
- JD 解析 / 评价 / 报告的 LLM 输出格式异常 → 兜底重试一次，仍失败给降级结果。
- 面试循环硬上限：每阶段最多 3 问（触顶自动推进），全程最多 12 轮（触顶自动收尾），
  单轮工具循环 ≤4 次——防无限追问。
- MiniMax-M3 的 `<think>` 内联思维链在 LLM 层剥离（reasoning 单独成事件，不污染正文）；
  工具轮回传消息在 wire 形状上做了适配。

## 结构

```text
src/hr_interview/
├── config.py        # env 装配，fail-fast
├── llm.py           # LLMClient 协议 + LiveLLM(OpenAI 兼容) + MockLLM（离线演示/测试）
├── store.py         # SQLite DAO（candidates/jds/matches/interviews/messages/answer_evals）
├── resume.py        # PDF 文本提取 + LLM 简历结构化画像
├── matching.py      # 逐条对照打分：verdict/匹配分/强项/缺口 + 出题侧重清单
├── evaluation.py    # 评价引擎：4 维评分 + 证据原话校验 + 总评报告
├── mock_llm.py      # 运行时离线演示件（无 Key 无网全流程可演示）
├── agent/
│   ├── prompts.py   # 面试官人设 + 追问策略 + 阶段状态机 + 近轮评价注入
│   ├── tools.py     # record_answer_eval / advance_stage / finish_interview
│   └── interviewer.py # 面试 Agent 循环（SSE：session/delta/reasoning/eval/stage/done/error）
└── api/             # FastAPI 路由
frontend/            # React 18 + TS + Vite（评估文书风）
docs/functions/      # function-tree：功能清单与上线状态（SSOT）
```

## 测试与验收

```bash
pytest -q                                        # 79 项：Agent 循环/评价/匹配/PDF/API/mock 全绿
python -m hr_interview                           # mock 模式离线可演示
cd frontend && npm run build                     # tsc 严格模式 + vite 构建通过
```

依赖版本钉死于 `version-lock.json`；不引 LangGraph / CrewAI / LangChain，
tool-use 循环为手写实现。
