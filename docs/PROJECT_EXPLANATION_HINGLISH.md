# RepoAgent — Poora Project Explanation & Evaluation Guide (Hinglish)

> **Evaluation/Viva Special Guide**: Yeh document RepoAgent ke har ek component, uski working, use karne ki wajah (*Kyu use kiya*), folder structure, aur future scope ko aasan Hinglish bhasha me explain karta hai taaki aap evaluator ke saamne confident hokar answer kar sakein.

---

## 1. Project Overview: RepoAgent Kya Hai?

### Problem Kya Thi?
Normal developers jab kisi naye ya bade project par kaam karte hain, toh unhe:
1. Poora codebase padhna padta hai ki kaunsi file kahan hai.
2. Requirement ke hisaab se planning karni padti hai.
3. Code likhna padta hai.
4. Manually test chalane padte hain (`pytest`, `npm test`), aur agar test fail ho toh code ko theek karna padta hai.
5. Code quality aur security review karni padti hai.
6. Git branch banakar GitHub par Pull Request (PR) kholni padti hai.

Agar hum yeh kaam aam ChatGPT ya kisi single LLM se karwayenge:
- Woh fake ya non-existent file paths soch lega (hallucination).
- Woh testing nahi chala sakta, code tod dega.
- Woh galat branch par commit kar sakta hai ya sensitive files (`.env`, passwords) GitHub par push kar dega.

### RepoAgent Ka Solution:
**RepoAgent ek Autonomous Multi-Agent AI System hai.**
Aap ise sirf **Repository ka path** aur ek **Natural Language Requirement** (jaise *"Add password-reset functionality"*) dete ho. Uske baad yeh:
1. Codebase ko inspect karta hai.
2. Architecture plan banata hai.
3. Code likhta hai.
4. **Docker Sandbox** ke andar real tests run karta hai.
5. Agar test fail ho, toh wahi error Coding Agent ko dekar **auto-correction** karwata hai (max 3 baar).
6. Code Review Agent se security & logic review karwata hai.
7. Sab pass hone ke baad GitHub par ek clean, verified **Pull Request (PR)** open kar deta hai bina `main` branch ko chhue.

---

## 2. Architecture: Kaunsi Cheez Kyu Use Ki Gayi?

Yeh project 2 alag-alag AI agent frameworks, standardized protocols, aur isolated environments ko jodata hai. Evaluator sabse pehle yahi puchega ki **"Yeh sab alag-alag cheezein kyu lagayi?"**

### (A) CrewAI Kyu Use Kiya?
* **File:** `src/repoagent/flow.py`, `src/repoagent/agents/crewai/`
* **Kyu use kiya:** CrewAI ek pipeline orchestration framework hai. Iska `Flow` feature hume step-by-step state machine (`analyze` -> `plan` -> `code` -> `test` -> `review` -> `open_pr`) banane deta hai. Coding Agent aur Testing Agent ka task-based feedback loop CrewAI me natural tarike se banta hai.

### (B) Google ADK (Agent Development Kit) Kyu Use Kiya?
* **File:** `src/repoagent/agents/adk/planner.py`, `src/repoagent/agents/adk/reviewer.py`
* **Kyu use kiya:** Industry requirement aur heterogeneous multi-agent design prove karne ke liye. Humne show kiya ki system kisi single framework par dependent nahi hai. Google ADK ke agents structured Pydantic schemas par strict output enforce karte hain, jo planning aur code review jaise high-precision tasks ke liye best hain.

### (C) A2A (Agent-to-Agent) Protocol Kyu Use Kiya?
* **File:** `src/repoagent/agents/adk/server.py`, `src/repoagent/a2a_client.py`
* **Kyu use kiya:** CrewAI aur Google ADK do alag frameworks hain jo direct Python code me aapas me baat nahi kar sakte. Unke beech standardized communication ke liye humne Google ka **A2A protocol** use kiya.
* **Kaise chalta hai:**
  - Google ADK agents microservices ki tarah HTTP servers par run hote hain:
    - **Planner Agent:** `http://127.0.0.1:8001/` (Agent Card: `/.well-known/agent-card.json`)
    - **Reviewer Agent:** `http://127.0.0.1:8002/` (Agent Card: `/.well-known/agent-card.json`)
  - CrewAI Flow ek HTTP client (`A2AClient`) bankar in servers se JSON-RPC format me task execute karwata hai.

