---
name: verificar-cuenta-antes-de-actuar-en-consolas-web
description: Dani corrigio dos veces por avanzar en Google Cloud sin chequear que cuenta estaba activa — verificar identidad antes de crear nada
metadata:
  type: feedback
---

Antes de crear o modificar algo en una consola web de Google (o cualquier SaaS con
multi-cuenta), verificar explicitamente que cuenta esta activa y decirselo a Dani.

**Why:** El 2026-09-07/08, armando el lector de Google Chat, avance dos veces en
Google Cloud con `musiald@gmail.com` en vez de la cuenta de laburo. Dani me freno las
dos ("Pera, pero esa no es la cuenta correcta"). Habia una senal clara que ignore: el
campo Organizacion decia "Sin organizacion", que es justo lo que pasa con una cuenta
consumer. Si hubiera creado el proyecto ahi, quedaba inservible — la Chat API no
funciona fuera de Workspace, y la opcion "Interno" del consent screen no existe sin
organizacion.

**How to apply:** Leer el boton de cuenta / avatar con `find` antes del primer click que
cree algo, y reportar el mail encontrado. Tratar cualquier campo de organizacion o tenant
vacio como sospecha de cuenta equivocada, no como un default aceptable. Cuando la cuenta
esta mal, no intentar resolverlo solo: el login lo hace Dani.

Ver [[cuentas-google-de-dani]].
