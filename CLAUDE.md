# GoogleChatCloude — descargador de Google Chat via API

Baja los mensajes de Google Chat a una base SQLite local, mas un `.md` legible por
space y mes. Auth de usuario (no bot): solo ve los spaces donde Dani ya es miembro.

**El script solo baja.** No resume, no extrae tareas, no interpreta. Un segundo
proyecto (la IA que organiza tareas y pendientes) lo invoca y lee la base. Todo el
diseño apunta a que ese consumidor trabaje facil: IDs estables, cursor de cambios,
modo no interactivo, corridas de segundos.

## Estado actual (2026-10-01)

**Funcionando sobre SQLite.** Agosto, septiembre y octubre migrados a
`~/gchat-export/gchat.db` y verificados contra los `.md` viejos de `out/`.

| Cosa | Valor |
|---|---|
| Cuenta / proyecto GCP | ver [SETUP.md](SETUP.md#this-installation) — es la de Workspace, NO el Gmail personal |
| Consent screen | tipo **Interno** (refresh token no vence a los 7 dias) |
| APIs habilitadas | Chat API + **People API** (esta ultima para los nombres) |
| `token.json` | 6 scopes de lectura; esta junto al script |
| Datos | `~/gchat-export/` (`gchat.db` + `md/`), fuera del repo |
| Datos viejos | `out/` y `personas.json` en el repo: formato anterior, ya migrado; se pueden borrar |

### Uso
```
python -u gchat_export.py                     # diario: incremental, 3-5 s
python -u gchat_export.py --month 2026-07     # backfill de un mes
python -u gchat_export.py --non-interactive --json   # desde el consumidor
python -m unittest discover -s tests -v       # tests, sin red
```

Si hay que re-autorizar (`--reauth`), la URL se abre **con la cuenta de Workspace**.
Con `--non-interactive` nunca abre navegador: sale con exit 3.

El contrato completo para el consumidor (exit codes, JSON, esquema, cursor) esta en
el README. Esta en ingles, igual que el codigo, por las instrucciones globales.

## Decisiones y por que

- **API en vez de scrapear la UI.** La Chat API filtra por `createTime` y pagina
  de a 1000. Scrollear la web era lento y fragil.
- **Consent screen Interno, no Externo.** Externo deja la app en Testing y el
  refresh token vence cada 7 dias.
- **SQLite como fuente de verdad, `.md` como vista.** El `.md` solo no le servia al
  consumidor: sin IDs de mensaje no puede linkear una tarea a su origen ni saber
  que ya proceso, y tenia que releer todo el mes. SQLite viene con Python (sin
  dependencias), es un solo archivo y se consulta.
- **Cursor `seq`, el estado del consumidor es del consumidor.** Cada insert,
  edicion o borrado sube `seq`; un mensaje que vuelve identico conserva el suyo.
  El consumidor guarda "procese hasta N" y pide `seq > N`. El descargador no sabe
  nada de el: por eso sigue siendo standalone.
- **Borrados se marcan, no se borran** (`deleted = 1` + `seq` nuevo), para que el
  consumidor pueda retirar lo que derivo de ese mensaje.
- **Se sacan `downloadUri`/`thumbnailUri` en cualquier nivel antes de guardar.**
  Traen un token nuevo en cada pedido: sin esto, cada corrida marcaba como editados
  todos los mensajes con adjuntos (55 de 868 en "La interna"). Tambien aparecen
  dentro de la cita de un mensaje reenviado (`quotedMessageSnapshot.attachments`):
  el primer arreglo solo miraba el nivel de arriba y se escaparon 2. Ademas vencen.
- **Citas y reenvios en columnas aparte** (`quote_type`, `quoted_sender`,
  `quoted_text`). Un reenvio no tiene texto propio: su contenido (ej. "nos pasaron
  la nota 3798315, vulnerabilidad en @sap/cds-mtxs") solo esta en el snapshot. Antes
  salia como "(no text)". `text` queda como lo que escribio esa persona.
- **Cobertura por space en dias UTC (`coverage`), y hoy nunca cuenta como cubierto.**
  Asi la proxima corrida rebaja hoy entero: correrlo dos veces el mismo dia agarra
  lo de la tarde, y la corrida del 1ro agarra lo tardio del 30. Es lo que Dani
  pidio ("rebajar el ultimo dia ejecutado y reemplazarlo") sin logica especial.
- **Fechas por prefijo `YYYY-MM-DD`, nunca timestamps completos como texto.** Como
  string, `"00:00:00.123Z"` ordena antes que `"00:00:00Z"` (`.` < `Z`).
- **Salteo por `lastActiveTime`.** Si un space no tuvo actividad desde lo que falta
  bajar, no se lo llama. Es lo que baja la corrida diaria de ~2 min a 3-5 s (107 de
  111 spaces salteados). Verificado: en los 111 spaces ningun mensaje es mas nuevo
  que su `lastActiveTime`.
- **Miembros cacheados**, se refrescan solo si cambia `membershipCount`. Antes eran
  ~110 llamadas por corrida. `--force` NO los refresca (hacerlo costaba 76 s).
- **Datos fuera del repo** (`--data-dir` > `$GCHAT_DATA_DIR` > `~/gchat-export`).
  El proyecto ya se mudo una vez de carpeta; el consumidor no tiene que depender de
  donde vive el codigo. Igual el `--json` le dice la ruta de la base.
- **`--non-interactive` sale con exit 3** en vez de abrir el flujo OAuth y esperar
  un navegador para siempre: un proceso desatendido no puede quedar colgado.
- **WAL + `BEGIN IMMEDIATE`.** El consumidor lee mientras se escribe, y dos
  corridas simultaneas no pueden repetir un `seq`.
- **Reintentos** (`num_retries=3`) para 429/5xx.
- **Nombre de archivo = slug + sufijo del ID del space**; al renombrarse se borra el
  `.md` viejo. Sin el sufijo, dos spaces con el mismo nombre se pisaban.
- **Escritura atomica** de los `.md` (tmp + rename).
- **`python -u` siempre.** Sin eso la URL de autorizacion queda en el buffer.

## Gotchas conocidos

- **La Chat API no devuelve nombres de personas. Nunca.** Ni `sender.displayName`
  ni las membresias: solo `users/NNN`. Hace falta People API + `directory.readonly`.
- **Los DMs no tienen `displayName`**: se nombran por la otra persona.
- **`people/me` no sirve** para el propio user id (pide scope `profile`). Se busca
  por email en la tabla `people`; sin eso todos los DMs se llaman como Dani.
- **People API se habilita aparte** en la consola GCP.
- Edicion o borrado de un mensaje viejo en un space **sin actividad nueva** no se
  detecta (el space se saltea). Para eso, `--force` sobre ese mes.
- `spaces.list` no devuelve DMs/grupos hasta que tengan un mensaje; los mensajes de
  sistema no vienen; un 403 suele ser control de acceso a apps del admin.
- Un space puede aparecer tarde en la lista con historial viejo: "BMS - ASCM" no
  estaba el 8/9 y trajo 100 mensajes de agosto al migrar.
- El login de Google **no se puede automatizar** con Playwright (spinner eterno en
  el selector de cuentas). Ese paso lo hace Dani.
- Los tests verifican algo de verdad solo si fallan con el bug: se valido metiendo
  bugs a mano. Asi aparecio un test que pasaba por la razon equivocada.

## Archivos

- `gchat_export.py` — el script
- `tests/test_gchat_export.py` — tests con una Chat API falsa
- `README.md` — uso y contrato para el consumidor (ingles)
- `SETUP.md` — pasos de Google Cloud + valores de esta instalacion (ingles)

## Seguridad

`credentials.json` y `token.json` estan en `.gitignore`, igual que `*.db`, `md/`,
`out/` y `personas.json`. La carpeta de datos tiene conversaciones de trabajo y
mails de la empresa sin encriptar.