### (D) MCP (Model Context Protocol) Kyu Use Kiya?
* **File:** `src/repoagent/mcp_servers/`, `src/repoagent/mcp_client.py`
* **Kyu use kiya:** Pehle LLMs ke liye custom tools har framework me alag likhne padte the. Anthropic ka open standard **Model Context Protocol (MCP)** tools ko ek common protocol deta hai jise koi bhi agent (CrewAI, ADK, Claude, Ollama) call kar sakta hai.
* **Hamaare 3 MCP Servers:**
  1. **Repo Reader MCP (`repo_reader.py`):** Codebase files read karta hai, tree list karta hai, code search karta hai. Isme strict security hai (`_resolve_safe`) taaki agent `../../` karke system ki secret files na padh sake.
  2. **Test Runner Docker Sandbox MCP (`test_runner.py`):** Agent ka likha code safe Docker container ke andar execute karta hai.
  3. **GitHub MCP Server (`@modelcontextprotocol/server-github`):** GitHub API par branch create karne, commit push karne, aur PR open karne ke liye use hota hai.

### (E) Docker Sandbox Kyu Lagaya?
* **File:** `sandbox/Dockerfile`, `src/repoagent/mcp_servers/test_runner.py`
* **Kyu use kiya:** Agar AI agent galat ya malicious code likh de (jaise `rm -rf /` ya infinite loop), toh woh aapke main computer ko crash ya delete na kare.
* **Security Rules:**
  - `network_mode="none"`: Container ke andar internet band hai, koi data leak nahi ho sakta.
  - Non-root user: Code `sandbox` user ke andar chalta hai.
  - Resource limits: Max 1 CPU aur 512 MB memory limit.
  - Timeout: 120 seconds me auto-kill agar infinite loop ho.

### (F) Pydantic v2 (`RunState`) Kyu Use Kiya?
* **File:** `src/repoagent/schemas.py`
* **Kyu use kiya:** Sabhi agents ke beech data exchange strictly typed hona chahiye. Humne 12 models banaye (`Plan`, `ChangeSet`, `TestReport`, `ReviewReport`, `PRSummary`, `RunState`).
* Agar kisi agent ka output schema se match nahi hota, toh Pydantic turant error raise karta hai, jisse system crash hone se bach jata hai.

### (G) LLM Providers (Ollama & Gemini)
* **File:** `src/repoagent/providers/`
* **Kyu use kiya:** System kisi paid API par dependent nahi hai.
  - Local/Free mode: Ollama me chalne wala `qwen2.5-coder:3b` ya `7b` model.
  - Cloud mode: Google Gemini (`gemini-2.0-flash`).
  - Provider Adapter pattern se aap runtime par flag dekar provider badal sakte hain bina code change kiye.

---

## 3. End-to-End Working Flow (Kaise Chalta Hai?)

```
1. Input: Requirement ("Add password-reset functionality") + Repo Path
                           │
                           ▼
2. Flow Initialization: Workspace Clone + RunState Init
                           │
                           ▼
3. Planning (Google ADK via A2A on Port 8001):
   - Prefetches directory structure via Repo Reader MCP.
   - Planner agent generates structured Plan (files to modify/create).
   - Validates that paths exist; if invalid, 1 retry allowed.
                           │
                           ▼
4. Coding Agent (CrewAI):
   - Plan ke hisaab se code likhta hai (ChangeSet).
   - Files ko workspace me update karta hai.
                           │
                           ▼
5. Testing Agent (CrewAI via Test Runner MCP in Docker):
   - Docker container me `pytest` aur `ruff` chalata hai.
   - [Case A: Tests FAIL]:
       -> Error trace aur log tail extract hoti hai.
       -> Coding Agent ko di jaati hai.
       -> Coding Agent fix karta hai (Capped at 3 retries).
       -> Agar 3 baar me bhi theek na ho -> Status = "needs_human" (Safe stop).
   - [Case B: Tests PASS]:
       -> Workspace hash generate hota hai.
       -> Proceed to Review.
                           │
                           ▼
6. Code Review Agent (Google ADK via A2A on Port 8002):
   - Real git diff (`git diff HEAD`) aur test report inspect karta hai.
   - Verdict deta hai: 'approve' ya 'request_changes'.
                           │
                           ▼
7. PR Agent (CrewAI via GitHub MCP):
   - Preflight check: Remote 'main' se local files compare karta hai.
   - Branch create karta hai: `repoagent/<run_id>`.
   - Sirf modified feature files commit karta hai (framework files excluded).
   - GitHub PR open karta hai.
   - Verify karta hai ki remote PR me wahi files hain jo local diff me thi.
   - `report.json` aur `report.md` write karta hai.
```

---

## 4. Abhi Tak Kya Kaam Hua Hai? (Step 1 Se Step 6 Tak Summary)

Hamaare project ke pehle 6 steps 100% complete aur verified hain:

