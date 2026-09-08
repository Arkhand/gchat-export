#!/usr/bin/env python3
"""Baja los mensajes de Google Chat a texto plano, particionados por mes y space.

Solo baja. No resume: eso se hace despues leyendo la salida.

Pensado para correr standalone y ser invocado por otra cosa (MCP, skill, cron):
no pide nada por consola salvo la primera autorizacion, y con --json escupe un
resumen parseable de que archivos escribio.

Uso tipico:
    python -u gchat_export.py --list-spaces      # ver que hay (y con que cuenta)
    python -u gchat_export.py                    # mes actual
    python -u gchat_export.py --month 2026-08
    python -u gchat_export.py --days 30

Salida:
    out/2026-08/Proyecto-X-a1b2c3.md
    out/2026-08/Marce-Gomez-d4e5f6.md

Cada archivo declara en su cabecera que rango cubre, asi una segunda corrida
solo baja lo que falta. Ver --force para ignorar eso.

Exit codes: 0 ok · 1 error duro · 2 parcial (algun space sin acceso).

Necesita credentials.json (OAuth client tipo Desktop) en el mismo directorio.
El token queda en token.json; la cuenta sale de ahi, no de un parametro.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    # La Chat API no devuelve nombres de personas en ningun endpoint: ni en
    # sender.displayName ni en las membresias, solo users/NNN. Los nombres
    # salen del directorio de Workspace via People API.
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/directory.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
HERE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(HERE, "credentials.json")
TOKEN = os.path.join(HERE, "token.json")
# Cache de users/NNN -> nombre. Evita cientos de llamadas por corrida.
PEOPLE_CACHE = os.path.join(HERE, "personas.json")

# Marca en la cabecera de cada .md con el rango realmente cubierto.
HEADER_RE = re.compile(
    r"<!-- gchat-export space=(?P<space>\S+) cubre=(?P<since>\S+)/(?P<until>\S+) "
    r"bajado=(?P<at>\S+) -->"
)


# ---------------------------------------------------------------- auth

def get_creds(reauth=False, quiet=False):
    if reauth and os.path.exists(TOKEN):
        os.remove(TOKEN)
        if not quiet:
            print("[i] token.json borrado; hay que volver a autorizar.")

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[!] No se pudo refrescar el token ({e}); re-autenticando.", file=sys.stderr)
                creds = None
        if not creds or not creds.valid:
            if not os.path.exists(CREDS):
                sys.exit(f"[X] Falta {CREDS}. Ver SETUP.md, paso 4.")
            print(
                "[i] Abri la URL de abajo con la cuenta de Workspace (no el Gmail personal).",
                file=sys.stderr,
            )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def whoami(creds):
    """Email de la cuenta autorizada, para no bajar con la cuenta equivocada."""
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return svc.userinfo().get().execute().get("email") or "?"
    except Exception:
        return "?"


# ---------------------------------------------------------------- API

def list_spaces(svc):
    spaces, token = [], None
    while True:
        resp = svc.spaces().list(pageSize=1000, pageToken=token).execute()
        spaces.extend(resp.get("spaces", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return spaces


def list_messages(svc, space_name, since_iso, until_iso):
    flt = f'createTime > "{since_iso}" AND createTime < "{until_iso}"'
    msgs, token = [], None
    while True:
        resp = (
            svc.spaces()
            .messages()
            .list(parent=space_name, filter=flt, pageSize=1000, pageToken=token)
            .execute()
        )
        msgs.extend(resp.get("messages", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return msgs


# ---------------------------------------------------------------- personas

def cache_name(entry):
    """El cache guarda {nombre,email}; las versiones viejas guardaban un str."""
    if isinstance(entry, dict):
        return entry.get("nombre") or ""
    return entry or ""


def cache_email(entry):
    return entry.get("email", "") if isinstance(entry, dict) else ""


def load_people_cache():
    try:
        with open(PEOPLE_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_people_cache(cache):
    try:
        tmp = PEOPLE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, PEOPLE_CACHE)
    except Exception as e:
        print(f"[!] No se pudo guardar {PEOPLE_CACHE}: {e}", file=sys.stderr)


def resolve_names(creds, user_ids, cache, log=print):
    """users/NNN -> nombre via People API, cacheando en disco.

    La Chat API no devuelve nombres (ni en sender ni en membresias), asi que
    los buscamos en el directorio de Workspace. El cache evita repetir cientos
    de llamadas en cada corrida.
    """
    faltan = sorted(u for u in user_ids if u and u not in cache)
    if not faltan:
        return cache
    try:
        people = build("people", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        log(f"[!] No se pudo usar People API: {e}")
        return cache

    log(f"[i] Resolviendo {len(faltan)} nombres nuevos...")
    fallados = 0
    # getBatchGet acepta hasta 50 por llamada.
    for i in range(0, len(faltan), 50):
        lote = faltan[i:i + 50]
        try:
            resp = people.people().getBatchGet(
                resourceNames=[u.replace("users/", "people/") for u in lote],
                personFields="names,emailAddresses",
            ).execute()
        except HttpError as e:
            fallados += len(lote)
            if i == 0:  # con que avise una vez alcanza
                log(f"[!] People API fallo: HTTP {e.resp.status}")
            continue
        for r in resp.get("responses", []):
            per = r.get("person") or {}
            rn = per.get("resourceName") or r.get("requestedResourceName") or ""
            uid = rn.replace("people/", "users/")
            nombre = ""
            for n in per.get("names") or []:
                nombre = (n.get("displayName") or "").strip()
                if nombre:
                    break
            if not nombre:
                for em in per.get("emailAddresses") or []:
                    nombre = (em.get("value") or "").split("@")[0]
                    if nombre:
                        break
            email = ""
            for em in per.get("emailAddresses") or []:
                email = (em.get("value") or "").strip()
                if email:
                    break
            if uid and nombre:
                cache[uid] = {"nombre": nombre, "email": email} if email else nombre
    if fallados:
        log(f"[!] {fallados} nombres no se pudieron resolver; quedan como users/NNN.")
    save_people_cache(cache)
    return cache


def list_members(svc, space_name):
    """{users/NNN: nombre} de un space. El nombre viene vacio casi siempre:
    la Chat API no lo manda, se completa despues con resolve_names()."""
    people, token = {}, None
    while True:
        try:
            resp = (
                svc.spaces()
                .members()
                .list(parent=space_name, pageSize=1000, pageToken=token)
                .execute()
            )
        except HttpError:
            return people
        for ms in resp.get("memberships", []):
            u = ms.get("member") or {}
            uid = u.get("name")
            if not uid or u.get("type") != "HUMAN":
                continue
            people[uid] = (u.get("displayName") or "").strip()
        token = resp.get("nextPageToken")
        if not token:
            return people


def my_user_id(my_email, cache, members_by_space, log=print):
    """Cual de los users/NNN soy yo. Sin esto los DMs se nombran con mi propio
    nombre en vez del de la otra persona.

    Por email es exacto (people/me necesitaria el scope 'profile', que no
    pedimos). Si el directorio no devolvio mi email, cae a la interseccion:
    yo soy el unico miembro que aparece en todos los spaces.
    """
    if my_email:
        for uid, entry in cache.items():
            if cache_email(entry).lower() == my_email.lower():
                return uid

    conjuntos = [set(p) for p in members_by_space.values() if p]
    if not conjuntos:
        return None
    comun = set.intersection(*conjuntos)
    return next(iter(comun)) if len(comun) == 1 else None


def dm_counterpart(people, me_id, my_name=None):
    """En un DM, el nombre del otro. None si no se puede determinar."""
    otros = [n for uid, n in people.items() if uid != me_id and n]
    if len(otros) == 1:
        return otros[0]
    # Si no supimos cual era mi id, al menos descartar por nombre: un DM que
    # se llama como uno mismo no le sirve a nadie.
    if my_name:
        otros = [n for n in otros if n != my_name]
        if len(otros) == 1:
            return otros[0]
    return None


# ---------------------------------------------------------------- nombres

def space_title(sp, people=None, me_id=None, my_name=None):
    name = (sp.get("displayName") or "").strip()
    if name:
        return name
    kind = sp.get("spaceType") or sp.get("type") or ""
    # Los DMs no traen displayName: los nombramos por la otra persona.
    if kind == "DIRECT_MESSAGE" and people:
        otro = dm_counterpart(people, me_id, my_name)
        if otro:
            return otro
    if kind == "GROUP_CHAT" and people:
        otros = sorted(n for uid, n in people.items()
                        if uid != me_id and n and n != my_name)
        if otros:
            corte = ", ".join(otros[:3])
            return corte + (f" +{len(otros) - 3}" if len(otros) > 3 else "")
    return {"DIRECT_MESSAGE": "DM sin nombre", "GROUP_CHAT": "Grupo sin nombre"}.get(
        kind, "Sin nombre"
    )


def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text.strip())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:60] or "space"


def space_filename(sp, people=None, me_id=None, my_name=None):
    """Nombre legible + sufijo del ID: dos spaces distintos nunca se pisan.

    El sufijo sale del ID del space, asi que el archivo es estable aunque la
    persona se cambie el nombre o se renombre el space.
    """
    sid = sp.get("name", "")
    suffix = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:6]
    return f"{slugify(space_title(sp, people, me_id, my_name))}-{suffix}.md"


# ---------------------------------------------------------------- fechas

def months_between(since, until):
    """Meses (date del dia 1) tocados por [since, until). until es exclusivo."""
    out, cur = [], since.replace(day=1)
    last = (until - dt.timedelta(days=1)).replace(day=1)
    while cur <= last:
        out.append(cur)
        cur = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def month_bounds(m):
    nxt = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return m, nxt


def iso_z(d):
    return f"{d.isoformat()}T00:00:00Z"


# ---------------------------------------------------------------- estado

def read_header(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                m = HEADER_RE.search(line)
                if m:
                    return {
                        "since": dt.date.fromisoformat(m.group("since")),
                        "until": dt.date.fromisoformat(m.group("until")),
                    }
    except Exception:
        pass
    return None


def already_covered(path, since, until):
    """True si el archivo ya cubre [since, until) entero."""
    h = read_header(path)
    return bool(h) and h["since"] <= since and h["until"] >= until


# ---------------------------------------------------------------- render

def sender_of(m, people=None):
    s = m.get("sender") or {}
    uid = s.get("name")
    nom = (s.get("displayName") or "").strip()
    if nom:
        return nom
    # Con auth de usuario displayName casi siempre viene vacio: lo sacamos
    # de la membresia del space.
    if people and uid and uid in people:
        return people[uid]
    return uid or "?"


def body_of(m):
    text = (m.get("text") or m.get("formattedText") or "").strip()
    bits = [text] if text else []
    for att in m.get("attachment") or []:
        fname = att.get("contentName") or "adjunto"
        bits.append(f"[adjunto: {fname}]")
    if not bits and m.get("cardsV2"):
        bits.append("(card / mensaje de app)")
    return " ".join(bits) or "(sin texto)"


def indent(text, pad):
    return text.replace("\n", "\n" + pad)


def render(sp, msgs, since, until, now_iso, people=None, me_id=None, my_name=None):
    """Markdown de un space para un mes. Agrupa por dia y por hilo."""
    title = space_title(sp, people, me_id, my_name)
    kind = sp.get("spaceType") or sp.get("type") or "?"

    # Primera aparicion de cada hilo dentro de lo que bajamos. Si un hilo
    # arranca mas tarde en el archivo pero se retoma antes, igual queremos
    # saber cual fue su primer mensaje visible.
    first_seen = {}
    for m in sorted(msgs, key=lambda m: m.get("createTime") or ""):
        tid = (m.get("thread") or {}).get("name")
        if tid and tid not in first_seen:
            first_seen[tid] = m.get("name")

    lines = [
        f"<!-- gchat-export space={sp['name']} cubre={since.isoformat()}/{until.isoformat()} "
        f"bajado={now_iso} -->",
        f"# {title}",
        "",
        f"{kind} - {len(msgs)} mensajes - {since.isoformat()} a {until.isoformat()}",
        "",
    ]

    by_day = defaultdict(list)
    for m in msgs:
        by_day[(m.get("createTime") or "")[:10]].append(m)

    for day in sorted(by_day):
        lines += [f"## {day}", ""]
        day_msgs = sorted(by_day[day], key=lambda m: m.get("createTime") or "")

        # Agrupar por hilo, respetando el orden de aparicion dentro del dia.
        threads, order = defaultdict(list), []
        for m in day_msgs:
            tid = (m.get("thread") or {}).get("name") or m.get("name")
            if tid not in threads:
                order.append(tid)
            threads[tid].append(m)

        for tid in order:
            group = threads[tid]
            head, rest = group[0], group[1:]
            hhmm = (head.get("createTime") or "")[11:16]
            # Si el hilo ya se habia visto otro dia, esto es continuacion; el
            # arranque esta mas arriba en este archivo o en un mes anterior.
            cont = " (sigue un hilo anterior)" if first_seen.get(tid) not in (None, head.get("name")) else ""
            lines.append(f"**{hhmm} {sender_of(head, people)}**:{cont} {indent(body_of(head), '  ')}")
            for m in rest:
                hhmm = (m.get("createTime") or "")[11:16]
                lines.append(f"  - **{hhmm} {sender_of(m, people)}**: {indent(body_of(m), '    ')}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_atomic(path, content):
    """Temporal + rename: un Ctrl+C no deja un .md trunco haciendose pasar por completo."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def drop_renamed(mdir, path):
    """Borra la version vieja de este space si cambio de nombre.

    El sufijo del archivo sale del ID del space, asi que un mismo id con otro
    nombre es el mismo space renombrado (p.ej. un DM que antes no tenia nombre).
    Sin esto quedan dos .md con el mismo contenido.
    """
    base = os.path.basename(path)
    m = re.match(r"^.*-([0-9a-f]{6})\.md$", base)
    if not m or not os.path.isdir(mdir):
        return
    suf = m.group(1)
    for f in os.listdir(mdir):
        if f != base and f.endswith(f"-{suf}.md"):
            try:
                os.remove(os.path.join(mdir, f))
            except OSError:
                pass


