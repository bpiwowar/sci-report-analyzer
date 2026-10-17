## Ce que fait SciReport Analyzer

SciReport Analyzer rassemble les publications des personnes que vous suivez depuis plusieurs sources, les
fusionne, et classe le **canal de publication** (revue ou conférence) de chaque article — les classements évaluent des canaux,
pas des articles individuels. Chaque personne dispose aussi de dossiers avec des périodes, des étiquettes, des notes, des PDF stockés
et des rapports, pour préparer une évaluation.

## Personnes et sources

1. **Ajoutez une personne** (nom, affiliation et ORCID facultatifs, les sources à interroger ;
   dans un dossier, sa période et ses étiquettes). Les sources sont interrogées et les profils
   possibles listés dans la fenêtre : cochez les bons (les plus probables le sont) pour les
   valider tout de suite. Les autres restent *candidats* dans l'onglet **Profil**, sous **Sources** (ses
   sous-onglets : **Parcours**, **Périodes** et **Sources** ; on modifie, purge ou supprime
   la personne au-dessus).
   Son **parcours** (son CV) s'y construit aussi : postes et diplômes, datés (une année,
   `2019-09` ou `2019-09-01`), un diplôme avec sa thèse ; le poste sans fin est le poste
   actuel, affiché sous son nom. Sa propre thèse sur theses.fr (une fois validée) est proposée
   comme doctorat à ajouter. Un rapport affiche le CV avec le bloc `[[cv]]`.
2. **Validez** les bons profils et **rejetez** les autres. Seuls les profils validés sont utilisés.
   Vous pouvez aussi coller l'URL ou l'identifiant d'un profil (DBLP, HAL, ORCID, Semantic Scholar, Google Scholar,
   theses.fr, OpenAlex). Une fois une source validée, les articles d'un candidat peuvent être **confrontés
   à celle-ci** (bouton ⋂) : la carte indique alors combien d'entre eux sont déjà connus, ce qui
   compte aussi dans son score. Son ORCID est affiché en rouge lorsqu'il diffère de celui de la personne (ou
   de celui que donnent les profils validés).
3. Chaque source affiche son **état de mise à jour** : *jamais synchronisée*, *mise à jour…*, *à jour*,
   *obsolète* (validée après sa dernière synchronisation) ou *erreur*. La synchronisation se fait uniquement à la demande ; un
   bandeau vous signale quand certaines sources ne sont pas à jour.

