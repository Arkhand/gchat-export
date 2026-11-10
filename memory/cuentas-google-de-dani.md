---
name: cuentas-google-de-dani
description: Dani tiene dos cuentas Google — el laburo es dmusial@blueboot.com (Workspace) y el Chrome por defecto esta logueado con la personal
metadata:
  type: user
---

Dani usa dos cuentas de Google:

- **`dmusial@blueboot.com`** — cuenta de laburo, Google Workspace con organizacion
  `blueboot.com`. Es la que hay que usar para cualquier cosa de trabajo: Google Chat,
  Google Cloud, APIs de Workspace.
- **`musiald@gmail.com`** — personal. Es la que aparece por defecto en su Chrome y en
  la extension de Claude in Chrome.

Tambien tiene `registromusial@gmail.com` y `danielteexplica@gmail.com` logueadas en el
mismo navegador.

Consecuencia practica: en cualquier consola web de Google, la sesion por defecto es la
personal. Hay que verificar la cuenta activa antes de hacer nada — ver
[[verificar-cuenta-antes-de-actuar-en-consolas-web]]. En Google Cloud la cuenta de
blueboot suele resolver como `authuser=3`.

Las APIs de Google Workspace (Chat entre ellas) rechazan cuentas consumer, asi que con
la personal directamente no se puede. Ver [[proyecto-gchat-mcp]].
