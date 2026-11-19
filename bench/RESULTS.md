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

## Travail hors ligne (0 appel modèle)

**Mesure à corriger.** Tous les runs `agent41_fast` (et le run de base `a41_luna`) sont partis avec la branche du « modèle de secours » (`RELIEF_MODEL == DRIVER_MODEL`). Le paramètre `reasoning` n'était pas envoyé, `max_tokens` était limité à 16k, et ni seed, ni température, ni route n'étaient transmis. En moyenne, 119 tokens de raisonnement par appel. Les chiffres ci-dessus correspondent donc à un luna qui ne raisonne presque pas. C'est corrigé : voir `test_relief_branch_keeps_driver_settings` et `tests/e2e_offline.py`.

**Analyse des 43 runs échoués.** Le patch casse 0 test existant et c'est le test caché de la tâche (F2P) qui échoue. L'agent comprend mal le comportement attendu. Les motifs retrouvés : généralisation arrêtée trop tôt (20916, 21930, 16597), correction au point du crash plutôt qu'à la source (17318), cas particulier ajouté pour garder un vieux test vert (18698, 19495, 20438), supposition de l'auteur prise au mot (20801), sur-ingénierie (23950).

**Changements dans `agents/agent41_fast.py`**
- Les réglages de luna sont vraiment envoyés : raisonnement high avec all_turns, seed 29, température 0, route azure/eu, 32k tokens.
- Retour au comportement de l'agent 41 sur trois points : détecteur d'abandon d'origine, vérification des chemins manquants (sans les URL ni les chemins système), processus d'arrière-plan conservés.
- Un appel d'outil coupé (JSON invalide) n'est plus exécuté avec des arguments vides.
- Le raisonnement est compté dans la taille du contexte. Délai d'appel porté à 900 s. La route n'est abandonnée que sur des erreurs du fournisseur.
- Greffes reprises de Z-CORE `agent.py` :
  - édition tolérante en 3 étapes (exacte, puis sans les espaces en fin de ligne, puis sans l'indentation avec réindentation) ;
  - fins de ligne CRLF conservées ;
  - indication de la ligne la plus proche quand l'édition ne trouve rien ;
  - contrôle de syntaxe pour js, sh, rb, php, pl, lua et go.
- Ajout aux instructions d'une section « modifier un codebase existant », tirée de l'analyse des échecs : corriger la cause à la couche générale, retirer plutôt qu'ajouter des branches, balayer les éléments frères, mettre à jour les tests périmés, se méfier des suppositions.
- Nouveau balayage déterministe au `finish` (`sibling_notes`), qui se déclenche une seule fois et seulement s'il trouve quelque chose. Il cherche deux choses dans les fichiers touchés :
  - les méthodes sœurs de même nom laissées telles quelles ;
  - les classes de caractères regex laissées à côté d'une classe remplacée.

  Rejoué sur les 120 patchs du banc (`tests/sweep_replay.py`) :

  | | Runs | Balayage déclenché |
  |---|---|---|
  | Échoués | 43 | 10 : 20916 ×4, 21930 ×2, 20438 ×4 |
  | Réussis | 77 | 4 : 16792 uniquement |

  Sur 20916, il pointe exactement la correction de référence.

**Tests hors ligne :** 12 tests unitaires et un test de bout en bout, `tests/e2e_offline.py`. Ce test fait tourner l'agent sur sympy-23950 avec un faux modèle (`fake_model.py`) qui rejoue des réponses écrites à l'avance, puis vérifie que la tâche est résolue par les tests officiels.
