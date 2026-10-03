## Ce que fait SciReport Analyzer

SciReport Analyzer rassemble les publications des personnes que vous suivez depuis plusieurs sources, les
fusionne, et classe le **canal de publication** (revue ou conférence) de chaque article — les classements évaluent des canaux,
pas des articles individuels. Chaque personne dispose aussi de dossiers avec des périodes, des étiquettes, des notes, des PDF stockés
et des rapports, pour préparer une évaluation.

## Personnes et sources

1. **Ajoutez une personne** (nom, affiliation facultative). Chaque source est interrogée et les profils
   possibles sont listés comme *candidats* dans l'onglet **Sources**.
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

Une **source principale** (Paramètres → Sources ; un dossier peut en utiliser une autre, ou aucune) décide
quels articles comptent : un article qu'elle ne liste pas pour la personne est exclu du panneau, des rapports
et de la synthèse, par exemple HAL, où les chercheurs du CNRS doivent déposer leurs articles. L'interrupteur
*seulement ceux absents de HAL* les liste, pour les y ajouter. Les autres sources aident toujours à trouver
le canal et la session. Les personnes sans profil validé sur cette source ne sont pas concernées.

## Fusion

Les notices de différentes sources sont fusionnées lorsqu'elles partagent un DOI, ou ont le même titre
(ou presque) et des années proches. Les **prépublications** (arXiv, prépublications HAL…) sont rattachées à la
version publiée quelle que soit leur année. Si deux travaux différents ont été fusionnés, **séparez** une notice depuis les
détails ; si un travail apparaît deux fois, **fusionnez**-le avec l'autre. Les notices attribuées à tort peuvent
être **masquées**.

## Canaux : le type, puis le rang

Chaque publication est liée à un **canal** (voir la page *Canaux*), sauf les livres et les chapitres
de livres : leur texte de canal est leur propre titre, leur collection ou leur éditeur, ils n'en ont donc pas (sauf
s'ils y sont liés à la main).
Chaque source donne un texte de canal, qui est apparié à un canal :

1. Le texte est nettoyé par les **règles de normalisation** (Paramètres : expressions régulières,
   éventuellement pour certaines sources seulement, qui suppriment les années, les ordinaux, les plages de pages…).
2. Il appartient au canal qui l'a comme **variante** (même texte nettoyé) ; une variante ajoutée
   à la main l'emporte sur tout le reste. Sinon, une **règle de canal** (une expression régulière sur le texte de la source,
   dans l'onglet Appariement du canal) peut le revendiquer ; sinon, un nouveau canal est créé.
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

**Sources utilisées.** Paramètres → Appariement choisit les sources de publications utilisées par l'application ; une source désactivée
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
l'article l'emporte, comme toujours.

