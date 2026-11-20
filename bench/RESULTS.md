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

## Bancs avec mes propres modèles (pont vers des sous-agents Claude Haiku, 0 appel OpenRouter)

`bench/bridge/` : le faux serveur transmet chaque requête de l'agent à un sous-agent Claude Haiku. Celui-ci joue le rôle du modèle et ne touche au code qu'avec les outils de l'agent. La notation se fait avec les tests officiels. Les deux agents sont pilotés par le même modèle, à l'aveugle : le sous-agent ne sait pas quelle version il pilote.

**A/B agent 41 d'origine contre agent41_fast, sur 7 tâches**

| Tâche | Jeu | agent41 | agent41_fast |
|---|---|---|---|
| 17139 | tâche mise de côté | ✅ 11 tours | ✅ 7 tours |
| 19783 | tâche mise de côté | ✅ 9 | ✅ 18 |
| 22914 | tâche mise de côté | ✅ 12 | ✅ 8 |
| 19495 | dure, déjà analysée | ❌ 16 | ✅ 6 |
| 20438 | dure, déjà analysée | ❌ F2P 0/2, 19 | ❌ F2P 1/2, 13 |
| 20801 | dure, déjà analysée | ✅ 10 | ✅ 13 |
| 23950 | dure, déjà analysée | ❌ 18 | ❌ 5 |
| **Total** | | **4/7, 95 tours, 1414 s** | **5/7, 70 tours, 1317 s** |

- Comportements observés sur l'original :
  - Sur 17139, 20438 et 23950, il refuse 2 fois le `finish` à cause d'un faux positif : chemins d'un traceback ou URL github pris pour des livrables manquants. agent41_fast corrige ce défaut.
  - Il impose une revue puis un second regard. Résultat : plus de tours, sans gain mesuré.
- **Précautions de lecture :**
  - L'échantillon est petit (n=7).
  - Les 4 tâches dures font partie des 30 tâches dont les échecs ont servi à écrire les consignes. Ce sont des résultats sur l'échantillon d'apprentissage.
  - Haiku résout toutes les tâches faciles, quel que soit l'agent.
- **Runs isolés d'agent41_fast sur les tâches qui avaient servi à concevoir le balayage :**
  - 21930 : non résolu en v1 (5/6). Résolu en v2 (6/6), une fois que le message du balayage demande de mettre à jour les tests périmés.
  - 20916 : non résolu en v1. Le run v2 réussi est **faussé** : les consignes contenaient un exemple tiré de cette tâche, retiré depuis.
  - 17318 et 18698 : résolus.

Pour reproduire : `python3 bridge/run_bridge_task.py <tâche> <port> [agent41|agent41_fast]`, un sous-agent qui pilote `bridge/bridge_cli.py`, puis `python3 bridge/summary.py`.

## A/B complet : 40 runs, 27 paires (même pilote Claude Haiku, à l'aveugle, 0 appel OpenRouter)

| Jeu | agent41 d'origine | agent41_fast |
|---|---|---|
| 13 tâches mises de côté (jamais regardées) | 11/13, 248 tours, 5433 s | 11/13, 151 tours, 2250 s |
| 7 tâches de l'A/B, passage 1 | 4/7, 95 tours, 1414 s | 5/7, 70 tours, 1317 s |
| 7 tâches de l'A/B, passage 2 | 5/7, 90 tours, 2167 s | 4/7, 63 tours, 1638 s |
| **Total (27 paires)** | **20/27, 433 tours, 9014 s** | **20/27, 284 tours, 5205 s** |

