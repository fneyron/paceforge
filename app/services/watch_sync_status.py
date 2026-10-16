"""Describe a partial import without claiming that the whole connection failed.

Only known provider outcomes are warnings. Authentication and unknown failures
remain actionable errors; a stored warning never proves that today's data arrived.
"""
from app.services import coros, garmin


def sync_notice(name: str, error: str | None, needs_reauth: bool = False) -> dict | None:
    if needs_reauth:
        return {"level": "error", "kind": "auth", "label": "Connexion à renouveler",
                "message": f"Reconnecte {name} dans Réglages."}
    if not error:
        return None
    if name == "COROS":
        remaining = coros._said(error)
        kinds = []
        for message, kind in ((coros.PARTIAL, "history"), (coros.HRV_PARTIAL, "hrv"),
                              (coros.HRV_UNREADABLE, "hrv_unreadable"), (coros.SLEEP_PARTIAL, "sleep"),
                              (coros.PHASES_PARTIAL, "phases"), (coros.ACTIVITIES_PARTIAL, "activities")):
            if message in remaining:
                remaining = remaining.replace(message, "").strip()
                kinds.append(kind)
        # Mixed or new error text must not be downgraded to a partial import.
        if kinds and not remaining:
            kind = kinds[0] if len(kinds) == 1 else "partial"
            label = {"history": "Historique incomplet", "hrv": "VFC incomplète",
                     "hrv_unreadable": "VFC indisponible", "sleep": "Sommeil incomplet",
                     "phases": "Phases du sommeil incomplètes", "activities": "Import des activités incomplet",
                     "partial": "Import incomplet"}[kind]
            return {"level": "warning", "kind": kind, "label": label,
                    "message": f"{name} : {label[0].lower() + label[1:] if not label.startswith('VFC') else label}."}
    elif name == "Garmin" and error == garmin.PARTIAL:
        return {"level": "warning", "kind": "partial", "label": "Import incomplet",
                "message": "Garmin : import incomplet."}
    return {"level": "error", "kind": "failure", "label": "Dernière synchro échouée",
            "message": f"{name} n’a pas pu être actualisé."}
