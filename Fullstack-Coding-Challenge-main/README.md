# Senior Fullstack Engineer — Take-Home Challenge

## Agentic Code Generation Workflow

**Time Budget:** 4–6 hours (we respect your time — scope accordingly)
**Submission Deadline:** 5 business days from receipt

---

## Overview

At our agency, AI-assisted development is a core part of how we build. This challenge tests your ability to design and implement an **agentic workflow** — an AI-powered system that takes a natural-language specification and autonomously generates a working frontend application.

You will build an agent (or multi-agent system) that reads a product specification and produces a **React + TypeScript** application that matches a reference implementation. The agent should plan, scaffold, generate code, and self-validate — not just make a single LLM call and hope for the best.

---

## The Boilerplate

A pre-built boilerplate is provided with the full stack already configured. **Your agent should generate code into this existing project structure**, not scaffold from scratch. This lets you focus on the agentic workflow rather than build tooling setup.

### What's Included

```
boilerplate/
├── src/
│   ├── main.tsx                   # Entry — boots MSW, wires Apollo + MUI
│   ├── App.tsx                    # Shell (placeholder for generated code)
│   ├── types.ts                   # Car interface
│   ├── test-setup.ts              # Vitest + MSW integration
│   ├── graphql/
│   │   ├── client.ts              # Apollo client configured
│   │   └── queries.ts             # GET_CARS, GET_CAR, ADD_CAR queries/mutations
│   ├── mocks/
│   │   ├── data.ts                # 5 seed cars with placeholder images
│   │   ├── handlers.ts            # MSW GraphQL handlers (GetCars, GetCar, AddCar)
│   │   ├── browser.ts             # MSW browser setup (dev)
│   │   └── server.ts              # MSW node setup (tests)
│   ├── components/
│   │   └── Example.tsx            # Reference component showing Apollo + MUI usage
│   └── __tests__/
│       └── Example.test.tsx       # Reference test showing MockedProvider pattern
├── public/mockServiceWorker.js    # MSW service worker
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── vitest.config.ts
```

### Tech Stack (pre-configured)

- React 19 + TypeScript
- Vite
- Apollo Client (GraphQL)
- Material UI (MUI)
- MSW (Mock Service Worker) for API mocking
- Vitest + Testing Library for testing

### Quick Start

```bash
npm install
npm run dev      # App at localhost:5173
npm run test     # Run test suite
npm run typecheck # TypeScript checking
```

---

## The Reference Application

Your agent's output should be a working **Car Inventory Manager** backed by a mock GraphQL API. It must:

1. **Display a list of cars** fetched via Apollo Client from a mock GraphQL API (GetCars query) served by MSW
2. **Show responsive car images** — the GraphQL schema includes mobile, tablet, and desktop image URLs. Render the appropriate image based on viewport width:
   - ≤ 640px → mobile
   - 641px – 1023px → tablet
   - ≥ 1024px → desktop
3. **Use Material UI cards** to present each car (make, model, year, color, image)
4. **Include an "Add Car" form** that submits via a GraphQL mutation (AddCar)
5. **Implement sorting and search** — a search bar to filter by model, plus sorting by year or make
6. **Extract GraphQL logic** into a `useCars()` custom hook
7. **Include unit tests** for key components

### Mock Data Schema

The boilerplate provides a `Car` type and 5 seed cars:

```typescript
interface Car {
  id: string;
  make: string;
  model: string;
  year: number;
  color: string;
  mobile: string;
  tablet: string;
  desktop: string;
}
```

### Optional Extras (the agent can attempt these)

- A `GetCar` query to fetch individual cars
- A year filter (multi-filter support alongside model search)
- A reusable `useCarFilters()` hook combining all filter logic

---

## What You Must Build

### Your Deliverable: An Agentic Workflow

Build a CLI tool or script (Node.js, Python, or TypeScript) that:

1. **Accepts a natural-language specification as input** (a text file or string describing the app above)
2. **Plans the implementation** — the agent should decompose the spec into discrete, ordered tasks (e.g., "create useCars hook", "build CarCard component", "write SearchBar")
3. **Generates the application code** — file by file, with awareness of dependencies between files, into the provided boilerplate
4. **Self-validates** — the agent should verify its output (e.g., run the test suite, or use a secondary LLM call to review its own code)
5. **Iterates on failures** — if validation fails, the agent should read the error output and attempt a fix (at least 1 retry loop)
6. **Outputs a runnable project** — the final result should work with:

```bash
cd generated-app && npm install && npm run dev
```

---

## Architecture Expectations