**Conclusions**
- **Taux de résolution identique : 20/27 pour chaque agent.** Le +1 du premier A/B relevait du bruit : 19495 et 23950 changent d'issue d'un passage à l'autre, dans les deux sens.
- **Efficacité :** −34 % de tours. Le temps baisse de −42 %, mais une seule tâche pèse lourd : sur 19040, l'original a pris 51 tours et 3058 s, contre 15 tours et 222 s pour la version rapide. Sans 19040, le temps baisse d'environ −13 %.
- **Pourquoi l'original est plus lent :**
  - Il refuse le `finish` à tort, à cause d'URL ou de chemins de traceback pris pour des livrables manquants. C'est arrivé sur 8 runs observés (17139, 17630, 20438, 23824, 24443, 19040 et d'autres).
  - Il impose une revue forcée et un second regard.
  - Sur 19040, il est resté bloqué jusqu'à ce que le pilote justifie les URL dans son résumé.
- **Incidents de protocole :**
  - Plusieurs pilotes ont fait des appels d'outils parasites, tous refusés sans effet : `interrupt_session`, `create_session`, `archive_session`, Claude Docs.
  - Le pilote de 21612 (fast) a listé `bench/bridge` et téléchargé antlr4 dans `/tmp/x`. Le run 21612 de l'original l'a ensuite réutilisé, car `/tmp` est partagé.
  - Aucun de ces incidents n'a touché un correctif noté.
- **Limites :**
  - Le pilote est Haiku, pas luna : ce banc mesure ce que la structure de l'agent apporte, pas le score que donnerait luna.
  - n=27.

## Attaque des tâches ratées par les deux agents (17630, 19783, 20428, 20438, 23950)

**Diagnostic, réalisé hors ligne sur les conversations enregistrées :**
- **19783 :** `I*Dagger(A)` reste non simplifié. Le modèle l'avait affiché sans y réagir.
- **20438 :** le correctif supprime seulement le plantage, et `Eq(b,c).simplify()` reste non évalué.
- **23950 :** la méthode publique `as_set` est remplacée par le hook privé `_eval_as_set`, que la méthode de base bloque.
- **17630 et 20428 :** le correctif est appliqué en aval du symptôme ; la cause racine se trouve dans le code du cœur.
- **20438 :** le second test caché inverse un test existant que le ticket ne demande pas de changer. Il est inatteignable.

**Deux nouveaux contrôles au `finish`, déterministes et sans appel modèle :**

1. **Rejeu des exemples de l'énoncé.**
   - Fonctionnement : les blocs de code (`>>>` ou nus) sont exécutés sur l'arbre de base, via un worktree git, puis sur l'arbre patché.
   - Signalements :
     - erreur de logique toujours levée ;
     - résultat `F(...).m()` toujours non évalué ;
     - `A op B` différent de `B op A`.
   - Les coquilles du ticket sont corrigées par rapprochement de noms : `Operators` devient `Operator`.
2. **Hook privé :** signale une méthode publique `X` supprimée au profit de `_eval_X` ou `_X`.

**Rejeu sur les patchs enregistrés (`tests/example_replay.py`) :**
- **Runs ratés signalés :**
  - 19783 p2 ×2 et 20438 p2 ×2 par les exemples ;
  - 23950 ×6 par le hook (2 runs du pont et 4 runs luna).
- **Faux positifs sur les runs réussis :** 0/40 (pont) et 0/77 (luna). Le seul faux positif rencontré concernait 16792, avec une erreur de compilation cython due à l'environnement. Il a disparu une fois le signalement « erreur toujours levée » limité aux erreurs de logique Python.

**Runs en direct :** agent41_fast pilote 2 passages sur chacune des 5 tâches.

| Tâche | Avant (agent41_fast, passages précédents) | Après (2 passages) |
|---|---|---|
| 19783 | 1/2 | **2/2** |
| 23950 | 0/2 | **2/2** |
| 20438 | 0/2 (F2P 1/2 au mieux) | 0/2 (F2P 1/2 les 2 fois) |
| 17630 | 0/1 | 0/2 |
| 20428 | 0/1 | 0/2 |
| **Total** | **1/8** | **4/10** |

**Honnêteté :**
- Les deux nouveaux contrôles **ne se sont pas déclenchés** pendant ces runs. Le modèle a trouvé la bonne forme avant d'arriver au `finish`. Le gain ne peut donc pas leur être attribué ; c'est vraisemblablement de la variance.
- Ces 5 tâches ont servi à concevoir les contrôles : ce sont des résultats sur l'échantillon d'apprentissage.
- Ce qui est démontré, c'est le rejeu : les contrôles attrapent 10 échecs enregistrés sans aucun faux positif.

## Correction à la racine de 17630 et 20428

**Contrôle « réparation après coup » (`repair_notes` + `repair_gate`), déterministe et sans appel modèle.** Il signale les patchs qui corrigent une valeur après qu'elle a été produite :
- un `return X` devenu `return f(X)` ;
- un `x = strip(x)` ;
- un remplacement conditionnel d'entrées calculées par un objet zéro ou vide ;
- un retrait à la main des zéros de tête.

Le message demande de remonter au premier appel qui produit la mauvaise valeur et de corriger cet appel lui-même, même dans un module du cœur. Si c'est un test (nullité, égalité) qui se trompe sur la valeur, c'est ce test qu'il faut corriger.

Rejoué sur tous les patchs enregistrés : il signale 16 runs ratés et **0 des 125 runs réussis**.

**Runs en direct (agent41_fast, pilote Haiku) :**

| Version | 17630 | 20428 |
|---|---|---|
| Toutes les versions précédentes (les deux agents) | 0/9 | 0/7 |
| Contrôle avec le 1er message | 0/3 | 0/3 |
| Message renforcé | **3/6** (s2, t2, t3) | **1/6** (s2) |

- **17630 résolu :** correction du postprocesseur d'`Add` dans `matexpr.py`. C'est le producteur réel : une somme de `ZeroMatrix` donnait un `0` scalaire.
  - Le contrôle s'est déclenché dans les runs s2 et t3. En t2, le modèle a trouvé le producteur seul.
  - Le pilote du run s2 a lu des fichiers du dépôt hors protocole (lecture seule).
- **20428 résolu en s2 :** correction de `Expression.__bool__`, le test de nullité du domaine EX. Les `strip` ajoutés en aval ont ensuite été retirés.
  - Dans les 5 autres runs, le contrôle s'est déclenché 4 fois, et le modèle a chaque fois justifié son `strip` en aval.
- **Réserve :** ces deux tâches ont servi à concevoir le contrôle et à écrire son message. Ce sont des résultats sur l'échantillon d'apprentissage, avec n=6.

## Banc réel avec Ling (`inclusionai/ling-3.2-flash:free`, 0 $)

Le proxy est verrouillé sur Ling : tout autre modèle reçoit une erreur 403. En cas de 429 de la file gratuite partagée, il relance environ toutes les 2 s, pendant 120 s au maximum. Limite normale de l'agent : 900 s.

**A/B sur 6 tâches, un passage, version améliorée avant les correctifs ci-dessous :**

| Tâche | agent41 | agent41_fast |
|---|---|---|
| 22914 | ✅ 545 s, 29 appels | ✅ 523 s, 27 appels |
| 17139 | ✅ 796 s, 31 appels | ✅ 558 s, 23 appels |
| 20590 | ✅ 674 s, 28 appels | ✅ 793 s, 35 appels |
| 23950 | ✅ 738 s, 23 appels | ✅ 686 s, 21 appels |
| 19783 | ✅ 807 s, 27 appels | ❌ aucune modification |
| 20801 | ✅ 808 s, 25 appels | ✅ 705 s, 26 appels (avec les correctifs) |
| **Total** | **6/6, moyenne 728 s / 27,2 appels** | **5/6, moyenne 679 s / 24,3 appels** |

**19783 après correctifs : ✅ 655 s, 19 appels, F2P 2/2.**

**Ce qui a été corrigé pour les modèles lents (Ling), au fil des diagnostics et des audits :**
- **Avis de dernier passage, puis tours réservés à `edit_file`**, quand aucun fichier n'a été modifié : au 20e appel ou à 40 % du temps, puis 2 appels plus tard. Mécanisme repris de Z-CORE (`LAST_PASS_NOTE` / `SETTLE`).
- **Taille de chaque réponse :**
  - Elle est calculée pour tenir dans 35 % du temps restant, au débit le plus lent observé (Ling varie de 12 à 56 tokens/s).
  - Un appel ne peut prendre plus de 40 % du temps restant. S'il expire, il est relancé sans réflexion.
- **Réflexion :**
  - Ling garde son raisonnement par défaut. Avec `enabled: true`, il réfléchissait environ 3 fois plus, et ses tours forcés se terminaient en réflexion pure, sans appel d'outil.
  - `effort: none` est envoyé pour le tour forcé, pour la réponse qui suit l'avis de dernier passage, et après une réponse coupée par la réflexion.
- **Outils :**
  - `bash` accepte une liste de commandes, mais Ling ne l'utilise pas.
  - Une recherche vide est signalée explicitement.
  - Le temps restant est affiché en nombre de réponses.
  - Les contrôles de fin sont exécutés juste après la première modification.

**Réserve :** un seul passage par tâche, et la variance de Ling est forte (de 6 à 27 appels sur une même tâche). Un deuxième passage complet est nécessaire pour comparer de façon fiable.
