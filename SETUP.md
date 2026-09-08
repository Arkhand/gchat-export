# Setup — lector de Google Chat

Todo esto es de una sola vez. Hacelo logueado con **la cuenta de Workspace**, no con
un Gmail personal: la Chat API no funciona con `@gmail.com`.

> Los valores concretos de esta instalacion (cuenta, proyecto) estan al final, en
> [Valores de esta instalacion](#valores-de-esta-instalacion).

## 1. Proyecto en Google Cloud
https://console.cloud.google.com/projectcreate
Nombre: `gchat-export` (o el que quieras). Si el selector de organizacion ofrece el
dominio de tu empresa, elegilo.

> Si el boton de crear proyecto esta bloqueado, tu admin de Workspace restringio la
> creacion de proyectos: pedile que te habilite o que cree el proyecto y te de rol de Editor.

## 2. Habilitar las dos APIs
Con el proyecto nuevo seleccionado, **Enable** en cada una:

- **Chat API** — https://console.cloud.google.com/apis/library/chat.googleapis.com
- **People API** — https://console.cloud.google.com/apis/library/people.googleapis.com

La People API no es opcional: la Chat API no devuelve nombres de personas en ningun
endpoint, solo IDs tipo `users/104692848...`. Sin People API los archivos salen con
esos IDs y los DMs sin identificar.

## 3. Pantalla de consentimiento OAuth
APIs & Services → OAuth consent screen
- User type: **Internal** ← importante. Al ser interna no necesita verificacion de Google
  y el refresh token no se vence a los 7 dias.
- App name: `gchat-export`, tu mail como support y developer contact. Save.

> Si en algun momento te manda a "Configure the Chat API" pidiendo nombre/avatar de app:
> eso es para construir un *bot*. Para solo leer con tu propio usuario no hace falta;
> si igual te obliga, poné cualquier nombre y guardá.

## 4. Credencial OAuth
APIs & Services → Credentials → **Create credentials** → **OAuth client ID**
- Application type: **Desktop app**
- Name: `gchat-export-desktop`
→ Create → **Download JSON**

Guardá ese archivo en esta misma carpeta con el nombre exacto **`credentials.json`**.

## 5. Dependencias
```
pip install --user -r requirements.txt
```

## 6. Correr
```
python -u gchat_export.py --list-spaces
```
La primera vez imprime una URL para autorizar. **Abrila con la cuenta de Workspace**:
al ser app Interna, con una cuenta personal rebota. Despues guarda `token.json` y no
vuelve a preguntar.

`--list-spaces` no baja nada: lista las conversaciones y arranca imprimiendo
`[i] Cuenta: ...`, asi confirmas que autorizaste con la cuenta correcta antes de
bajar nada.

Ver el [README](README.md) para el uso diario y todas las opciones.

## Permisos que pide
Todos de solo lectura, y solo de spaces donde ya sos miembro:

| Scope | Para que |
|---|---|
| `chat.spaces.readonly` | listar las conversaciones |
| `chat.messages.readonly` | leer los mensajes |
| `chat.memberships.readonly` | saber quien esta en cada conversacion |
| `directory.readonly` | resolver `users/NNN` a nombres reales |
| `userinfo.email` + `openid` | saber con que cuenta esta corriendo |

## Si falla

| Error | Causa |
|---|---|
| `Google Chat API is only available to Google Workspace users` | estas logueado con una cuenta personal, no la de Workspace |
| `403 PERMISSION_DENIED` al listar | el admin tiene control de acceso a apps activado (Admin console → Security → API controls). Hay que confiar la app |
| `access_denied` en el navegador | la consent screen quedo en Testing y tu usuario no esta como tester. Pasala a Internal (paso 3) |
| `People API has not been used in project` | falta el paso 2: habilitar People API |
| Los mensajes salen como `users/NNN` | falta People API, o el token es viejo: correr con `--reauth` |
| Todos los DMs se llaman como vos | no se pudo identificar tu propio ID; borrar `personas.json` y volver a correr |

---

## Valores de esta instalacion

| Cosa | Valor |
|---|---|
| Cuenta | `dmusial@blueboot.com` (Workspace) — NO el Gmail personal |
| Proyecto GCP | `gchat-export-508011`, dentro de la org `blueboot.com` |
| Cliente OAuth | `gchat-export-desktop`, tipo Desktop app |
| Consent screen | Interno |

Ojo: el Chrome por defecto abre con la cuenta personal. Al autorizar o entrar a la
consola de GCP, verificar arriba a la derecha que diga la cuenta de Workspace.