We're evaluating **how you design the agentic loop**, not just whether the output compiles.

Your system should demonstrate:

| Concept | What We're Looking For |
|---|---|
| **Task Decomposition** | The agent breaks the spec into ordered, dependency-aware steps — not one giant prompt |
| **Tool Use** | The agent calls tools (file write, shell commands, LLM calls) as discrete actions |
| **Context Management** | The agent passes relevant context between steps without exceeding token limits |
| **Error Recovery** | The agent reads test or type-check output and feeds errors back into the generation loop |
| **Prompt Design** | Prompts are structured, specific, and use techniques like few-shot examples or schema enforcement |

### How You Work Matters

Beyond the code itself, we want to see **how you approach the problem**:

- **Planned work** — Break your work into clear tickets or tasks before diving in. We want to see evidence of upfront thinking, not just a single "initial commit" with everything.
- **Clear architecture decisions** — Document why you chose your approach. What tradeoffs did you consider? Why this LLM provider? Why this agent structure?
- **Meaningful commit history** — Small, focused commits that tell a story. We should be able to read your git log and understand how the project evolved. Avoid a single large commit with all the work.
- **Iterative development** — Show that you built incrementally — get one piece working, then the next. Not everything at once.

### Recommended (Not Required) Stack for the Agent

- **LLM Provider:** Anthropic (Claude), OpenAI, or any provider — use what you're strongest with
- **Agent Framework:** LangChain, LangGraph, CrewAI, Mastra, plain function-calling loops — your choice, or roll your own
- **Tooling:** File system operations, shell execution (vitest, npm), LLM API calls

---

## Evaluation Criteria

### Primary (70%)

| Criteria | Weight | Description |
|---|---|---|
| **Agent Design** | 25% | Quality of the agentic loop: planning, execution, validation, retry. Is it a real workflow or a wrapper around a single prompt? |
| **Output Quality** | 20% | Does the generated app work? Does it meet the functional spec? |
| **Prompt Engineering** | 15% | Are prompts well-structured? Do they constrain output format and provide the right context at each step? |
| **Error Handling** | 10% | Does the agent recover from generation failures gracefully? |

### Secondary (30%)

| Criteria | Weight | Description |
|---|---|---|
| **Code Quality (of the agent)** | 10% | Is the agent code clean, typed, and well-organized? |
| **Documentation** | 10% | README explaining architecture decisions, how to run, and tradeoffs |
| **Creativity** | 10% | Bonus features: multi-agent collaboration, caching, parallel generation, cost optimization |

---

## Submission Requirements

1. **A Git repository** (GitHub, GitLab, or zipped) containing:
   - The agent source code
   - A `README.md` with setup instructions, architecture overview, and design decisions
   - A sample spec file (the natural-language input your agent consumes)
   - A sample output directory (a generated app we can run)

2. **A `.env.example` file** listing which API keys your agent needs (see the provided `.env.example` for the format). We will supply our own keys when running your agent.

3. **A short write-up** (can be in the README) covering:
   - Which LLM(s) you used and why
   - Your agent architecture (a diagram is welcome)
   - What worked well and what you'd improve with more time
   - Approximate cost per run (tokens used, API cost)

4. **Working demo:** We will run your agent with your sample spec and verify the output compiles and runs. We may also **modify the spec slightly** to test generalization.

---

## What We're NOT Looking For

- **A perfect UI** — functional correctness matters more than polish
- **An over-engineered framework** — a clean, well-thought-out script is better than a sprawling abstraction layer
- **Memorization** — if your agent only works because the spec is hardcoded into the prompts, that's a red flag. We'll test with a modified spec
- **Databases, backends, or infrastructure** — the boilerplate uses MSW to mock the API. There is no real backend. Do not build one. No databases, no Docker, no server setup
- **Authentication, deployment, or CI/CD** — keep scope focused on the agent and the generated app

---

## Getting Started

```bash
# 1. Clone this repo (contains the boilerplate)
git clone <repo-url> && cd Fullstack-Coding-Challenge

# 2. Verify the boilerplate works
npm install
npm run dev        # Should run at localhost:5173
npm run test       # Should pass (2 tests)
npm run typecheck  # Should pass

# 3. Build your agent (in a separate directory or repo)
# Your agent should copy this boilerplate, then generate code into it

# 4. Run your agent
node agent.js --spec ./spec.txt --output ./generated-app

# 5. Verify the output
cd generated-app
npm install
npm run dev
```

---

## Questions?

If anything is ambiguous, make a reasonable assumption and document it in your README. We value clear thinking over asking for clarification on every detail.
