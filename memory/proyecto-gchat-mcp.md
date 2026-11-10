---
name: proyecto-gchat-mcp
description: "MCP de Google Chat sobre una copia SQLite local, mas el descargador CLI que la llena — antes gchat-export, convertido el 2026-11-10"
metadata:
  type: project
---

Dani quiere que Claude lea sus chats de Google para dos cosas: revisar a fin de mes
"qué hice" y que la tarea programada `revisar-mails` (lunes a viernes 9/12/15/18) detecte
pendientes en los chats además de en los mails y proponga tarjetas de Trello.

Historia: empezó el 2026-09-08 como `gchat-export` en
`C:\Users\musia\Desktop\Proyectos\_SinCliente\GoogleChatCloude`, un descargador a SQLite +
`.md` pensado para que "un segundo proyecto" leyera la base. El 2026-11-10 se convirtió en
este MCP (`C:\Users\musia\Desktop\Proyectos\_MCP\gchat-mcp`), conservando la historia de git y
el remoto privado `Arkhand/gchat-export`.

Decisiones de Dani en la conversión:
- El descargador queda como programa de línea de comandos; la herramienta `sync` del MCP lo
  ejecuta como subproceso.
- Se sacan los `.md`: el MCP lee la base directo.
- No hay tarea de Windows: `revisar-mails` llama a `sync` antes de leer.

Datos en `~/gchat-export/` (`gchat.db`), credenciales en `~/gchat-export/auth/`. La base
había quedado parada el 2026-10-30 porque la tarea de Windows nunca se registró.

**Why:** el consumidor real resultó ser Claude, no un programa aparte; con el MCP no hace
falta que cada consumidor abra SQLite a mano.

**How to apply:** antes de tocarlo, leer `CLAUDE.md`, `docs/FEATURE_MAP.md` y
`docs/DECISIONS.md`. Si `sync` dice que la autorización venció, el login lo hace Dani en una
terminal con la cuenta de Workspace (ver [[login-google-no-se-automatiza]],
[[cuentas-google-de-dani]]).
