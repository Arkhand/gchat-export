# GoogleChatCloude — lector de Google Chat via API

Baja los mensajes de Google Chat de un rango de fechas a texto plano, para poder
resumir despues "que se laburo" en un periodo. Auth de usuario (no bot): solo ve
los spaces donde Dani ya es miembro.

**El script solo baja.** No resume ni interpreta: escribe archivos y sale. La idea
es que despues un MCP/Skill lo invoque y lea la carpeta `out/`, asi que corre
standalone y con `--json` devuelve un resumen parseable de lo que escribio.

## Estado actual (2026-09-08)

**Funcionando.** Autorizado y probado: septiembre 2026 bajado, 1202 mensajes en
15 archivos, con nombres de personas resueltos.

| Cosa | Valor |
|---|---|
| Cuenta / proyecto GCP | ver [SETUP.md](SETUP.md#valores-de-esta-instalacion) — es la de Workspace, NO el Gmail personal |
| Consent screen | tipo **Interno** (sin verificacion de Google, refresh token no vence a los 7 dias) |
| Cliente OAuth | tipo Desktop app |
| APIs habilitadas | Chat API + **People API** (esta ultima para los nombres) |
| `credentials.json` | descargado y en su lugar (gitignoreado) |
| `token.json` | generado, con 6 scopes de lectura |
| `personas.json` | cache de `users/NNN` -> nombre+email (gitignoreado) |

### Uso
```
python -u gchat_export.py --month 2026-08
```

Si hay que re-autorizar (`--reauth`), la URL se abre **con la cuenta de Workspace**.
Al ser app Interna, con el Gmail personal rebota. `--list-spaces` es el chequeo
barato: no baja nada y arranca imprimiendo `[i] Cuenta: ...`.

## Interfaz

La **cuenta no es un parametro**: sale del `token.json` generado al autorizar.
El script imprime con cual esta corriendo apenas arranca.

| Flag | Que hace |
|---|---|
| `--days N` | ultimos N dias |
| `--since` / `--until` | `YYYY-MM-DD`, until exclusivo |
| `--month YYYY-MM` | un mes entero |
| *(nada)* | mes actual |
| `--only` / `--exclude` | filtrar spaces por nombre (repetibles) |
| `--force` | rebajar aunque ya este bajado |
| `--list-spaces` | listar y salir, sin bajar |
| `--reauth` | borrar token y re-autorizar |
| `--json` | resumen final parseable por stdout |
| `--out RUTA` | destino (default `out/`) |

Exit codes: `0` ok · `1` error duro · `2` parcial (algun space sin acceso).
Con `--json` el JSON va a stdout limpio y los logs a stderr.

## Salida

```
out/2026-08/Proyecto-X-a1b2c3.md
out/2026-09/Marce-Gomez-d4e5f6.md
```

Mes afuera, space adentro. Cada `.md` agrupa por dia y por hilo; los mensajes van
al archivo del mes en que se enviaron, y si un hilo se retoma se marca
"(sigue un hilo anterior)".

## Decisiones y por que

- **API en vez de scrapear la UI.** La Chat API filtra por `createTime` y pagina
  de a 1000, asi que un mes entero baja en segundos. Scrollear la web era lento y fragil.
- **Consent screen Interno, no Externo.** Externo deja la app en modo Testing y ahi
  el refresh token se vence cada 7 dias — habria que re-loguear todo el tiempo.
- **La Chat API exige cuenta Workspace.** Con una cuenta personal tira
  "Google Chat API is only available to Google Workspace users".
- **Solo baja, no resume.** El `digest.md` viejo mezclaba bajada con presentacion.
  Sacarlo deja el script como una pieza sola, invocable desde un MCP/Skill que
  despues lee `out/`.
- **Texto plano, no JSON.** El markdown por mes/space se lee directo: "que hice en
  agosto con Marce" es abrir un archivo, no filtrar un JSON grande. No se guarda
  crudo aparte porque lo unico que valia la pena (hilos, nombre de adjuntos) ya
  quedo dentro del `.md`.
- **Nombre + sufijo de ID en el archivo.** Dos spaces con el mismo displayName
  existen; sin el sufijo se pisarian entre si.
- **Cabecera con el rango cubierto.** Cada `.md` declara `cubre=desde/hasta`, asi
  la segunda corrida sabe que falta en vez de asumir que "existe = esta completo".
  Un `--days 7` no marca el mes entero como bajado.
- **El mes en curso siempre se rebaja.** Le siguen entrando mensajes; los meses
  cerrados se saltean salvo `--force`.
- **Escritura atomica** (tmp + rename). Un Ctrl+C a la mitad no deja un `.md`
  trunco haciendose pasar por completo.
- **Cache de nombres en `personas.json`.** Resolver 70 personas son 2 llamadas
  batch; sin cache se repetirian en cada corrida. Guarda nombre + email porque
  el email es lo que permite saber cual de los IDs es uno mismo.
- **Al renombrarse un space se borra el `.md` viejo.** El sufijo del archivo sale
  del ID, asi que mismo id + otro nombre = renombrado. Sin esto quedaban
  duplicados (paso al resolver los nombres de los DMs).
- **`python -u` siempre.** Sin eso Python bufferea stdout, la URL de autorizacion
  queda invisible y el script parece colgado cuando en realidad esta esperando.

## Gotchas conocidos

- `spaces.list` no devuelve DMs ni grupos hasta que tengan al menos un mensaje.
- Los mensajes de sistema ("fulano se unio al space") no vienen en la lista.
- Si algun space tira 403, el script lo saltea y lo reporta al final: suele ser
  control de acceso a apps del admin de Workspace (Admin console -> Security -> API controls).
- **La Chat API no devuelve nombres de personas. Nunca.** Ni `sender.displayName`
  ni las membresias: solo `users/NNN`. Por eso hace falta People API + el scope
  `directory.readonly`. Agregar `chat.memberships.readonly` solo no alcanza:
  devuelve los IDs de los miembros, pero tampoco sus nombres.
- **Los DMs no tienen `displayName`**: se nombran por la otra persona, que sale
  de cruzar los miembros con el directorio.
- **`people/me` no sirve** para saber el propio user id: pide el scope `profile`.
  Se identifica por email, comparando contra `personas.json`. Importa: sin eso
  todos los DMs se nombran con el nombre de uno mismo.
- **People API hay que habilitarla aparte** en la consola GCP; no viene con Chat.
- El login de Google **no se puede automatizar** con Playwright: la pagina deja
  un spinner permanente y el click nunca se estabiliza. Esa parte la hace Dani.

## Archivos

- `gchat_export.py` — el script
- `personas.json` — cache `users/NNN` -> nombre+email (gitignoreado: tiene mails)
- `SETUP.md` — pasos de Google Cloud (ya ejecutados, sirven para rehacerlo)
- `out/YYYY-MM/<Space>-<id>.md` — salida, texto plano por mes y space

## Seguridad

`credentials.json`, `token.json` y `personas.json` estan en `.gitignore`.
No commitearlos: los dos primeros son credenciales y el tercero tiene nombres y
mails de gente de la empresa. `out/` tambien esta ignorado — son conversaciones
de trabajo en texto plano, sin encriptar.