| Source | Ce qu'elle fournit |
|---|---|
| DBLP (SPARQL) | Publications en informatique, ordre exact des auteurs |
| HAL | Archive ouverte française, PDF, types de documents |
| ORCID | Travaux déclarés par le chercheur (sans listes d'auteurs) |
| Semantic Scholar | Publications, PDF en accès ouvert (la désambiguïsation peut être bruitée) |
| Google Scholar | Pages de profil ; en cas de blocage, téléversez une page de profil enregistrée |
| theses.fr | Thèses encadrées, rapportées (rapporteur), examinées, présidées |
| OpenAlex | Nécessite une clé d'API (gratuite) : renseignez-la dans Paramètres → Clés d'API |

Votre **adresse e-mail** (Paramètres → Clés d'API) est requise avant toute récupération : elle est envoyée
aux sources (OpenAlex, Crossref et Unpaywall la demandent) afin qu'elles puissent vous contacter.

Une **source principale** (Paramètres → Sources de publications ; un dossier peut en utiliser une autre, ou aucune,
dans la section « Citations et sources » de ses réglages, tout comme une personne dans un dossier, depuis ses
périodes) décide
quels articles comptent : un article qu'elle ne liste pas pour la personne est exclu des rapports et de la
synthèse, par exemple HAL, où les chercheurs du CNRS doivent déposer leurs articles. Le panneau les liste tous
par défaut (un avertissement dit combien sont absents de HAL, chacun signalé) ; son filtre *tous / dans HAL /
absents de HAL* ne montre que ceux qu'elle liste, ou que les autres, pour les y ajouter. Les autres sources aident toujours à trouver
le canal et la session. Les personnes sans profil validé sur cette source ne sont pas concernées.

## Fusion

Les notices de différentes sources sont fusionnées lorsqu'elles partagent un DOI, ou ont le même titre
(ou presque) et des années proches. Les **prépublications** (arXiv, prépublications HAL…) sont rattachées à la
version publiée quelle que soit leur année. Si deux travaux différents ont été fusionnés, **séparez** une notice depuis les
détails ; si un travail apparaît deux fois, **fusionnez**-le avec l'autre. Les notices attribuées à tort peuvent
être **masquées** (après confirmation) : elles quittent les listes, graphiques, décomptes, rapports et exports,
mais restent dans la base ; le filtre « seulement les masqués » des articles les retrouve (pour les réafficher).
Les prépublications qui ne sont pas (encore) publiées ne sont pas affichées par défaut (« masquer les prépublications »).

## Canaux : le type, puis le rang

Chaque publication est liée à un **canal** (voir la page *Canaux*), sauf les livres et les chapitres
de livres : leur texte de canal est leur propre titre, leur collection ou leur éditeur, ils n'en ont donc pas (sauf
s'ils y sont liés à la main).
Chaque source donne un texte de canal, qui est apparié à un canal :

1. Le texte est nettoyé par les **règles de nettoyage** (Paramètres → Règles de nettoyage : expressions régulières,
   éventuellement pour certaines sources seulement, qui suppriment les années, les ordinaux, les plages de pages…).
2. Il appartient au canal qui l'a comme **variante** (même texte nettoyé) ; une variante ajoutée
   à la main l'emporte sur tout le reste. Sinon, une **règle de canal** (une expression régulière sur le texte brut de la source,
   ou sur son texte nettoyé, dans l'onglet Appariement du canal) peut le revendiquer ; sinon, un nouveau canal est créé.
   Une notice dont l'ISSN figure sur un canal appartient à celui-ci quel que soit son texte.
3. Les variantes et les règles de canal peuvent attribuer aux articles une **session** (démo, findings…).

Lorsque les sources d'un article donnent des canaux différents, l'un d'eux est choisi automatiquement ; vous pouvez
valider une autre source pour l'article. Paramètres → Données et cache permet d'effacer tout ce qui a été
calculé automatiquement (les décisions manuelles sont conservées) et de refaire l'appariement.

**Notices DOI.** Lorsqu'un article a un DOI (venant d'une source, ou saisi à la main dans ses détails), la
notice enregistrée avec celui-ci (Crossref, sinon DataCite et les autres registres) est récupérée une fois
et conservée définitivement. C'est la source principale de l'article : ses titre, année, auteurs et canal sont utilisés,
les autres sources ne complètent que ce qui lui manque. Un chapitre de livre sans événement (par exemple un volume
d'une collection comme LNCS) ne désigne aucun canal réel : ce sont les autres sources qui le donnent.

**Sources utilisées.** Paramètres → Sources de publications choisit les sources utilisées par l'application ; une source désactivée
n'est ni interrogée ni synchronisée, et ses notices sont écartées (conservées pour plus tard).

Les canaux sont classés selon deux niveaux :

1. **Type** : prépublication, conférence internationale ou nationale, atelier international
   ou national, revue internationale ou nationale, livre (livres et chapitres), ou autre
   (thèses, rapports…). Les canaux classés par CORE / Scimago / JCR sont internationaux ; les autres sont
   classés à l'aide de mots-clés (Paramètres → Types de canaux).
2. **Rang** : niveau CORE pour les conférences, quartile pour les revues, trouvés dans les jeux de données
   de classement. Un type peut avoir un **niveau par défaut** (par exemple, toute conférence nationale compte comme
   « C »), utilisé sauf décision manuelle contraire.

**Éditions CORE.** Les rangs CORE évoluent au fil des ans (éditions 2008 à 2026). Un article reçoit le
rang de l'édition en vigueur l'année de sa publication : un article de 2019, le rang CORE2018 (Paramètres → Types
de canaux permet d'utiliser plutôt la dernière édition). Ces classements sont fournis avec l'application
(`scripts/build_core.py` les reconstruit).

**Années Scimago.** De même, l'article d'une revue reçoit le quartile Scimago de l'année de l'article
(sinon de l'année antérieure la plus proche, sinon de la première). Les données Scimago sont téléchargées depuis
le dépôt du projet et vérifiées chaque semaine ; leurs années sont listées dans
Paramètres → Données et cache (« Scimago, années passées ») ; scimagojr.com bloque l'application, donc une année
manquante se télécharge dans le navigateur et son CSV se dépose là (ou depuis les détails d'un article,
« Importer »). Il est conservé (les années passées ne changent pas) et fait aussi revenir les revues qui ne sont plus
listées (par exemple celles qui ont cessé de paraître).

**Sources des données.** Revues : [SCImago Journal & Country Rank](https://www.scimagojr.com)
(*SCImago, (n.d.). SJR — SCImago Journal & Country Rank [Portal]*). Conférences :
[ICORE conference rankings](https://portal.core.edu.au/conf-ranks/). Canaux prédateurs :
la liste de Beall, telle que maintenue par
[stop-predatory-journals](https://github.com/stop-predatory-journals/stop-predatory-journals.github.io)
(actualisée chaque semaine ; y figurer est un indice, pas un verdict). JCR : votre propre import
(les données Clarivate ne sont jamais fournies). Citez les sources lorsque vous rapportez des rangs.

Les **ateliers** sont des canaux à part entière, avec une ou plusieurs **conférences principales** (avec les années, car
un atelier peut changer de conférence). Un article d'atelier prend le rang de la conférence principale de son année et
est compté dans sa propre catégorie, par exemple « Atelier A* ». Des textes comme « X @ SIGIR », « co-located with
… » ou « Workshop on … » font d'un canal un atelier ; sa page de canal suggère la conférence principale.
Un canal de conférence dont les variantes ressemblent à des ateliers propose de les séparer en canaux
d'ateliers.

Les **canaux conjoints** sont des conférences tenues ensemble (par exemple CORIA-TALN) : un canal dont les textes nomment
les acronymes de deux conférences ou plus est constitué de celles-ci (sa page de canal les affiche, et
elles peuvent être modifiées à la main). Ses articles prennent le niveau que ses conférences ont en commun ; lorsqu'ils
diffèrent, le canal est listé dans l'onglet **Conférences multiples**, où vous choisissez le niveau de quelle
conférence utiliser (d'ici là, le plus bas). Un niveau fixé à la main sur le canal ou
l'article l'emporte, comme toujours. Un canal conjoint peut aussi être ajouté à la main (**Ajouter un canal**,
« Une conférence conjointe ») : laissé sans nom, il est nommé d'après ses conférences dans leur ordre (« A / B »),
et son acronyme joint les leurs par des tirets (CORIA-TALN).

Tout peut être décidé à la main, **pour le canal** (par défaut) ou **pour un seul article** (cochez
« Seulement pour cet article ») : lier un article à un autre canal, fixer son type, choisir une
entrée de classement ou fixer un niveau (pour un seul article, en indiquant pourquoi). L'année d'un article et la position
de l'auteur peuvent aussi être corrigées. Les décisions manuelles sont signalées et
jamais écrasées par les traitements automatiques ni par les resynchronisations. Sur les pages des canaux, vous pouvez aussi
**fusionner** des variantes qui désignent le même canal (en donnant au résultat un nouveau nom si vous le souhaitez), ou
en séparer une ; **Proposer des fusions…** parcourt les canaux qui se ressemblent, une paire à la
fois : choisissez le canal principal, puis dites ce que l'autre est pour lui (une paire déclarée *pas la même* n'est plus proposée), et la page d'un canal liste les canaux qui
semblent liés. Chacun vient avec la relation devinée : le même canal, une de ses sessions (p. ex. *Findings of
the ACL: EMNLP* est la session Findings d'EMNLP), une conférence commune qui l'inclut (EMNLP-IJCNLP)
ou un de ses ateliers ; changez-la si besoin, puis appliquez. Les décisions sur les canaux font partie des
paramètres exportés.

**Non classé, vérifié.** Un canal sans rang trouvé peut n'être dans aucun classement, ou
n'avoir simplement pas encore été reconnu : son rang affiche « ? » (en pointillés) jusqu'à ce
que quelqu'un décide, et les listes de canaux comme les articles d'une personne peuvent
n'afficher que ceux **à vérifier** (de même pour les articles d'une correspondance approchée).
Le niveau **Non classé (vérifié)** dit que le canal n'est dans aucun classement (pour
certaines périodes, ou toutes les années : le bouton de ce nom, qui garde aussi son type tel
quel) ; ses articles restent dans la catégorie de leur type (et prennent le niveau par
défaut du type, s'il y en a un). Un atelier qui a une conférence principale en prend
toujours le rang.

**Périodes.** Le niveau fixé à la main d'un canal peut changer avec le temps : donnez un
niveau par période (p. ex. B jusqu'en 2017, A à partir de 2018) et chaque article prend le
niveau de son année ; les années qu'aucune période ne couvre gardent l'autre classement du
canal (son entrée de classement, sinon le classement automatique). Un canal garde aussi ses
**anciens noms** (NIPS jusqu'en 2017, puis NeurIPS) : un article affiche le nom et le sigle
de son année. Une **règle de canal** peut être limitée aux articles de certaines années :
ces années-là, elle l'emporte sur les variantes du texte (p. ex. un atelier devenu
conférence sous le même nom).

**Paramètres partagés.** Un groupe peut partager ses paramètres et ses décisions sur les
canaux par un dépôt git (Paramètres → Paramètres partagés) : un fichier par canal, relu
comme du code. **Récupérer** prend les changements du groupe et garde les vôtres (une
décision changée des deux côtés est un conflit que vous tranchez) ; **Envoyer** propose les
vôtres, sur une branche à vous pour une demande de fusion (pull request) ou sur la branche
du groupe. Chaque décision garde qui l'a prise et quand (votre nom, réglé là), et peut dire
pourquoi (la note d'un niveau). Les personnes, les articles et les lignes JCR ne sont jamais
partagés. Le format : `docs/json-formats.md`.

## Catégories et niveaux

<!-- levels -->

Les sessions satellites (Findings, démos, articles courts…) et les ateliers sont affichés comme des catégories
hachurées distinctes, par exemple « Findings CORE A » ou « Atelier A* » (les rayures d'une session sont de sa
couleur). Les sessions, leurs noms, couleurs et règles de détection se règlent dans Paramètres → Sessions
(ajoutez-en une, p. ex. une session industrielle, et ses articles sont aussi comptés à part) ; la session d'un
article peut être fixée à la main dans ses détails (automatique, la session principale, ou une session).
Lorsque les sources ne divergent que parce que certaines donnent une session (démo, court…) et d'autres la session normale,
la session l'emporte (démo, Findings, atelier…), quel que soit le canal donné par chacune. Des
sessions différentes (par exemple démo et court) sont un problème à régler : validez une source dans les détails, ou
fixez la session de l'article. De même, un atelier
l'emporte sur sa conférence principale (par exemple une notice DOI donnant EMNLP pour un article de BlackboxNLP).
Les actes édités (présidence) conservent le rang de leur canal dans des catégories propres, par exemple
« Actes (éd.) CORE A* » ; un volume édité sans canal est apparié par son titre.

## Panneau

Cliquez sur une barre ou une entrée de légende pour filtrer de façon croisée : les autres graphiques et la liste sont restreints
à la sélection (un nouveau clic la retire). **Maj**-clic ajoute une entrée à la sélection
(ou la retire), **Alt**-clic la sélectionne seule : par exemple Q1, Q2, CORE A* et CORE A ensemble.
Un article correspond à l'une des entrées choisies sur un graphique, et à celles de tous les
graphiques. Les puces à côté des filtres retirent une entrée ; **Réinitialiser** efface tous les
filtres (recherche, étiquettes, sessions, sélection…), sauf la période et ses années. Un article non classé affiche plutôt son type (par exemple « Conf. nat. », « Livre »).

Cliquez sur une publication pour ouvrir ses détails :

- **Publication** : liens, auteurs (confirmer les correspondances de noms), sources (séparer / fusionner),
  corrections (type, session, année…).
- **Appariement du canal** : le processus d'appariement, étape par étape — les textes de canal de chaque source et
  le canal auquel chacun appartient (et comment), le canal, son type et le rang. Chaque étape indique
  si elle est automatique ou manuelle ; ✎ **Remplacer** ouvre son éditeur, ↶ revient à l'automatique. Lorsque
  plusieurs sources donnent des canaux différents, choisissez celui à utiliser.

Une icône ⚠ marque les publications qui demandent un examen (nom introuvable parmi les auteurs, correspondance
possible à confirmer, canal ou année manquants, sources donnant des canaux différents…) ; l'interrupteur
« avec problèmes » ne liste que celles-ci. Survolez le badge d'une source pour voir la notice de cette source ;
cliquez dessus pour ouvrir la page.

Les **périodes** sont des intervalles d'années nommés (par exemple une période d'évaluation). Au sein d'une période, vous pouvez
**étoiler** des articles. La période choisie est mémorisée.

**Étiquettes et notes** (détails d'un article, onglet « Étiquettes et notes ») : saisissez un nom pour créer une étiquette ; elle
reste attachée à l'article. Les **listes étoilées** (★, par ex. « starred ») sont les listes ordonnées d'articles du
dossier, posées dans la période de chaque personne : le bouton ★ met un article dans la première, les puces ★ des
détails dans n'importe laquelle. Ce sont des réglages du dossier (leur section « Citations et sources » : l'onglet
« Paramètres » de la fenêtre du dossier, ou Paramètres → Réglages des dossiers ; leurs noms, le format de leurs
numéros, si leurs articles sont à citer, leur ordre). Renommez et recolorez les étiquettes avec le
bouton 🏷 ou dans Paramètres → Étiquettes et catégories. Chaque article a une **note** (Markdown),
plus une note propre à la période sélectionnée. Le filtre « étiquettes » affiche les articles ayant l'une
des étiquettes choisies. L'onglet **Articles étoilés** de la personne montre chaque liste étoilée du dossier
de la période, dans son ordre, avec son nombre d'articles (il suit la période du panneau). Son bouton ZIP
télécharge les PDF stockés de ses articles en un seul fichier (« 01 - Auteur - Année - Titre.pdf », dans
l'ordre de la liste) ; les articles sans PDF y sont listés dans un fichier texte (son infobulle donne
« n sur m PDF » ; sans aucun PDF stocké, il est désactivé). Pour écrire à
leur sujet, utilisez l'éditeur de rapport (ci-dessous).

**Étiqueter à partir d'une liste** (le bouton liste du panneau) : collez une liste de publications, par exemple la liste
numérotée d'un rapport copiée depuis un PDF. Chaque élément est apparié à un article par son identifiant HAL ou son DOI, sinon
par son titre (les auteurs et le canal qui l'entourent n'importent pas) ; vérifiez les correspondances, ajoutez les
articles qui manquent aux sources (par l'identifiant HAL / DOI de l'élément, ou trouvés sur HAL), puis posez une étiquette sur ceux-ci, ou mettez-les
dans une liste étoilée (★, dans la période) avec leurs **numéros** dans la liste. L'onglet Articles
étoilés affiche les articles de cette liste dans son ordre (#3 sur leur puce), et la liste 🗒 les numérote et
se termine par les éléments non trouvés.
Une liste désordonnée (lignes coupées, mots coupés, éléments collés, numéros de page mêlés) : **Lire avec le
LLM** (facultatif, voir IA locale plus bas) la fait lire par le LLM local en références (titre, auteurs, canal,
année, DOI ou lien), affichées chacune avec son titre en premier et appariées par celui-ci ; une longue liste
est lue par parties, dans son ordre, la partie en cours affichée (« Arrêter » : les parties non lues
découpées par leurs lignes, comme sans lui). Une partie sans réponse valide est aussi découpée par ses lignes ;
un DOI ou un lien donné par le LLM qui n'est pas dans la liste est écarté. Un élément lu se modifie par ses
parties (titre, auteurs, canal, année, DOI). Désactivé (le LLM désactivé, ou pas de modèle) : le bouton est
grisé, la fenêtre le dit, avec un lien vers ses réglages.

**Numéros dans une liste étoilée** (la place d'un article dans celle-ci) : Alt-cliquez
sur un numéro (`#3` à côté de la liste sur la ligne d'un article ; dans ses détails, où `#`
s'affiche quand il n'en a pas, un simple clic aussi) pour le saisir (Entrée ou quitter le champ enregistre, Échap annule, vide
le retire). Pour ordonner tous les articles d'une liste, cliquez sur le bouton liste numérotée
à côté de son nom dans l'onglet Articles étoilés : faites glisser les articles ou
utilisez leurs flèches ↑ / ↓ (ils sont alors tous numérotés 1, 2… dans le nouvel ordre), saisissez
un numéro, « Numéroter dans cet ordre » ou « Effacer les numéros ». Les articles sans numéro
viennent après ceux numérotés (les plus récents d'abord), dans la liste comme dans les citations.

Les notes sont en Markdown, avec **LaTeX** : `$x^2$` en ligne, `$$\sum_i x_i$$` centré sur sa ligne.
Un saut de ligne est conservé (comme dans Obsidian). Elles sont éditées avec un éditeur Markdown (barre d'outils,
⌘B / ⌘I, un aperçu à côté ou à la place). Un lien se suit d'un clic dans l'aperçu, ou d'un
⌘/Ctrl-clic dans l'éditeur : `[Dossier CRCH, p. 15](/doc/2?page=15)` ouvre le document à
cette page (dans la fenêtre du PDF qui l'affiche : là), les autres dans un nouvel onglet.

**Citations dans les notes du dossier** (l'onglet du dossier à côté d'un PDF) : les notes
de la personne dans le dossier citent ses articles avec la syntaxe de Pandoc : `[@key]` avec son
format de citation (celui de sa liste étoilée, sinon celui des articles seulement cités ; sans : sa référence), `[@a; @b]` ou `[@a, @b]` plusieurs (chacun avec le modèle, comme dans
`[@a, @b]{.notes}`, aussi écrit sans crochets : `@a, @b{.notes}`), `@key` ses titre, canal, année et catégorie,
`[@key]{.notes}` avec ses notes, `[@key]{.tags}` avec ses étiquettes (`{.notes .tags}` les
deux), et des modèles : `[@key]{.short-venue (.year)}` donne `EMNLP (2026)` (champs :
`.number` le numéro tel que sa liste étoilée l'écrit, `.index` le numéro seul comme dans
`{**#.index**}`, `.title`, `.venue`, `.short-venue` l'acronyme, `.year`, `.tags`, `.notes` ;
un crochet sans valeur est supprimé). Les modèles nommés (Paramètres → Modèles de citation,
ou ceux du dossier) s'utilisent comme `.nom`. Les réglages du dossier (leur section
« Citations et sources ») fixent ses listes étoilées : un article est numéroté dans la
première où il est (dans son ordre, sinon par année). Chaque liste, et les articles seulement cités (d'aucune liste :
numérotés à part, dans l'ordre de citation), a deux formats : sa **référence** (le numéro, par ex. `**#{index}**`, dans les
références et comme `.number`) et sa **citation** (comment `[@key]` cite, par ex. `{(.number .short-venue, .year)}` ; vide :
la référence), avec un exemple de chacun ; les citations insérées sont de simples `[@key]`. Les articles à discuter sont ceux des listes
à citer (sinon ceux des années de la période) ; le mode
« Articles » de l'éditeur (son icône rouge tant que chacun n'est pas cité, orange si un article cité
n'est pas des années de la période, verte une fois tous cités ; combien au survol) montre, à la place de l'aperçu, les articles à discuter avec
leurs numéros (rouge tant qu'ils ne sont pas cités, orange : hors des années de la période ;
un clic le cite, le menu ⋮ avec un modèle), celui sous le curseur mis en évidence, une
recherche (Entrée cite le premier ; les autres articles de la personne aussi), et « Les citer »
(ceux pas encore cités) ; les articles de chaque liste s'y réordonnent aussi (glissés,
leurs flèches ↑ / ↓, ou leur numéro Alt-cliqué et saisi), les citations renumérotées. Un ⌘/Ctrl-clic sur une citation (ou le
bouton ↗ de la barre d'outils, le curseur dessus) affiche les détails de son article dans une
nouvelle fenêtre. Copier remplace les citations, suivies des références, numérotées
comme citées (ou là où se trouve `[[references]]`, seul sur sa ligne : le menu « Insérer un bloc » de la barre
d'outils ; `[[references notes tags]]` : avec leurs notes, leurs étiquettes). Les blocs
s'écrivent `[[nom]]` (autrefois `[]{.nom}`, toujours lu).

**Modèles de rapport** (la section « Rapport » de chaque réglage des dossiers : Paramètres → Réglages des
dossiers, ou l'onglet « Paramètres » de la fenêtre du dossier) : les textes dont un rapport part,
ses titres, ses blocs (`[[publications]]`, `[[excerpts]]`, `[[cv]]`, `[[references]]`) et des
consignes en commentaires (`%% … %%`, omis à l'affichage et à la copie) ; un est fourni. Les notes
d'une personne dans un dossier partent vides : le bouton 📄 de la barre d'outils des notes du dossier les
fait partir d'un modèle (en demandant d'abord quand elles sont écrites), ou enregistre leur structure comme
modèle (« Enregistrer comme modèle… » : leurs titres et leurs blocs, sans ce qui est écrit). La même
section fixe la langue des rapports (références, extraits, synthèse… ; par défaut, celle de l'interface).

**Filtres de copie** : Maj+clic sur un bouton de copie (les notes, les extraits) copie via un
filtre, en texte enrichi collé mis en forme dans un formulaire : ceux activés dans Paramètres →
Filtres de copie (plusieurs : celui à utiliser est demandé). **Marmotte** (CNRS) garde la mise en
forme de base que permettent ses formulaires : titres `#` en bandeaux numérotés (« --- 1. Recherche --- »),
`##` en lignes en gras, listes en paragraphes (« • … ») ; les commentaires (`%% … %%`, par ex.
l'emplacement des citations), encadrés, tâches, étiquettes et lignes étiquetées #question ou
#to-check sont retirés, les tableaux aussi. Une étiquette peut à la place devenir un texte
(Paramètres → Filtres de copie → Étiquettes changées en texte) : un avant le texte de la ligne
(par ex. une icône, 🌍) et un après, d'une couleur (ou surlignés), en gras, aussi le texte de la
ligne si coché, par ex. les étiquettes des marqueurs des extraits (par défaut : 🌍 pour
#rayonnement ou #influence, ⭐ pour #saillant ou #salient, 🔹 pour #mineur ou #minor, ⚠️ pour
#bemol ou #caveat ;
#local, #national, #international, les niveaux, en « (national) »… après le texte de la ligne). Marmotte garde le gras, l'italique, le soulignement, les couleurs
de texte, le surlignage, les emojis (🌍 ⭐ 📣) et les symboles (★ ✦ ◆ ➤) ; il retire les
exposants, les tailles de police et le texte barré.

**PDF** : l'icône de fichier PDF à côté d'un titre (avec un crayon une fois annoté ; les icônes PDF rouges simples
après le badge d'une source sont le PDF de cette source, en ligne) ouvre le PDF stocké de l'article à côté de la page
de la personne (⌘/Ctrl-clic : dans une nouvelle fenêtre). Une icône grise signifie qu'un lien en accès ouvert (ou un
DOI) est connu : cliquez dessus pour
télécharger le PDF (après confirmation, sauf si elle est désactivée) et l'ouvrir. La visionneuse (PDF.js,
installée une seule fois) dispose d'outils de surlignage, de texte, de dessin et d'image ; les annotations sont enregistrées dans le
PDF stocké (toutes les quelques secondes, avec Enregistrer ou ⌘S/Ctrl+S) ; le menu de la visionneuse (») enregistre,
télécharge une copie, imprime et ajoute des images. ← / → permettent d'aller et venir après
avoir suivi un lien dans le PDF. Au-dessus, la barre de menu et l'en-tête de la personne (ses onglets) restent ; le
bouton plein écran les masque (pour tous les PDF, jusqu'à les afficher à nouveau). Le bouton du panneau latéral affiche à côté les étiquettes et les notes de l'article
(y compris celles d'une période), ses détails (comme dans le panneau des publications) et ses signets ; 🔖+ crée un signet sur la sélection (sinon sur l'endroit affiché), et 🔍 trouve l'article du
texte sélectionné (parmi les articles de la personne, sinon sur HAL, pour l'ajouter) ; les actions nécessitant une
sélection sont grisées en l'absence de sélection. Une zone d'une page sert aussi de sélection (une figure, un tableau, un
passage sur plusieurs colonnes) : activez l'outil zone (le cadre en pointillés dans l'en-tête, ou **A** ou **R**) et
tracez un rectangle, ou Alt+glisser ; son texte constitue la sélection, et le rectangle son emplacement
(Escape, ou un clic ailleurs, l'abandonne). Raccourcis (une seule touche, sauf pendant la saisie) : **H** surligne
la sélection (sinon active ou désactive l'outil de surlignage), **T** texte, **D** dessin, **I**
image (activer ou désactiver), **A** ou **R** zone, **B** signet, **F** trouver l'article, **E** ajouter à une
catégorie, **Q** citer dans la dernière note utilisée (une citation Markdown, avec sa page), **Maj+Q**
citer au curseur, dans la phrase (`"…" %% [Application, p. 4](lien) %%` : l'emplacement dans un
commentaire Obsidian, renvoyant à la page du PDF, masqué dans l'aperçu ; le bouton de citation des barres d'outils des notes, avec Maj+clic ; cliqué : comme Q) ; ceux de PDF.js : ⌘F / Ctrl+F recherche, + / − zoom, N / P (ou J / K) page suivante et
précédente, R rotation, Home / End, Delete supprime l'annotation sélectionnée. Le bouton ⬇ du panneau
télécharge les PDF de tous les articles affichés ; « Stocker un PDF » (détails) en téléverse un. Paramètres →
Données et cache affiche l'espace utilisé et supprime les fichiers des articles qui ne sont plus dans la base de données. Un
article qui disparaît des sources emporte avec lui son PDF téléchargé ; un PDF téléversé ou annoté
conserve l'article.

**PDF à côté de la page d'une personne** : quel que soit l'onglet (les publications, le rapport, les extraits…), les
PDF s'ouvrent à sa droite : les documents de la personne (onglet Rapport, menu « Documents ») et ses articles (leur
icône PDF, dans la liste ou à côté des notes ; un lien vers un PDF, par exemple celui d'une citation dans les notes, ou
un extrait d'un autre PDF), par exemple les extraits avec un document, ou le rapport avec un article. Une bande en bas
les liste : cliquez sur l'un pour l'afficher, sur celui affiché (ou ») pour les réduire (la page seule ; « réaffiche
le dernier), ✕ pour en fermer un (de la vue seulement : rien n'est supprimé). Faites glisser leur bord gauche pour les
redimensionner. Ils restent ouverts pour la personne (et dans l'adresse) jusqu'à leur fermeture. Chacun a l'en-tête de
la visionneuse (son ↗ l'ouvre dans une fenêtre à part) et son panneau latéral, masqué : pendant ce temps, les notes et
extraits de la page sont les siens, Q citant dans les notes (l'onglet Rapport), E classant la sélection (l'onglet
Extraits), F trouvant l'article de la sélection et L marquant les articles d'une liste (depuis l'onglet Rapport), B
ajoutant un signet à l'endroit lu, un extrait cliqué sur ses pages sélectionné dans l'onglet Extraits, et un extrait
cliqué là montré sur sa page. Un document renommé depuis son titre (double-clic) l'est aussi dans le bandeau.
Dans un dossier, le bandeau est toujours là, quel que soit l'onglet : son + y ajoute un document (PDF) de la
personne, comme des fichiers PDF déposés dessus.

**Documents** (onglet Rapport de la personne, menu « Documents ») : PDF concernant la personne au sein d'un dossier ou
d'une période (un dossier de candidature, un CV…), téléversés là et lus dans la même visionneuse (annotations, signets,
une note), à côté de la page (ou dans un nouvel onglet, depuis leur ⋮). Les articles de la personne mentionnés dans un
document sont retrouvés (par le titre, quelques coquilles
tolérées, ou par DOI / identifiant HAL) et deviennent des liens (un léger soulignement pointillé ; rouge lorsque le PDF de
l'article est stocké). Les références
numérotées (« [11] », « 11. », « [C3] ») font aussi pointer leurs citations (« [11] », « [3, 11] », « [3-5] »)
vers l'article, tout comme les citations auteur-année (« (Lyu et al., 2023b) », « Marti Vidal and
Montiverdi, 2021, 2020 ») appariées aux références trouvées (premier auteur, année et lettre).
Un clic affiche les détails de l'article à côté du document,
comme dans le panneau des publications (étiquettes, notes, son PDF à consulter en ligne, télécharger ou stocker) ; « Pas cet
article » oublie une correspondance erronée. Une référence qui n'a pas été trouvée : sélectionnez-la, 🔍, puis « Lier
ici ». L'onglet des articles les liste (un champ de recherche le filtre : chaque mot, dans le titre,
les auteurs, le canal ou l'année). La note du document cite les articles de la personne comme le font les rapports
(`[@key]`, le bouton ❝ pour en choisir un, ceux du document en premier) ; son aperçu et sa copie
les numérotent (« [1] », dans l'ordre de première citation), la copie se terminant par les références.

**Catégories** (page du dossier : le bouton des catégories ; l'onglet Extraits de la personne ; l'onglet des
catégories de la visionneuse ; ou la section « Catégories » des réglages du dossier, modifiée aussitôt) :
l'arborescence ordonnée des catégories d'un dossier (par exemple une grille d'évaluation), que l'on fait glisser pour les ordonner et les imbriquer,
chacune avec un identifiant facultatif (`#id`, pour les rapports : voir plus bas) ; une catégorie contenant des extraits (directement ou en dessous, de n'importe quelle personne du dossier)
n'est supprimée qu'une fois ceux-ci déplacés vers une autre, choisie à ce moment-là. Dans le PDF d'un document (un rapport,
un dossier de candidature… pas celui d'un article), sélectionnez un passage (ou cliquez sur un surlignage), puis « ajouter à une
catégorie » (en-tête, ou touche E ; sur un extrait déjà classé, cliqué ou son texte
sélectionné : le modifie) : cliquez sur une catégorie, ou tapez pour la trouver (la dernière choisie est proposée, marquée et affichée :
Entrée la prend ; en tapant, Entrée : la première trouvée ;
un nouveau nom la crée, la boîte de dialogue restant ouverte), avec les années concernées (celles trouvées
dans le passage, par exemple « 2026-32 » : 2026–2032, et retirées de son texte quand elles le commencent
suivies de deux-points ou le terminent après eux ; son calendrier barré les efface, le texte redevenant celui
sélectionné) et, le cas échéant, ses marqueurs : saillant (un point fort), rayonnement, mineur (de moindre poids) ou
bémol (une réserve) ; lorsqu'il pourrait déjà s'agir d'un extrait
existant (mots similaires, signalé par un avertissement ; ou trouvé en tapant ses mots), fusionnez-le plutôt avec
celui-ci, en conservant son texte ou seulement son emplacement (une référence). L'onglet des catégories liste
les extraits de la personne (clic : aller au passage ; une catégorie repliée avec son chevron, ou toutes ;
recherchés par leur texte, leurs étiquettes ou le texte d'une section), pour les modifier (une seule fenêtre, en onglets : « Extrait », son texte, ses années,
marqueurs, niveau et étiquettes, les passages tels que sélectionnés affichés, pour y revenir ou
les ajouter au texte ; « Catégorie », pour le déplacer, avec le même sélecteur, ce qui a été
modifié étant d'abord enregistré ; « Notes », son commentaire et un lien), les déplacer (l'onglet
de cette fenêtre), les fusionner (en faisant glisser l'un sur l'autre, ou avec son icône de fusion puis un clic sur
l'autre : regroupés, chacun avec sa propre citation ou seulement son emplacement, son icône lien / citation basculant de l'un à l'autre,
sur un seul élément avec la catégorie, les années… de l'autre ; le crayon du groupe modifie son texte d'ensemble,
par ex. une phrase qui les résume, ses extraits étant alors cités dans le commentaire qui le suit, avec leurs
emplacements, tous toujours teintés sur le PDF ; à nouveau sortis
avec son icône de séparation), les réordonner (en les faisant glisser sur le bord supérieur ou inférieur d'un autre), les écarter
(son ✕ : laissés hors du texte généré, les notes et rapports, toujours conservés et listés, barrés ;
rétablis avec le même bouton), les commenter (sa bulle, les « Notes » de la fenêtre : un commentaire en Markdown sur
l'extrait, ou sur un groupe, affiché dessous et placé après son élément dans les notes en
commentaire `%% … %%`, non affiché ; et un lien, une URL, écrit après lui dans les notes, son 🔗
sur sa ligne), les supprimer,
ou les copier en Markdown (sans guillemets, leurs emplacements dans des commentaires Obsidian : `%% Application, p. 4;
p. 12 %%`, puis leurs années ; ceux qui témoignent du rayonnement sont aussi listés, par catégorie, dans une dernière
section « Rayonnement », ou là où est le bloc `[[influence]]` (ou `[[rayonnement]]`) ; l'icône de badge nomme les documents, par exemple un long nom de fichier sous la forme
« Application ») ;
le menu « Insérer un bloc » du rapport les insère, par catégorie (le bloc `[[excerpts]]`, tenu à jour),
ceux d'une catégorie qui a un identifiant, ou la section Rayonnement ;
`[[excerpts#id]]` insère ceux de la catégorie de cet identifiant (et en dessous ; ses
sous-catégories en titres sous celui du bloc), alors omis de `[[excerpts]]`, par ex.
`# Recherche` puis `[[excerpts#recherche]]`, et `# Autres activités` puis `[[excerpts]]`.
Où vont les extraits de rayonnement, les réglages du dossier le disent (leur section
« Extraits » : « Extraits de rayonnement ») : listés aussi dans la section Rayonnement (là où est `[[influence]]`, sinon à
la fin de `[[excerpts]]`), comme par défaut ; seulement là, sous des sous-sections au nom
de leurs catégories (imbriquées comme elles, un niveau plus bas), retirés de leurs catégories
(une catégorie restée vide disparaît) ; ou seulement dans leurs catégories, avec leur
étiquette, par ex. changée en icône par les filtres de copie. Les extraits saillants, mineurs
et les bémols ont toujours leur étiquette.
Les réglages du dossier (section « Extraits ») fixent l'étiquette de chaque marqueur (par défaut dans la langue de
ses rapports : #saillant, #rayonnement, #mineur, #bemol ; en anglais #salient, #influence,
#minor, #caveat) et
si un extrait se déplace quand on le marque (« En premier », par défaut) : marqué, il passe en
tête de sa catégorie, parmi ceux marqués ainsi (les saillants d'abord, puis le rayonnement,
puis les mineurs, puis les bémols) ; démarqué, après eux ; on peut ensuite le déplacer à son gré, le rapport
suivant cet ordre.
Un extrait peut aussi avoir un niveau (sa fenêtre d'édition : « Niveau »), par ex. local,
national ou international, en étiquette après celles de ses marqueurs (#national) ; les
réglages du dossier fixent ses niveaux (leurs étiquettes, par défaut dans la langue de ses
rapports ; un ajouté avec +, retiré avec son ×) et le niveau par défaut des extraits qui n'en
ont pas (en italique sur leurs lignes, dans l'onglet des catégories, où la recherche les trouve
aussi par leur niveau).
Chaque catégorie peut avoir un texte propre à la personne (Markdown, par exemple un résumé de
ses éléments ; son crayon, au survol, dans l'onglet des catégories, ou un clic sur le texte) :
placé après son titre, avant ses éléments (en mode imbriqué, celui d'une catégorie restée
vide va sous sa sous-section de la section Rayonnement). Avec « Ce texte seul, sans lister
ses extraits », la section est résumée par son texte : dans les notes et la copie, ses
extraits ne sont pas listés mais suivent le texte entre `%% … %%` (un commentaire, jamais
affiché) pour référence ; l'onglet des catégories les liste toujours.

Les **dossiers** (page Rapports) regroupent des personnes, par exemple pour un comité de sélection : un dossier a un nom,
une date et peut être masqué. Chaque personne a sa **propre période** dans chacun de ses dossiers
(définie en cliquant dessus sur sa ligne dans le dossier, ou dans son onglet Profil, sous Périodes), avec ses propres étoiles, étiquettes et notes ; ouvrir une personne depuis un
dossier sélectionne celle-ci. Retirer une personne d'un dossier (✕ sur sa ligne, ou dans son onglet Profil)
garde cette période, avec ses données, comme l'une de ses propres périodes, ou la supprime
(avec une seconde confirmation qui les liste si elle a des notes, documents, extraits ou
textes de rapport).
La **déplacer** vers un autre dossier (le bouton dossier avec une flèche, aux mêmes endroits)
emporte cette période, avec toutes ses données ; ses extraits sont classés dans les catégories
de l'autre dossier (ajoutées s'il le faut).
Le bouton 📝 de la ligne d'une personne (aussi à côté du nom du dossier sur sa page) ouvre ses **notes dans le
dossier** (son onglet Rapport ; ses extraits par catégorie : l'onglet Extraits), comme à côté de ses PDF : pour les
écrire sans ouvrir de document (par exemple quand elle n'en a aucun). La fenêtre d'un extrait nomme sa catégorie sous
son titre. Son onglet **Chronologie** dessine ce qui est daté à son sujet le long d'un axe horizontal des années, les
plus anciennes à gauche (chaque année une colonne, pour que les trous se voient ; l'année en cours marquée ; sa
période dans le dossier sur fond turquoise), une ligne par sorte. Sa **carrière** (onglet Profil) : une ligne de son
CV en barre le long de sa durée, au mois près quand ses dates le donnent (un poste actuel : jusqu'à aujourd'hui, en
s'estompant), son nom au-dessus ; les **doctorants** qu'elle a encadrés, chacun en barre du début de la thèse à sa
soutenance (en cours : jusqu'à aujourd'hui), le nom du doctorant au-dessus ; les barres qui se chevauchent l'une
au-dessus de l'autre. Ses **extraits** dans le dossier, une ligne par catégorie principale (de la couleur de leur
catégorie) : le début de leur texte au-dessus d'une fine barre le long de leurs années (une seule : celle-là) ; ses
**articles** : une petite barre par année, aussi haute que leur nombre (leurs titres au survol). Au survol, une barre
dit tout d'elle. Cliqué, ce qu'il est s'affiche sous la chronologie : une ligne de la carrière ou un doctorant, avec
**Modifier** (sa fenêtre : la chronologie redessinée) et un bouton vers son onglet ; un extrait, ou une année (ses
extraits), où un extrait cliqué ouvre son PDF à côté de la page, à l'extrait (sélectionné là), la chronologie
toujours affichée ; son crayon le modifie (sa fenêtre, comme dans l'onglet Extraits : ses années, ses marqueurs, son
texte… ; la chronologie redessinée), son bouton liste le montre dans l'onglet Extraits. L'extrait d'un article sans années est à l'année de l'article. La chronologie commence au plus
ancien de sa carrière, de ses thèses et de ses extraits (ses articles au plus trois ans avant ; les plus anciens
comptés en dessous). Elle s'ouvre agrandie (des années deux fois plus larges que pour tenir dans la largeur), défilée
jusqu'à sa période dans le dossier ; son curseur élargit ou resserre les années dès qu'on le déplace (le texte gardant sa taille, le milieu restant en vue), de la chronologie entière dans la largeur à quatre fois plus large ; la chronologie garde l'endroit où elle est défilée quand on clique un élément ou qu'elle est redessinée. Chaque sorte s'affiche ou se masque par son interrupteur ; celle dont
elle n'a aucun élément est signalée. Un extrait a son propre interrupteur (dans les détails en dessous, ou son bouton
dans l'onglet Extraits) : éteint, il est laissé hors de la chronologie (toujours gardé), listé à part en dessous, où
il se rallume. Les extraits se **filtrent** (les pastilles sous les interrupteurs) : par leurs marqueurs (saillant, rayonnement, mineur, bémol) et par leurs niveaux (ceux du dossier : local, national… ; un extrait sans niveau compte comme du niveau par défaut du dossier). Plusieurs pastilles d'une sorte : l'une d'elles ; les deux sortes : les deux ; aucune cochée : tous affichés. Les filtres s'appliquent à la chronologie, aux détails et aux listes en dessous ; le nombre d'extraits qu'ils masquent est indiqué, avec un bouton pour les effacer. Un dossier sans niveaux le signale à la place de leurs pastilles. Les extraits sans années, et les lignes de la carrière sans dates, sont listés à part, en dessous. La
chronologie prend toute la largeur : un PDF affiché à côté de la page est mis de côté pendant ce temps (toujours dans
le bandeau du bas ; celui ouvert depuis la chronologie s'affiche), et réaffiché en quittant l'onglet.

Les dossiers peuvent être **dans d'autres dossiers** : la page Rapports les montre en **arbre**, chaque dossier
déplié montrant les dossiers qu'il contient puis ses personnes, une ligne chacune (sa période, ses étoiles, ses
étiquettes, ses sources, ce qui demande un coup d'œil) ; replié, son nombre de personnes. Les dossiers dépliés ou
repliés (aussi tous à la fois, avec les boutons au-dessus de l'arbre) et celui sélectionné sont mémorisés. Un clic
sur un dossier le sélectionne (déplié) : ses actions et son filtre par étiquette sont dans la barre au-dessus, qui
reste en haut ; « Toutes les personnes (nettoyage) » liste chaque personne. Glissez un dossier sur un autre pour le mettre
dedans (sur son haut ou son bas : à côté), ou sur « Déposer ici » pour le premier niveau. Le menu ⋮ de chaque
dossier le renomme, ajoute un dossier dedans, le déplace (« Déplacer vers… ») et choisit ses réglages (« Ses
réglages… »). Les **réglages des dossiers** sont nommés, dans Paramètres → Réglages des dossiers. On les y crée
(vides, ou copie d'autres), renomme, modifie, rend par défaut et supprime (une fois qu'aucun dossier ne les
utilise et qu'aucun extrait n'est classé dans leurs catégories). Ils se modifient par sections, en onglets en
haut (aussi l'onglet « Paramètres » de la fenêtre du dossier, son onglet « Dossier » donnant son nom, sa date, ses
notes et les réglages qu'il utilise) : **Catégories** (leur arborescence, modifiée aussitôt), **Rapport** (les
modèles de rapport, la langue des rapports), **Extraits** (les étiquettes des marqueurs et si un extrait marqué
passe en premier, les niveaux, où vont les extraits de rayonnement), **Citations et sources** (la source
principale, les listes étoilées, la façon dont les notes numérotent et citent les articles, les modèles de
citation propres au dossier) et **Modèles (IA locale)** (un LLM et un modèle d'embeddings utilisés à la place de
ceux par défaut ; vide : ceux par défaut). Chaque dossier utilise les réglages choisis dans sa fenêtre ou son
menu (une icône dans l'arbre quand ce ne sont pas ceux par défaut), sinon ceux par défaut ; un dossier créé dans
un autre part de son choix, et un dossier déplacé garde ses réglages. Choisir d'autres réglages classe les extraits
de ses personnes dans les catégories de mêmes noms (ajoutées si elles manquent), leurs articles étoilés dans les
listes étoilées de mêmes noms. Des réglages utilisés par plusieurs dossiers changent pour tous : les dialogues des
catégories et des citations disent lesquels.

**Catégories de coauteurs** (par exemple « Collaborateurs int. ») : cliquez sur le nom d'un coauteur dans les
détails d'une publication pour le placer dans une catégorie (ou pour indiquer qu'il s'agit de la personne ou de l'un de
ses doctorants) ; ses liens « Rechercher sur… » le recherchent sur Google Scholar, Semantic
Scholar, DBLP, ORCID, HAL et le web (avec le titre de l'article), dans une nouvelle fenêtre. Les catégories
sont mises en évidence dans les listes d'auteurs et fournissent un filtre « avec … »
à côté de « avec un doctorant » ; gérez-les dans Paramètres → Étiquettes et catégories.

Les listes d'auteurs mettent en évidence la personne (en **gras**, en utilisant ses alias) et ses doctorants
(d'après theses.fr, avec des alias que vous pouvez ajouter dans l'onglet Thèses).

**Rôle de contribution** : le rôle de la personne dans chaque article (seul auteur, premier auteur, contributeur,
impliqué, encadrant, dernier auteur…), affiché globalement et par année ; cliquez sur une barre pour filtrer.
Les rôles et leurs règles se définissent dans Paramètres → Rôles de contribution : la première règle qui s'applique
l'emporte (sinon le rôle par défaut). La condition d'une règle utilise `n` (nombre d'auteurs), `p` (la
position de la personne) et `phd` (avec l'un de ses doctorants), par exemple
`n>=12 and p>=25% and p<=75%` ; une position négative se compte à partir de la fin (`p>=-3` : l'un des
trois derniers auteurs).

Les **thèses** (d'après theses.fr) affichent leur début et leur fin (soutenance) : theses.fr ne donne le début que des
thèses en cours, donc pour une thèse soutenue, c'est le début observé lorsqu'elle était en cours, sinon une
estimation (3 ans avant la soutenance, marquée ≈). Elles suivent la période du panneau :
encadrements qui la chevauchent, jurys dont la soutenance tombe dans la période.

Les **doctorants** se modifient à la main (onglet Thèses) : les dates d'un doctorant de theses.fr (posées sur les
siennes, gardées quand theses.fr est relu ; marquées ✎), et un doctorant ajouté à la main (nom, titre, encadrants,
dates). Retirer un doctorant ajouté à la main le supprime ; un doctorant de theses.fr est masqué à la place :
toujours listé (barré, avec un commentaire disant pourquoi), il peut être rétabli, et n'est compté nulle part (listes
d'auteurs, filtre « avec doctorant », rôles de contribution), même quand theses.fr est relu.

Les **paramètres** sont groupés (Sources, Canaux, Annotations, Rapports, IA, Données) dans une liste à gauche ;
le champ de recherche au-dessus trouve un réglage par ses mots. Une page aux modifications non enregistrées est
listée en dessous, avec ses Enregistrer / Annuler.

**IA locale** (facultative : Paramètres → IA). Les informations personnelles sont confidentielles : seuls des
modèles locaux sont utilisés, servis sur cette machine ou le réseau local (les autres serveurs sont refusés, les
modèles cloud d'Ollama jamais listés ni utilisés). **LLM local** l'active, avec son serveur (Ollama, ou un serveur à l'API compatible OpenAI, `/v1/chat/completions`, avec une clé d'API facultative, lue aussi dans `SCI_REPORT_ANALYZER_LLM_API_KEY`), le modèle
général, s'il réfléchit avant de répondre (par défaut), et les **embeddings** (d'Ollama ou d'un serveur à l'API
compatible OpenAI) qui suggèrent la catégorie d'un extrait (le plus proche des groupes d'extraits de chaque
catégorie : combien de groupes au plus, combien d'extraits dans un groupe au moins ; « Entraîner », dans le
sélecteur de catégorie, réapprend à partir des extraits classés). Chaque tâche a sa propre page, avec son modèle
(le modèle général sauf si un autre est choisi ; un petit, 4–8B, suffit souvent, et est bien plus rapide), s'il
réfléchit (« Par défaut » : non ; « Réfléchit » ; « Ne réfléchit pas ») et ses instructions (modifiées là ;
« Instructions par défaut » les rétablit) :

- **Lecture d'un parcours** : dans l'onglet Parcours d'une personne, « Lire dans ses documents », le LLM lit les
  pages de ses documents qui portent sur un parcours (trouvées par les mots réglés là, dans chaque langue) et
  suggère des lignes de son CV.
- **Réécriture des extraits** : un nouvel extrait est reformulé (court, impersonnel, sans ses dates) et ses années
  lues en lui et autour, proposé dans sa fenêtre, où son texte se modifie aussi tout de suite (la sélection gardée
  comme son original). « Aucun (pas de réécriture) » : seulement ses années, trouvées autour.
  Son bouton « Changer de LLM » fait la même réécriture avec d'autres modèles, l'un après l'autre, leurs
  formulations côte à côte avec leur durée (le premier passage d'un modèle compte son chargement) ; « Garder »
  en prend une, et peut faire de son modèle celui qui réécrit les extraits.
- **Lecture d'une liste de références** : dans « Étiqueter à partir d'une liste », « Lire avec le
  LLM » lit une liste collée en références (titre, auteurs, année, canal, DOI et lien), appariées par leur
  titre ; ce qui est entre elles (une note sur un article, un titre de section, des étiquettes) est laissé de
  côté, comme un titre absent de la liste ; chacune garde son numéro dans la liste (« P3. » : 3) ; une
  longue liste par parties.

Quand le LLM est désactivé (ou qu'une tâche n'a pas de modèle, ou n'obtient pas de réponse valide), la page de la
tâche, la fenêtre de l'extrait et celle d'Étiqueter à partir d'une liste le disent, avec un lien pour l'activer. Les réglages d'un dossier peuvent
utiliser d'autres modèles (leur section « Modèles (IA locale) »).

Des **sauvegardes** de la base de données (pas des PDF) sont faites dans `backups/` à côté de celle-ci (Paramètres →
Données et cache indique où) : une par jour au démarrage (les 7 dernières sont conservées), et une avant chaque mise à niveau de
son schéma (`pre-migration-…` ; une sauvegarde marquée `.pending` provient d'une mise à niveau qui a échoué, et est
toujours conservée). Pour en restaurer une, quittez l'application et copiez-la par-dessus le fichier de la base de données (en supprimant les
fichiers `-wal` et `-shm` situés à côté).