| Step | Kaam Kya Hua | Verification / Result |
| :--- | :--- | :--- |
| **Step 1: Setup & Design Freeze** | Project scaffold, Python 3.11 `.venv`, Pydantic v2 schemas (`schemas.py`), demo repo (`demo_repo/` with 12 auth/notes tests). | 27 unit tests pass, `pip check` clean. |
| **Step 2: Orchestrator Skeleton** | `RepoAgentFlow` (CrewAI Flow), retry logic router, Gemini/Ollama provider adapters, CLI runner. | 51 tests pass. |
| **Step 3: MCP Tool Layer** | `repo_reader` FastMCP, Docker sandbox `test_runner` MCP, GitHub MCP client. | 80 tests pass, Docker container verified. |
| **Step 4: Coding Loop & Correction** | Coding Agent, Testing Agent, Docker test runner loop with failure feedback (max 3 retries), ChangeSet applier. | 87 tests pass, loop tested on failure recovery. |
| **Step 5: Google ADK over A2A** | ADK Planner (port 8001), ADK Reviewer (port 8002), A2A HTTP client with secret redaction, path validation retry. | 134 tests pass, A2A communication verified. |
| **Step 6: PR Agent & Real GitHub PR**| Baseline preflight check, GitHub MCP atomic commit & PR opening, post-PR file verification, report writer. | **Real GitHub PR #1 created on `Kushagra-Pachauri/demo_repo`** (Clean 3 files, 0 leaks). |
| **Post-Step 6 Safeguards** | Replay benchmark lock (`REGISTERED_REPLAY_BENCHMARKS`), state consistency checker (`run_state.json` vs `report.json`), safe `--from-run` promotion workflow, duplicate PR prevention. | **149 tests pass (100%)**, zero test pollution. |

---

## 5. Important Viva / Evaluation Questions & Best Answers

Examiner aapse yeh technical questions zaroor puchega. Inke seedhe aur solid answers yaad rakhein:

#### Q1: Aapne CrewAI aur Google ADK dono kyu use kiye? Ek se kaam nahi chal sakta tha?
> **Answer:** "Sir, practical enterprise projects me koi single AI framework complete solution nahi deta. CrewAI dynamic pipelines aur self-correction coding loops (Orchestration) ke liye best hai, jabki Google ADK specialized microservices aur strict schema enforcement ke liye suited hai. Humne **heterogeneous multi-agent architecture** prove kiya hai jahan dono frameworks open **A2A protocol** ke through seamlessly coordinate karte hain."

#### Q2: A2A (Agent-to-Agent) protocol kya hai aur yeh kaam kaise karta hai?
> **Answer:** "A2A protocol Google ka standard open protocol hai jo AI agents ko microservices ki tarah expose karne deta hai. Hamaare ADK agents port 8001 (Planner) aur 8002 (Reviewer) par Starlette server par run hote hain aur ek standardized `/.well-known/agent-card.json` expose karte hain. CrewAI Flow in cards ko read karke HTTP JSON-RPC ke through unhe task submit karta hai aur verified Pydantic response leta hai."

#### Q3: Model Context Protocol (MCP) ka kya fayda hua?
> **Answer:** "MCP ek universal standard protocol hai jo AI models ko tools se connect karta hai. Pehle agar tool badalna ho toh saare agent ka code rewrite karna padta tha. MCP se hamaare tools (`repo_reader`, `test_runner`, `GitHub MCP`) standalone servers hain. Agar kal ko hum CrewAI ki jagah koi doosra agent use karein, tools me 1 line bhi change nahi karni padegi."

#### Q4: AI ne code likh diya, par aap guarantee kaise dete ho ki code safely chalega aur computer hack nahi hoga?
> **Answer:** "Sir, 3 solid layers of protection hain:
> 1. **Filesystem Guardrails:** `repo_reader` kisi bhi relative traversal (`../../`) ya absolute path ko workspace se bahar nahi jaane deta (`_resolve_safe`).
> 2. **Docker Container Sandbox:** Har test execution `repoagent-sandbox:latest` container me chalta hai jisme network band (`network_mode=none`) hai, non-root user hai, aur memory/cpu limits hain.
> 3. **Command Whitelist:** Sirf `pytest` aur `ruff` allowed hain; shell injection characters jaise `;`, `&&`, `|` block kiye jate hain."

#### Q5: Agar LLM tests pass nahi kar paya toh kya system infinite loop me fas jayega?
> **Answer:** "Nahi sir. Hamaare Flow me strict **Capped Retry Logic** (`max_retries=3`) hai. Har failure par test logs aur error Coding Agent ko wapas milte hain. Agar 3 retries ke baad bhi test fail rahe, toh pipeline safely terminate hokar `RunStatus.NEEDS_HUMAN` state me switch ho jaati hai aur error report save karti hai. System kabhi infinite loop me nahi jata."

