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

## 30 tâches sympy, luna uniquement (2 passages)

| Variante | Résolues | Temps moy. | Temps méd. | Coût/tâche | Appels |
|---|---|---|---|---|---|
| a41_fast passage 1 | 18/30 | 134s | 115s | $0.0064 | 16.1 |
| a41_fast passage 2 | 21/30 | 194s | 121s | $0.0066 | 16.9 |
| a41_norev passage 1 | 19/30 | 106s | 85s | $0.0056 | 13.8 |
| a41_look (second regard luna) | 19/30 | 239s | 223s | $0.0078 | 19.6 |

- Le second regard luna a tourné sur 30/30 tâches et n'a modifié le patch que 2 fois (16450, 20154), déjà résolues sans lui : 0 tâche récupérée, +~110 s par tâche.
- Seules 4 tâches changent d'issue d'un passage à l'autre (17318, 18698, 19495, 20801) : la variance porte sur ~±2 tâches ; 8 tâches échouent partout.
- a41_norev du passage 2 exclu : un doublon de lanceur l'a exécuté deux fois en même temps dans le même dossier (mesures mélangées).
- Dépense totale des 2 passages : $1.25, 3127 appels, 100 % openai/gpt-6-luna.
