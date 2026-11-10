---
name: login-google-no-se-automatiza
description: El flujo de login/OAuth de Google no se puede completar con Playwright; ese paso lo hace Dani
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3edbbdc5-75d2-4c3b-93ef-b2022903a3dd
  modified: 2026-09-08T12:40:23.751Z
---

El selector de cuentas de Google (`accounts.google.com`) no se puede operar con
Playwright: la página deja un spinner "Cargando" permanente y el click sobre la
cuenta nunca se estabiliza (timeout esperando "visible, enabled and stable").
Navegar a consolas ya logueadas sí funciona — lo que falla es el flujo de
login/consent.

**Why:** es una defensa de Google contra automatización. Insistir además puede
marcar la cuenta como actividad sospechosa.

**How to apply:** para OAuth, imprimir la URL y pedirle a Dani que la abra en su
Chrome. Antes de tocar cualquier consola web con Playwright, verificar la cuenta
activa (ver [[verificar-cuenta-antes-de-actuar-en-consolas-web]]) — el perfil por
defecto es la personal, no blueboot (ver [[cuentas-google-de-dani]]).
Para habilitar APIs o clicks en consolas ya autenticadas, Playwright sí sirve.