#### Q6: GitHub par PR open karte waqt kya security measures hain?
> **Answer:** 
> 1. **Never Touch Main:** Hamesha isolated branch `repoagent/<run_id>` par commit hota hai.
> 2. **Baseline Preflight Check:** Remote `main` ke baseline hash ko local workspace se compare kiya jata hai taaki purane code par galat PR na bane.
> 3. **Push Scoping:** Sirf actual modified feature files commit hoti hain; `.env`, `runs/`, `__pycache__`, ya RepoAgent ka apna framework code kabhi push nahi hota.
> 4. **No Auto-Merge:** PR create hoti hai par merge hamesha human developer hi karega."

#### Q7: Replay Mode aur Live Mode me kya difference hai?
> **Answer:** "Local 3B coding models hamesha complex logic pass nahi kar paate. Isliye humne **Replay Mode** banaya jo benchmark reference fixture ko replay karta hai for deterministic testing. Sath hi humne guardrail lagaya ki custom natural language request ko Replay mode me reject kiya jaye taaki live user feature me accidentally purana password-reset code PR na ban sake. Live runs ke liye `--from-run <id>` promotion workflow banaya hai jo verified successful live runs ko hi PR banata hai."

---

## 6. Project Files & Directory Guide (Kaunsi File Kahan Hai)

```
D:\Agentic AI\
├── src/repoagent/
│   ├── schemas.py              # 12 Pydantic v2 data models (RunState, Plan, TestReport, etc.)
│   ├── flow.py                 # CrewAI Flow orchestrator & retry state machine
│   ├── run.py                  # CLI entry point (python -m repoagent.run)
│   ├── pr_agent.py             # PR creation, baseline preflight check, report writer
│   ├── a2a_client.py           # A2A client connecting CrewAI to ADK microservices
│   ├── mcp_client.py           # MCP client connecting to FastMCP & GitHub MCP
│   ├── providers/              # LLM Provider adapters (Gemini & Ollama/vLLM)
│   ├── agents/
│   │   ├── crewai/             # Coding Agent & Testing Agent
│   │   └── adk/                # Planner Agent (port 8001) & Reviewer Agent (port 8002)
│   └── mcp_servers/
│       ├── repo_reader.py      # FastMCP server for safe file reading & directory tree
│       └── test_runner.py      # FastMCP server executing pytest inside Docker
├── sandbox/
│   └── Dockerfile              # Isolated non-root Docker test environment
├── demo_repo/                  # Target repository (Notes API with 12 baseline tests)
├── tests/                      # 149 comprehensive tests (100% passing)
├── docs/                       # Architecture, Progress, Setup guide, and Evidence
└── scripts/                    # Helper scripts (start_agents.ps1, demo_tools.py, etc.)
```

---

## 7. Future Scope & Remaining Work (Steps 7–10)

Agale steps me project me yeh advanced features add honge:
1. **Step 7: Advanced Guardrails & Callbacks:** Prompt injection detection, automated secret scanning in generated code, aur task lifecycle callbacks.
2. **Step 8: Observability with Langfuse:** Langfuse SDK v5 se har agent execution, latency, LLM tokens, aur cost ka real-time visual dashboard banana.
3. **Step 9: FastAPI & Streamlit UI:** Ek interactive web dashboard jahan user frontend se repo select kare, prompt dale, aur real-time me agents ko kaam karte aur PR bante hue dekhe.
4. **Step 10: Multi-Container Cloud Packaging:** `docker-compose.yml` se poore system (A2A servers, MCP servers, Web UI, API) ko single command se deploy karna aur benchmark evaluation report produce karna.

---

## 8. Summary for Presentation / Viva (Quick 1-Minute Pitch)

> "RepoAgent ek guardrailed autonomous software engineering system hai. Yeh natural language requirements ko padhkar existing codebases me changes plan karta hai, code likhta hai, Docker container ke andar isolated test chalata hai, test failures par self-correct karta hai, aur passing code ka security review karke ek verified GitHub Pull Request open karta hai.
> Technical novelty yeh hai ki hum **CrewAI** aur **Google ADK** dono frameworks ko **A2A protocol** ke zariye ek sath chalate hain, tools ke liye **Model Context Protocol (MCP)** use karte hain, aur execution security ke liye **Docker Sandbox** enforce karte hain. Abhi tak humne 149 passing tests ke sath Step 6 complete karke GitHub par pehli verified Pull Request live create ki hai."
