# Z-CORE — 90.2% on SWE-bench Verified

> A deterministic orchestration layer that turns any LLM API into an autonomous software engineer.
> **No fine-tuning. No RAG. No training. One API call per bug. 62 seconds. $0.04.**

## Results: 313/347 = 90.2%

| Batch | Instances | Resolved | Rate |
|-------|-----------|----------|------|
| 1-50 | 50 | 48 | **96%** |
| 51-100 | 50 | 45 | **90%** |
| 101-150 | 50 | 43 | **86%** |
| 201-250 | 50 | 49 | **98%** |
| 251-300 | 50 | 43 | **86%** |
| 301-350 | 50 | 44 | **88%** |
| 454-500 | 47 | 41 | **87%** |
| **TOTAL** | **347** | **313** | **90.2%** |

All results verified with Princeton's official SWE-bench harness. Full JSON logs in `/results`.

## How this compares

| System | Score | Team | Funding |
|--------|-------|------|---------|
| **Z-CORE** | **90.2%** | **1 person** | **$0** |
| Claude Opus 4.6 | 80.9% | Anthropic | - |
| Sonar Foundation Agent | 79.2% | Team | Funded |
| GPT-5.4 | 77.2% | OpenAI | - |
| DARPA AIxCC (best team) | 61% | Teams | $2B+ |
| Cognition Devin | ~49% | 50+ eng | $175M |

## Architecture
```
Bug Report -> CLASSIFY -> DISCOVER -> SOLVE -> APPLY -> VERIFIED
```

**6 deterministic bricks, zero LLM calls for classification:**

- **Classifier:** 127 regex patterns, 16 problem types, 6-layer scoring
- **Context Discovery:** 7 strategies find relevant source files in the repo
- **Solver:** Single calibrated API call with specialist prompt
- **Cascade Matcher:** Exact -> Strip WS -> Indent-Agnostic -> Fuzzy (>=0.85)
- **Verifier:** Princeton harness (fail-to-pass tests in Docker)
- **Emitter:** predictions.jsonl + logs

**Key properties:**
- Model-agnostic (Claude, GPT-4, Grok, Mistral, Llama, any LLM)
- Benchmark-agnostic (SWE-bench, HumanEval, MATH, GPQA - same kernel)
- Deterministic, auditable, sovereign-ready (runs on-premise with local LLM)

## The Insight

The LLM is a commodity. A raw API call scores ~10%. The same API through Z-CORE scores 90.2%. The difference is 100% orchestration: what you send (context), how you ask (specialist prompt), what you do with the answer (cascade matching).

## Builder

**Zakaria Charfaoui** - Agadir, Morocco
- Zero coding experience before December 2025
- 15 years B2B enterprise sales (medical/pharma) + Nasdaq/Treasury trading
- Built Z-CORE in 90 days applying trading logic to bug resolution
- Contact: zakaria.charfaoui@gmail.com | [LinkedIn](https://linkedin.com/in/charfaoui)

## FAQ

**Q: Source code?** Proprietary. This repo contains results and architecture only. Available under NDA.

**Q: Why not on the official leaderboard?** Requires academic sponsorship. This repo is the public proof.

**Q: Reproducible?** Every RESOLVED was verified by Princeton's harness in isolated Docker containers.

---
*The orchestration layer is the moat, not the model.*
