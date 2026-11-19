# Agent 41 — banc SWE-bench Verified (sympy, 12 tâches)

Même modèle de travail pour tous : `openai/gpt-6-luna`. Notation avec les tests officiels (FAIL_TO_PASS + PASS_TO_PASS).
Tâches : 16450 16886 17655 18189 18698 19495 19637 19954 20801 21847 22714 24539
(16886 18189 18698 19495 19637 20801 = tâches non résolues par Z-CORE dans `results/batch454_500.json`).

| Variante | Résolues | Temps moyen | Temps médian | Coût moyen | Appels moyens |
|---|---|---|---|---|---|
| agent41 de base (second regard Sonnet) | 11/12 | 132 s | 123 s | $0.120 | 26.5 |
| agent41_fast, passe 1 | 9/12 | 116 s | 99 s | $0.009 | 18.5 |
| agent41_fast, passe 2 | 10/12 | 113 s | 94 s | $0.008 | 17.6 |
| agent41_fast sans revue forcée (`a41_norev`) | 11/12 | 105 s | 107 s | $0.009 | 16.1 |

Constats :
- Le second regard (Sonnet) = 92 % du coût de la base et n'a modifié aucune ligne sur les 12 tâches.
- Variantes rapides : 30/36 résolues au total (83 %) contre 11/12 (92 %) pour la base — écart dans le bruit à n=12,
  sauf 19495 (0/3 en rapide, 1/1 en base).
- Coût divisé par ~14, temps moyen −20 % pour `a41_norev`.

Détails par tâche : `results/*.jsonl`. Reproduire : `python3 run_bench.py --variants a41_norev --instances ...`