# ---------------------------------------------------------------- main

def main():
    today = dt.date.today()
    ap = argparse.ArgumentParser(
        description="Baja mensajes de Google Chat a texto plano por mes y space."
    )
    ap.add_argument("--days", type=int, help="ultimos N dias")
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--until", help="YYYY-MM-DD (exclusivo)")
    ap.add_argument("--month", help="YYYY-MM: un mes entero")
    ap.add_argument("--only", action="append", default=[],
                    help="solo spaces cuyo nombre contenga esto (repetible)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="saltear spaces cuyo nombre contenga esto (repetible)")
    ap.add_argument("--force", action="store_true", help="rebajar aunque ya este bajado")
    ap.add_argument("--list-spaces", action="store_true", help="listar spaces y salir")
    ap.add_argument("--reauth", action="store_true", help="borrar token y re-autorizar")
    ap.add_argument("--json", action="store_true",
                    help="resumen final en JSON por stdout (para invocar desde otro programa)")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    args = ap.parse_args()

    # Con --json, stdout queda limpio para el JSON: el resto va a stderr.
    log = (lambda *a: print(*a, file=sys.stderr)) if args.json else print

    if args.month:
        try:
            since = dt.date.fromisoformat(args.month + "-01")
        except ValueError:
            sys.exit(f"[X] --month invalido: {args.month} (formato YYYY-MM)")
        until = month_bounds(since)[1]
    elif args.days:
        since, until = today - dt.timedelta(days=args.days), today + dt.timedelta(days=1)
    else:
        since = dt.date.fromisoformat(args.since) if args.since else today.replace(day=1)
        until = dt.date.fromisoformat(args.until) if args.until else today + dt.timedelta(days=1)

    if until <= since:
        sys.exit(f"[X] Rango vacio: {since} -> {until}")

    creds = get_creds(reauth=args.reauth, quiet=args.json)
    account = whoami(creds)
    log(f"[i] Cuenta: {account}")
    svc = build("chat", "v1", credentials=creds, cache_discovery=False)

    spaces = list_spaces(svc)

    # Miembros de cada space, una sola vez: de aca salen los nombres de las
    # personas y el titulo de los DMs, que no traen displayName.
    log(f"[i] Leyendo miembros de {len(spaces)} spaces...")
    members = {sp["name"]: list_members(svc, sp["name"]) for sp in spaces}
    me_id = None  # se resuelve despues de tener el cache de nombres

    # Los nombres no vienen de Chat: se buscan en el directorio y se cachean.
    todos = {uid for p in members.values() for uid in p}
    cache = resolve_names(creds, todos, load_people_cache(), log)
    nombres = {uid: cache_name(e) for uid, e in cache.items()}
    me_id = my_user_id(account, cache, members, log)
    for p in members.values():
        for uid in p:
            if nombres.get(uid):
                p[uid] = nombres[uid]

    resueltos = sum(1 for uid in todos if nombres.get(uid))
    log(f"[i] {resueltos}/{len(todos)} personas identificadas")
    if todos and not resueltos:
        log("[!] Ningun nombre resuelto: los mensajes van a salir como users/NNN.")

    my_name = nombres.get(me_id) if me_id else None

    def title_of(sp):
        return space_title(sp, members.get(sp["name"]), me_id, my_name)

    def file_of(sp):
        return space_filename(sp, members.get(sp["name"]), me_id, my_name)

    def keep(sp):
        title = title_of(sp).lower()
        if args.only and not any(p.lower() in title for p in args.only):
            return False
        return not any(p.lower() in title for p in args.exclude)

    kept = [sp for sp in spaces if keep(sp)]

    if args.list_spaces:
        if args.json:
            print(json.dumps({
                "account": account,
                "spaces": [
                    {
                        "name": sp["name"],
                        "title": title_of(sp),
                        "type": sp.get("spaceType") or sp.get("type"),
                        "file": file_of(sp),
                    }
                    for sp in kept
                ],
            }, ensure_ascii=False, indent=2))
        else:
            log(f"[i] {len(kept)} spaces accesibles (de {len(spaces)}):\n")
            for sp in sorted(kept, key=lambda s: title_of(s).lower()):
                kind = sp.get("spaceType") or sp.get("type") or "?"
                log(f"    {title_of(sp):<45} {kind:<16} {file_of(sp)}")
        return 0

    log(f"[i] Rango: {since} -> {until}  ({len(kept)} de {len(spaces)} spaces)")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    this_month = today.replace(day=1)

    files, total, skipped_done = [], 0, 0
    no_access = []

    for m in months_between(since, until):
        m_start, m_end = month_bounds(m)
        # El recorte importa: --days 7 no debe declarar el mes entero como cubierto.
        lo, hi = max(m_start, since), min(m_end, until)
        mdir = os.path.join(args.out, m.strftime("%Y-%m"))
        log(f"\n[{m.strftime('%Y-%m')}]  {lo} -> {hi}")

        for sp in kept:
            people = members.get(sp["name"]) or {}
            path = os.path.join(mdir, file_of(sp))
            # El mes en curso siempre se rebaja: le siguen entrando mensajes.
            if not args.force and m < this_month and already_covered(path, lo, hi):
                skipped_done += 1
                continue
            try:
                msgs = list_messages(svc, sp["name"], iso_z(lo), iso_z(hi))
            except HttpError as e:
                no_access.append({"space": title_of(sp), "error": f"HTTP {e.resp.status}"})
                continue
            if not msgs:
                continue
            os.makedirs(mdir, exist_ok=True)
            write_atomic(path, render(sp, msgs, lo, hi, now_iso, people, me_id, my_name))
            drop_renamed(mdir, path)
            files.append({"path": path, "space": title_of(sp),
                          "month": m.strftime("%Y-%m"), "messages": len(msgs)})
            total += len(msgs)
            log(f"    {len(msgs):5d}  {title_of(sp)}")

    code = 2 if no_access else 0

    if args.json:
        print(json.dumps({
            "account": account,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "out": args.out,
            "messages": total,
            "files": files,
            "skipped_already_downloaded": skipped_done,
            "no_access": no_access,
        }, ensure_ascii=False, indent=2))
    else:
        log(f"\n[OK] {total} mensajes en {len(files)} archivos -> {args.out}")
        if skipped_done:
            log(f"     {skipped_done} space/mes ya estaban bajados (--force para rebajar)")
        if no_access:
            log(f"[!] Sin acceso a {len(no_access)} spaces:")
            for e in no_access:
                log(f"      {e['error']}  {e['space']}")

    return code


if __name__ == "__main__":
    sys.exit(main())
