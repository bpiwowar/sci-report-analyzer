"""theses.fr (French PhD theses: supervision, reviewing, juries)."""

from __future__ import annotations

import re
from typing import Any

from ..i18n import N_, Labels
from .base import AuthorCandidate, FetchedThesis, FetchResult, SourceAdapter, get_json

API = "https://theses.fr/api/v1"
IDREF_RE = re.compile(r"\d{8}[\dX]")
ROLES = {
    "Directeur / Directrice": "director",
    "Rapporteur / Rapporteuse": "rapporteur",
    "Examinateur / Examinatrice": "examiner",
    "Président / Présidente du jury": "president",
    "Membre du jury": "examiner",
    "Auteur / Autrice": "author",
}
ROLE_LABELS = Labels(
    {
        "director": N_("Supervised"),
        "rapporteur": N_("Reviewer (rapporteur)"),
        "examiner": N_("Examiner"),
        "president": N_("Jury president"),
        "author": N_("Own thesis"),
        "other": N_("Other"),
    }
)


def _person(p: dict[str, Any]) -> str:
    return " ".join(x for x in (p.get("prenom"), p.get("nom")) if x)


def thesis_from(t: dict[str, Any], role: str) -> FetchedThesis:
    tid = t["id"]
    return FetchedThesis(
        thesis_id=tid,
        role=ROLES.get(role, "other"),
        title=t.get("titre"),
        student=", ".join(_person(a) for a in t.get("auteurs") or []) or None,
        supervisors=[_person(d) for d in t.get("directeurs") or []],
        status=t.get("status"),
        defence_date=t.get("date_soutenance"),
        start_date=t.get("date_inscription"),
        discipline=t.get("discipline"),
        institution=(t.get("etablissement_soutenance") or {}).get("nom"),
        url=f"https://theses.fr/{tid}",
    )


class ThesesFrAdapter(SourceAdapter):
    name = "thesesfr"
    label = "theses.fr"
    provides_theses = True
    provides_publications = False

    def profile_url(self, external_id: str) -> str:
        return f"https://theses.fr/{external_id}"

    def parse_url(self, url: str) -> str | None:
        m = re.search(
            r"(?:theses\.fr/(?:api/v1/personnes/personne/)?|^)(\d{8}[\dX])\b", url.strip()
        )
        return m.group(1) if m else None

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        data = await get_json(
            f"{API}/personnes/recherche/", params={"q": name, "debut": 0, "nombre": 10}
        )
        out = []
        for p in data.get("personnes") or []:
            # Records without an IdRef are loose name mentions in a single thesis: they have
            # no profile page (404) and cannot be fetched.
            if not p.get("has_idref") or not IDREF_RE.fullmatch(str(p.get("id", ""))):
                continue
            roles = p.get("roles") or {}
            out.append(
                AuthorCandidate(
                    source=self.name,
                    external_id=p["id"],
                    display_name=_person(p),
                    url=self.profile_url(p["id"]),
                    affiliation=", ".join(p.get("etablissements") or []) or None,
                    works_count=len(p.get("theses") or []),
                    extra={"roles": roles, "disciplines": p.get("disciplines") or []},
                )
            )
        return out

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        data = await get_json(f"{API}/personnes/personne/{external_id}")
        theses = []
        for role, items in (data.get("theses") or {}).items():
            theses.extend(thesis_from(t, role) for t in items or [])
        return FetchResult(theses=theses, display_name=_person(data))
