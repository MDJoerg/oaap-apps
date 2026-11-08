"""Die Assistenten des Chats und wer sie sieht (RFC-0074 D2, D4).

Die Assistenten liegen in den Daten des Chats, im Austauschformat von
oaap.ai.assistants 0.1 (`{"v": 1, "assistants": [...]}`) -- die Plattform
speichert keine. Dazu kommt höchstens einer aus der Konfiguration
(`CHAT_ASSISTANT`), den nur der Betreiber ändert.

Sichtbarkeit (D4), über die fachlichen Berechtigungen (RFC-0045):

| scope   | sieht                                   | verwaltet                          |
|---------|-----------------------------------------|------------------------------------|
| private | nur `owner`                             | nur `owner`                        |
| context | wer `assistant.use` im Team hat         | wer `assistant.manage` im Team hat |
| tenant  | jede Person, die den Chat öffnen darf   | `tenant_admin`                     |

Ein Kontext ist in dieser Fassung ein Team (`{team: <kennung>}`) -- das Feld,
das der Chat im Manifest erklärt. Sind die Berechtigungen nicht lesbar, sieht
niemand einen Kontext-Assistenten (geschlossen scheitern, wie authz_client).
Sichtbarkeit ist eine Frage der Ordnung, keine Sicherheitsgrenze: Was ein
Assistent tun kann, begrenzen Sicht und Rechte der sprechenden Person (D1).
"""
import json
import os
import threading

import assistants
from authz_client import may_in

CONTEXT_FIELD = "team"
_LOCK = threading.Lock()


class Who:
    """Wer fragt: Kennung, Plattformrollen, fachliche Berechtigungen (oder None)."""

    def __init__(self, uid, roles, grants):
        self.uid, self.roles, self.grants = uid, set(roles), grants


def _team(a):
    ctx = (a.get("visibility") or {}).get("context") or {}
    return ctx.get(CONTEXT_FIELD)


def can_see(a, who):
    scope = (a.get("visibility") or {}).get("scope")
    if scope == "tenant":
        return True
    if scope == "private":
        return bool(who.uid) and a.get("owner") == who.uid
    if scope == "context":
        team = _team(a)
        return bool(team) and (may_in(who.grants, "assistant.use", team=team)
                               or may_in(who.grants, "assistant.manage", team=team))
    return False


def can_manage(a, who):
    scope = (a.get("visibility") or {}).get("scope")
    if scope == "tenant":
        return "tenant_admin" in who.roles
    if scope == "private":
        return bool(who.uid) and a.get("owner") == who.uid
    if scope == "context":
        team = _team(a)
        return bool(team) and may_in(who.grants, "assistant.manage", team=team)
    return False


def problems(a):
    """Die Sätze von oaap.ai.assistants, dazu was nur dieser Chat verlangt."""
    errs = assistants.check(a)
    v = a.get("visibility") if isinstance(a, dict) else None
    if not errs and v.get("scope") == "context" and set(v["context"]) != {CONTEXT_FIELD}:
        errs.append(f"visibility: dieser Chat kennt als Kontext nur {CONTEXT_FIELD} "
                    f"(z. B. {{\"{CONTEXT_FIELD}\": \"service\"}}).")
    return errs


class Store:
    def __init__(self, path, configured=None):
        self.path = path
        self.configured = configured      # (Assistent, Fehler) aus CHAT_ASSISTANT oder None

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
        except FileNotFoundError:
            return []
        return [a for a in doc.get("assistants") or [] if isinstance(a, dict)]

    def _write(self, items):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": assistants.VERSION, "assistants": items}, f, ensure_ascii=False,
                      indent=1)
        os.replace(tmp, self.path)

    def all(self):
        """(Assistent, verwaltet von 'config' oder 'chat') -- die aus der Konfiguration zuerst."""
        out = []
        if self.configured and not self.configured[1]:
            out.append((self.configured[0], "config"))
        ids = {a["id"] for a, _ in out}
        out += [(a, "chat") for a in self._read() if a.get("id") not in ids]
        return out

    def get(self, aid):
        for a, origin in self.all():
            if a.get("id") == aid:
                return a, origin
        return None, None

    def visible(self, who):
        return [(a, origin) for a, origin in self.all() if can_see(a, who)]

    def save(self, a, who, new_owner=True):
        """Anlegen oder ersetzen. Gibt [] oder Sätze zurück."""
        if isinstance(a, dict) and new_owner and (a.get("visibility") or {}).get("scope") == "private":
            a = dict(a, owner=who.uid)
        elif isinstance(a, dict) and not a.get("owner"):
            a = dict(a, owner=who.uid)
        errs = problems(a)
        if errs:
            return errs
        with _LOCK:
            old, origin = self.get(a["id"])
            if origin == "config":
                return ["Diesen Assistenten pflegt der Betreiber in der Konfiguration "
                        "(CHAT_ASSISTANT)."]
            if old is not None and not can_manage(old, who):
                return [f"Den Assistenten {a['id']} darfst du nicht ändern."]
            if not can_manage(a, who):
                return ["So darfst du keinen Assistenten anlegen: "
                        + {"tenant": "für den ganzen Mandanten nur als tenant_admin.",
                           "context": "für ein Team nur, wer dort Assistenten verwalten darf.",
                           }.get(a["visibility"]["scope"], "nur für dich selbst.")]
            items = [x for x in self._read() if x.get("id") != a["id"]] + [a]
            self._write(items)
        return []

    def delete(self, aid, who):
        with _LOCK:
            old, origin = self.get(aid)
            if old is None:
                return ["Kein solcher Assistent."]
            if origin == "config":
                return ["Diesen Assistenten pflegt der Betreiber in der Konfiguration."]
            if not can_manage(old, who):
                return [f"Den Assistenten {aid} darfst du nicht löschen."]
            self._write([x for x in self._read() if x.get("id") != aid])
        return []

    def export(self, who):
        return {"v": assistants.VERSION,
                "assistants": [a for a, _ in self.all() if can_manage(a, who)]}

    def import_document(self, doc, who):
        """Alles oder nichts: erst jeden prüfen, dann speichern."""
        errs = assistants.check_document(doc)
        if errs:
            return errs
        for i, a in enumerate(doc["assistants"]):
            errs += [f"[{i}] {e}" for e in problems(a)]
            old, origin = self.get(a["id"])
            if origin == "config" or (old is not None and not can_manage(old, who)):
                errs.append(f"[{i}] {a['id']}: gibt es schon, und du darfst ihn nicht ersetzen.")
            elif not can_manage(a, who) and not ((a.get("visibility") or {}).get("scope") == "private"):
                errs.append(f"[{i}] {a['id']}: so darfst du keinen Assistenten anlegen.")
        if errs:
            return errs
        for a in doc["assistants"]:
            errs += self.save(a, who)
        return errs
