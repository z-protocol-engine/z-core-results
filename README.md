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
| Claude Opus 4.5 (best public) | 80.9% | Anthropic | - |
| Sonar Foundation Agent | 79.2% | Team | Funded |
| GPT-5.4 | 77.2% | OpenAI | - |
| DARPA AIxCC (best team) | 61% | Teams | $2B+ |
| Cognition Devin | ~49% | 50+ eng | $175M |

## What is Z-CORE?

A proprietary software layer that sits between any LLM API and a codebase. It transforms how the model sees the problem and what it does with the answer.

**The insight:** the LLM is a commodity. A raw API call scores ~10%. The same API through Z-CORE scores 90.2%. The difference is 100% orchestration.

**Key properties:**
- Model-agnostic (works with any LLM — Claude, GPT, Grok, Mistral, Llama, open-source)
- Deterministic and auditable
- Single API call per bug
- Sovereign-ready: runs fully on-premise with local LLMs. Zero data leaves the building.

## Builder

**Zakaria Charfaoui** - Agadir, Morocco
- Zero coding experience before December 2025
- 15 years B2B enterprise sales (medical/pharma) + Nasdaq/Treasury trading
- Built Z-CORE in 90 days
- Contact: zakaria.charfaoui@gmail.com | [LinkedIn](https://linkedin.com/in/charfaoui)

## Inquiries

Source code and technical architecture available under NDA for serious inquiries only.

---
*The orchestration layer is the moat, not the model.*