Tout peut être décidé à la main, **pour le canal** (par défaut) ou **pour un seul article** (cochez
« Seulement pour cet article ») : lier un article à un autre canal, fixer son type, choisir une
entrée de classement ou fixer un niveau (pour un seul article, en indiquant pourquoi). L'année d'un article et la position
de l'auteur peuvent aussi être corrigées. Les décisions manuelles sont signalées et
jamais écrasées par les traitements automatiques ni par les resynchronisations. Sur les pages des canaux, vous pouvez aussi
**fusionner** des variantes qui désignent le même canal (en donnant au résultat un nouveau nom si vous le souhaitez), ou
en séparer une ; **Proposer des fusions…** parcourt les canaux qui se ressemblent, une paire à la
fois (une paire déclarée *pas la même* n'est plus proposée). Les décisions sur les canaux font partie des
paramètres exportés.

## Catégories et niveaux

<!-- levels -->

Les sessions satellites (Findings, démos, articles courts…) et les ateliers sont affichés comme des catégories
hachurées distinctes, par exemple « Findings CORE A » ou « Atelier A* ». Les **signalements** associés à une session (court,
démo…) placent un article dans une telle catégorie ; les autres signalements sont de simples libellés sur lesquels filtrer.
Lorsque les sources ne divergent que parce que certaines donnent une session (démo, court…) et d'autres la session normale,
la session l'emporte (démo, Findings, atelier…), quel que soit le canal donné par chacune. Des
sessions différentes (par exemple démo et court) sont un problème à régler : validez une source dans les détails, ou
signalez l'article. De même, un atelier
l'emporte sur sa conférence principale (par exemple une notice DOI donnant EMNLP pour un article de BlackboxNLP).
Les actes édités (présidence) conservent le rang de leur canal dans des catégories propres, par exemple
« Actes (éd.) CORE A* » ; un volume édité sans canal est apparié par son titre.

## Panneau

Cliquez sur une barre ou une entrée de légende pour filtrer de façon croisée : les autres graphiques et la liste sont restreints
à la sélection. Un article non classé affiche plutôt son type (par exemple « Conf. nat. », « Livre »).

Cliquez sur une publication pour ouvrir ses détails :

- **Publication** : liens, signalements, auteurs (confirmer les correspondances de noms), sources (séparer / fusionner).
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

**Étiquettes et notes** (détails d'un article, onglet « Étiquettes et notes ») : saisissez un nom pour créer une étiquette. Une étiquette
**globale** reste attachée à l'article ; une étiquette **propre à une période** (⏱) est posée séparément dans chaque
période / dossier. « étoilé » (le bouton ★) est une telle étiquette. Renommez et recolorez les étiquettes avec le
bouton 🏷 ou dans Paramètres → Signalements, étiquettes et catégories. Chaque article a une **note** (Markdown),
plus une note propre à la période sélectionnée. Le filtre « étiquettes » affiche les articles ayant l'une
des étiquettes choisies ; pour écrire à leur sujet, utilisez l'éditeur de rapport (ci-dessous).

**Étiqueter à partir d'une liste** (le bouton liste du panneau) : collez une liste de publications, par exemple la liste
numérotée d'un rapport copiée depuis un PDF. Chaque élément est apparié à un article par son identifiant HAL ou son DOI, sinon
par son titre (les auteurs et le canal qui l'entourent n'importent pas) ; vérifiez les correspondances, ajoutez les
articles qui manquent aux sources (par l'identifiant HAL / DOI de l'élément, ou trouvés sur HAL), puis posez une étiquette (par exemple
étoilé, propre à la période) sur ceux-ci avec leurs **numéros** dans la liste. Filtrer sur cette étiquette
affiche les articles dans l'ordre de la liste (#3 sur leur étiquette), et la liste 🗒 les numérote et
se termine par les éléments non trouvés.

Les notes sont en Markdown, avec **LaTeX** : `$x^2$` en ligne, `$$\sum_i x_i$$` centré sur sa ligne.
Un saut de ligne est conservé (comme dans Obsidian). Elles sont éditées avec un éditeur Markdown (barre d'outils,
⌘B / ⌘I, un aperçu à côté ou à la place).

**Rapport** (✒ dans le panneau, une période / un dossier étant choisi ; un nouvel onglet) : un rapport Markdown sur
la personne, citant ses articles avec la syntaxe de Pandoc : `[@key]` le numéro de l'article (`**#6**`,
le format peut être modifié), `[@a; @b]` plusieurs, `@key` ses titre, canal, année et catégorie,
`[@key]{.notes}` avec ses notes, `[@key]{.tags}` avec ses étiquettes (`{.notes .tags}` les deux), et des
modèles : `[@key]{.short-venue (.year)}` donne `EMNLP (2026)` (champs : `.number`, `.index`
le numéro seul comme dans `{#.index}`, `.title`, `.venue`, `.short-venue` l'acronyme, `.year`,
`.tags`, `.notes`, comme dans `{**#.index** (.short-venue .year): .notes}` ; un crochet sans
valeur est supprimé). « Citer comme » choisit comment un article est inséré quand on clique dessus dans
le panneau latéral (ou avec Enter), parmi les modèles définis dans Paramètres → Modèles de rapport. Les
articles à discuter sont ceux qui ont certaines étiquettes (sinon ceux des années de la période), numérotés selon
la liste à partir de laquelle une étiquette a été posée (sinon par année) ; le panneau latéral les liste, en rouge tant qu'ils ne sont pas cités, avec une recherche
rapide (⌘K / Ctrl-K, Enter cite la première correspondance ; les autres articles de la personne aussi). Les articles
étiquetés hors des années de la période sont listés à part, en bas (en orange tant qu'ils ne sont pas cités), et
comptés à part. « Les citer » insère ceux de la période qui ne sont pas encore discutés. Copier (ou télécharger)
remplace les citations, éventuellement suivies de la liste des articles. Le rapport est enregistré
au fil de la saisie.

**PDF** : l'icône PDF à côté d'un titre ouvre le PDF stocké de l'article dans une nouvelle fenêtre (rouge ;
✎ une fois annoté). Une icône grise signifie qu'un lien en accès ouvert (ou un DOI) est connu : cliquez dessus pour
télécharger le PDF (après confirmation, sauf si elle est désactivée) et l'ouvrir. La visionneuse (PDF.js,
installée une seule fois) dispose d'outils de surlignage, de texte, de dessin et d'image ; les annotations sont enregistrées dans le
PDF stocké (toutes les quelques secondes, avec Enregistrer ou ⌘S/Ctrl+S). ← / → permettent d'aller et venir après
avoir suivi un lien dans le PDF. Le bouton du panneau latéral affiche à côté les étiquettes et les notes de l'article
(y compris celles d'une période), ses détails (comme dans le panneau des publications) et ses signets ; 🔖+ crée un signet sur la sélection (sinon sur l'endroit affiché), et 🔍 trouve l'article du
texte sélectionné (parmi les articles de la personne, sinon sur HAL, pour l'ajouter) ; les actions nécessitant une
sélection sont grisées en l'absence de sélection. Une zone d'une page sert aussi de sélection (une figure, un tableau, un
passage sur plusieurs colonnes) : activez l'outil zone (le cadre en pointillés dans l'en-tête, ou **A**) et
tracez un rectangle, ou Alt+glisser ; son texte constitue la sélection, et le rectangle son emplacement
(Escape, ou un clic ailleurs, l'abandonne). Raccourcis (une seule touche, sauf pendant la saisie) : **H** surligne
la sélection (sinon active ou désactive l'outil de surlignage), **T** texte, **D** dessin, **I**
image (activer ou désactiver), **A** zone, **B** signet, **F** trouver l'article, **E** ajouter à une
catégorie ; ceux de PDF.js : ⌘F / Ctrl+F recherche, + / − zoom, N / P (ou J / K) page suivante et
précédente, R rotation, Home / End, Delete supprime l'annotation sélectionnée. Le bouton ⬇ du panneau
télécharge les PDF de tous les articles affichés ; « Stocker un PDF » (détails) en téléverse un. Paramètres →
Données et cache affiche l'espace utilisé et supprime les fichiers des articles qui ne sont plus dans la base de données. Un
article qui disparaît des sources emporte avec lui son PDF téléchargé ; un PDF téléversé ou annoté
conserve l'article.

**Documents** (onglet Documents de la personne) : PDF concernant la personne au sein d'un dossier ou d'une période (un
dossier de candidature, un CV…), téléversés là et lus dans la même visionneuse (annotations, signets, une
note). Les articles de la personne mentionnés dans un document sont retrouvés (par le titre, quelques coquilles
tolérées, ou par DOI / identifiant HAL) et deviennent des liens (un léger soulignement pointillé ; rouge lorsque le PDF de
l'article est stocké). Les références
numérotées (« [11] », « 11. », « [C3] ») font aussi pointer leurs citations (« [11] », « [3, 11] », « [3-5] »)
vers l'article, tout comme les citations auteur-année (« (Lyu et al., 2023b) », « Gari Soler and
Apidianaki, 2021, 2020 ») appariées aux références trouvées (premier auteur, année et lettre).
Un clic affiche les détails de l'article à côté du document,
comme dans le panneau des publications (étiquettes, notes, son PDF à consulter en ligne, télécharger ou stocker) ; « Pas cet
article » oublie une correspondance erronée. Une référence qui n'a pas été trouvée : sélectionnez-la, 🔍, puis « Lier
ici ». L'onglet des articles les liste (un champ de recherche le filtre : chaque mot, dans le titre,
les auteurs, le canal ou l'année). La note du document cite les articles de la personne comme le font les rapports
(`[@key]`, le bouton ❝ pour en choisir un, ceux du document en premier) ; son aperçu et sa copie
les numérotent (« [1] », dans l'ordre de première citation), la copie se terminant par les références.

**Catégories** (page du dossier : le bouton des catégories ; ou l'onglet des catégories de la visionneuse) :
l'arborescence ordonnée des catégories d'un dossier (par exemple une grille d'évaluation), que l'on fait glisser pour les ordonner et les imbriquer,
chacune avec des années facultatives ; une catégorie contenant des extraits (directement ou en dessous, de n'importe quelle personne du dossier)
n'est supprimée qu'une fois ceux-ci déplacés vers une autre, choisie à ce moment-là. Dans le PDF d'un document (un rapport,
un dossier de candidature… pas celui d'un article), sélectionnez un passage (ou cliquez sur un surlignage), puis « ajouter à une
catégorie » (en-tête, ou touche E) : cliquez sur une catégorie, ou tapez pour la trouver (Enter : la première ;
un nouveau nom la crée, la boîte de dialogue restant ouverte), avec les années concernées et, le cas échéant, si
elle témoigne du rayonnement de la personne (« rayonnement ») ; lorsqu'il pourrait déjà s'agir d'un extrait
existant (mots similaires, signalé par un avertissement ; ou trouvé en tapant ses mots), fusionnez-le plutôt avec
celui-ci, en conservant son texte ou seulement son emplacement (une référence). L'onglet des catégories liste
les extraits de la personne (clic : aller au passage), pour les modifier (texte, années, rayonnement, couleur :
sa teinte sur le PDF ; les passages tels que sélectionnés sont affichés, pour y revenir ou les ajouter au texte),
les déplacer (avec le même sélecteur), les fusionner (en faisant glisser l'un sur l'autre, ou avec son icône de fusion puis un clic sur
l'autre : regroupés, chacun avec sa propre citation ou seulement son emplacement, son icône lien / citation basculant de l'un à l'autre,
sur un seul élément avec la catégorie, les années… de l'autre ; le crayon du groupe modifie son texte d'ensemble,
ses extraits étant alors cités uniquement par leurs emplacements, tous toujours teintés sur le PDF ; à nouveau sortis
avec son icône de séparation), les réordonner (en les faisant glisser sur le bord supérieur ou inférieur d'un autre), les supprimer,
ou les copier en Markdown (sans guillemets, leurs emplacements dans des commentaires Obsidian : `%% Application, p. 4;
p. 12 %%`, puis leurs années ; ceux qui témoignent du rayonnement sont aussi listés, par catégorie, dans une dernière
section « Rayonnement » ; l'icône de badge nomme les documents, par exemple un long nom de fichier sous la forme
« Application ») ;
le bouton ❝ du rapport les insère, par catégorie.

Les **dossiers** (page Personnes) regroupent des personnes, par exemple pour un comité de sélection : un dossier a un nom,
une date et peut être masqué. Chaque personne a sa **propre période** dans chacun de ses dossiers
(définie dans son onglet Périodes), avec ses propres étoiles, étiquettes et notes ; ouvrir une personne depuis un
dossier sélectionne celle-ci.

**Catégories de coauteurs** (par exemple « Collaborateurs int. ») : cliquez sur le nom d'un coauteur dans les
détails d'une publication pour le placer dans une catégorie (ou pour indiquer qu'il s'agit de la personne ou de l'un de
ses doctorants) ; ses liens « Rechercher sur… » le recherchent sur Google Scholar, Semantic
Scholar, DBLP, ORCID, HAL et le web (avec le titre de l'article), dans une nouvelle fenêtre. Les catégories
sont mises en évidence dans les listes d'auteurs et fournissent un filtre « avec … »
à côté de « avec un doctorant » ; gérez-les dans Paramètres → Signalements, étiquettes et catégories.

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

Des **sauvegardes** de la base de données (pas des PDF) sont faites dans `backups/` à côté de celle-ci (Paramètres →
Données et cache indique où) : une par jour au démarrage (les 7 dernières sont conservées), et une avant chaque mise à niveau de
son schéma (`pre-migration-…` ; une sauvegarde marquée `.pending` provient d'une mise à niveau qui a échoué, et est
toujours conservée). Pour en restaurer une, quittez l'application et copiez-la par-dessus le fichier de la base de données (en supprimant les
fichiers `-wal` et `-shm` situés à côté).
