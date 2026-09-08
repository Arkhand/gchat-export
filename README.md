# gchat-export

Baja tus mensajes de Google Chat a texto plano, organizados por mes y conversacion,
para poder responder despues "que se laburo en agosto" sin scrollear la web.

Usa **auth de usuario**, no un bot: solo ve las conversaciones donde vos ya sos miembro.

**Solo baja. No resume.** Escribe archivos y sale — la idea es que un MCP, un skill o
vos mismo lean despues la carpeta `out/`.

## Instalacion

Ver [SETUP.md](SETUP.md): hay que crear un proyecto en Google Cloud, habilitar dos APIs
y bajar un `credentials.json`. Es de una sola vez.

```
pip install -r requirements.txt
python -u gchat_export.py --list-spaces    # primera vez: autoriza en el navegador
```

## Uso

```bash
python -u gchat_export.py                       # mes actual
python -u gchat_export.py --month 2026-08       # un mes entero
python -u gchat_export.py --days 30             # ultimos 30 dias
python -u gchat_export.py --since 2026-07-15 --until 2026-08-02
```

Correlo siempre con `python -u`: sin eso Python bufferea stdout, la URL de autorizacion
queda invisible y el script parece colgado cuando en realidad esta esperando.

### Opciones

| Flag | Que hace |
|---|---|
| `--days N` | ultimos N dias |
| `--since` / `--until` | `YYYY-MM-DD`, until exclusivo |
| `--month YYYY-MM` | un mes entero |
| *(nada)* | mes actual |
| `--only` / `--exclude` | filtrar conversaciones por nombre (repetibles) |
| `--force` | rebajar aunque ya este bajado |
| `--list-spaces` | listar y salir, sin bajar |
| `--reauth` | borrar el token y volver a autorizar |
| `--json` | resumen final parseable por stdout |
| `--out RUTA` | destino (default `out/`) |

La **cuenta no es un parametro**: sale del `token.json` que se genera al autorizar. El
script imprime con cual esta corriendo apenas arranca. Para cambiarla, `--reauth`.

## Salida

```
out/
  2026-08/
    Joaquin-Mansilla-f2b935.md
    La-interna-b451c5.md
  2026-09/
    Francisco-Cosco-8b4eb3.md
```

Mes afuera, conversacion adentro. El sufijo del nombre sale del ID de la conversacion,
asi que dos personas con el mismo nombre nunca se pisan y el archivo es estable aunque
alguien se renombre.

Cada archivo agrupa por dia y por hilo:

```markdown
## 2026-09-03

**14:22 Dani Musial**: che, quedo roto staging
  - **14:25 Marce Gomez**: lo miro
  - **15:40 Marce Gomez**: era la env var, ya esta

**16:01 Dani Musial**: te paso el informe [adjunto: informe-agosto.pdf]
```

Cada mensaje va al archivo del mes en que se envio. Si un hilo se retoma mas tarde, se
marca `(sigue un hilo anterior)` en vez de mover el mensaje de mes.

## Bajadas incrementales

Cada `.md` declara en su cabecera que rango cubre:

```
<!-- gchat-export space=spaces/AAA cubre=2026-09-01/2026-09-08 bajado=2026-09-08T10:22Z -->
```

El script no pregunta "existe el archivo?" sino "ya cubre lo que me pediste?". Si bajaste
con `--days 7` y despues pedis el mes entero, ve que solo cubre hasta el 8 y rebaja lo
que falta. Los meses ya completos se saltean; **el mes en curso siempre se rebaja**
porque le siguen entrando mensajes.

La escritura es atomica (temporal + rename): un Ctrl+C a la mitad no deja un archivo
trunco haciendose pasar por completo.

## Llamarlo desde otro programa

Corre standalone y no pide nada por consola salvo la primera autorizacion.

```bash
python -u gchat_export.py --month 2026-08 --json
```

Con `--json` el resumen sale por stdout y los logs por stderr:

```json
{
  "account": "vos@tuempresa.com",
  "since": "2026-08-01",
  "until": "2026-09-01",
  "messages": 6138,
  "files": [
    {"path": "out/2026-08/La-interna-b451c5.md", "space": "La interna",
     "month": "2026-08", "messages": 530}
  ],
  "no_access": []
}
```

Exit codes: `0` ok · `1` error duro · `2` parcial (alguna conversacion sin acceso).

## Rendimiento

Referencia real, 105 conversaciones:

| | |
|---|---|
| Septiembre (1202 mensajes, 8 dias) | ~40 s |
| Agosto (6138 mensajes, mes entero) | ~2 min |

La primera corrida ademas resuelve los nombres de las personas y los cachea en
`personas.json`; las siguientes ya no pagan ese costo. Un mes ya bajado se saltea entero.

## Limitaciones

- `spaces.list` no devuelve DMs ni grupos hasta que tengan al menos un mensaje.
- Los mensajes de sistema ("fulano se unio al space") no aparecen.
- Si una conversacion tira 403 se saltea y se reporta al final; suele ser control de
  acceso a apps del admin de Workspace.
- Un mes ya bajado no detecta mensajes editados o borrados despues. Usar `--force`.

## Seguridad

Estos archivos **no se commitean** (estan en `.gitignore`):

| Archivo | Que tiene |
|---|---|
| `credentials.json` | credencial OAuth de la app |
| `token.json` | tu token de acceso |
| `personas.json` | nombres y mails del directorio de la empresa |
| `out/` | las conversaciones, en texto plano sin encriptar |

`out/` son mensajes de trabajo sin encriptar en tu disco. Si vas a guardar meses de
DMs, considera apuntar `--out` a una carpeta respaldada o cifrada.
