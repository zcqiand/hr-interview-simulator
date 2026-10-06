# hr-interview-simulator — 仓库工作约定（供 Claude Code）

AI 面试官：**会追问、记得住**的 HR 面试模拟 Agent。
求职者上传简历 + 录入目标岗位 JD → 匹配打分出差距 → 面试官按差距定制出题、像真人一样追问
（含糊要证据、亮点往下挖、跑题拉回）→ 每轮回答按 4 维度即时评价入档 → 结束出结构化总评报告。
突出点只有一个：**评价闭环**——追问由上一轮评价驱动，报告由全程证据汇总。

## 铁律

- **TDD**：核心逻辑（评价引擎/匹配打分/追问策略/Agent 循环）先写失败测试 → 实现 → 绿 → commit。
- **mock-friendly**：`pip install -e ".[dev]" && pytest -q` 必须在无 Key、无网下全绿（FakeLLM 注入，禁止测试里真调 API）。
- **版本钉死**：依赖与 `version-lock.json` 一致，不引 lock 外的库。编排框架：LangGraph（面试流程·流程视角）+ CrewAI（解析/打分·团队视角）已入 lock；**禁 langchain 顶层包与 langchain-openai**（langchain-core 仅作 langgraph 传递依赖）。两框架边界：解析/打分 = CrewAI，面试流程 = LangGraph，代码互不 import。
- **禁止 env 默认值兜底**：`LLM_MODE` 必填（mock|live）；live 模式下 base_url/api_key/model 缺一即启动报错。
- **密钥不入库**：key 只在 `.env`（已 gitignore），`.env.example` 放占位符。
- **只增不改**：扩功能不动现有模块签名/行为。

## 技术栈（钉死于 version-lock.json）

- 后端：Python 3.10+ / FastAPI / openai SDK（OpenAI 兼容直调：MiniMax-M3，base_url 可配）/ pypdf（简历文本提取）
- 前端：React 18 + TypeScript + Vite 5（npm 走 registry.npmmirror.com）
- 存储：SQLite（`data/app.db`，thin DAO，不引 ORM）
- 语音：暂无（面试打字对话；后续需要再加 Web Speech）

## 验收

```bash
pip install -e ".[dev]"
pytest -q                        # 全绿，无 Key/无网
python -m hr_interview           # LLM_MODE=mock 时离线可演示（8802）
cd frontend && npm install && npm run build  # 前端构建通过
```

## 结构

```text
src/hr_interview/
├── config.py        # env 装配，fail-fast
├── llm.py           # LLMClient 协议 + LiveLLM(OpenAI兼容) + MockLLM（`<think>` 剥离 / wire 序列化）
├── store.py         # SQLite DAO（candidates/jds/matches/interviews/messages/answer_evals）
├── resume.py        # PDF 文本提取 + LLM 简历结构化画像
├── matching.py      # ★ 逐条对照打分：verdict/匹配分/强项/缺口 + 出题侧重清单
├── evaluation.py    # ★ 评价引擎：4 维评分模型 + 证据引用 + 总评报告
├── agent/
│   ├── prompts.py   # 面试官人设 + 追问策略 + 画像/差距/档案 组装系统提示词
│   ├── tools.py     # record_answer_eval / advance_stage / finish_interview
│   └── interviewer.py # 面试 Agent 循环（阶段状态机 + 追问 + SSE）
└── api/             # 路由：resumes / candidates / jds / matches / interviews
```

## 领域规则

- **评价 4 维度**：技术深度 / 表达清晰 / 证据可信 / 岗位匹配，各 1-5 分；证据必须摘回答**原话**，不许面试官脑补。
- **追问由评价驱动**：某维度 ≤2 分 → 追问要证据；≥4 分 → 往深挖一层；跑题 → 拉回当前题。
- **阶段状态机**：开场 → 技术 → 项目深挖 → 候选人反问 → 收尾，单向推进；每阶段提问上限 3，触顶自动推进。
- **面试官不编造**：不得引用候选人简历里没有的经历；总评每条判定必须能回指到某轮证据。
- **解析失败如实说**：加密 PDF/扫描件无文本层 → 明确报错提示，不硬猜内容。
- **示例数据全虚构**：随仓示例简历/JD 的姓名、电话、公司一律虚构并在文件头注明。
- MiniMax-M3 对接坑（`<think>` 内联思维链、工具轮回传 wire 形状）已在 llm.py 处理，见记忆 `minimax-m3-integration-pitfalls`。
