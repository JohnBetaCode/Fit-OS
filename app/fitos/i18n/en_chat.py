"""English for the WhatsApp chat, training notes and the coach. Keys are the Spanish source text.

Areas: `wa/` (cards, session flow, help, parser descriptions), `training/` (progression notes, planner
rationale, job details), `coach/` (context lines, fallbacks, tool errors), `jobs.py`, plus the PR kinds
(`cards.PR_ES`) and plan statuses (`session_flow.PLAN_STATUS_ES`). Muscle/equipment labels are in en_data."""

EN: dict[str, str] = {
    # ── wa/parser.py: describe() ─────────────────────────────────────────────
    "+ {w} kg de lastre": "+ {w} kg added",
    " c/u": " each",
    " de asistencia": " assist",
    "· peso corporal": "· bodyweight",
    # ── wa/cards.py ──────────────────────────────────────────────────────────
    "peso corporal": "bodyweight",
    "calibrar peso": "weight TBD",
    "{w} kg por mancuerna": "{w} kg per dumbbell",
    "*{header} {focus}* en {place} · {date}": "*{header} {focus}* at {place} · {date}",
    "Hoy toca": "Today:",
    "{n} ejercicios · ~{minutes} min": "{n} exercises · ~{minutes} min",
    "*1* Empezar · *2* Cambiar a {other} · *3* Otro enfoque · *4* Hoy descanso":
        "*1* Start · *2* Switch to {other} · *3* Other focus · *4* Rest today",
    "Objetivo: {sets} × {reps}": "Target: {sets} × {reps}",
    " con {w}": " at {w}",
    " · RIR {rir} · descanso {rest}": " · RIR {rir} · rest {rest}",
    "Última vez: {last}": "Last time: {last}",
    "Llevas {done} de {sets} sets": "{done} of {sets} sets done",
    "Responde: *reps x kg* (10x22) · ocupada · otra · salta · video · fin":
        "Reply: *reps x kg* (10x22) · busy · other · skip · video · finish",
    "🏆 *Récord*: {flags}": "🏆 *PR*: {flags}",
    # PR kinds (cards.PR_ES)
    "1RM estimado": "estimated 1RM",
    "peso máximo": "heaviest weight",
    "reps a ese peso": "reps at that weight",
    # Plan statuses (session_flow.PLAN_STATUS_ES)
    "propuesto": "proposed",
    "aceptado": "accepted",
    "en curso": "in progress",
    "completado": "completed",
    "saltado": "skipped",
    "reemplazado": "replaced",
    # ── wa/session_flow.py ───────────────────────────────────────────────────
    "{reps} con {w} kg ({when})": "{reps} at {w} kg ({when})",
    "otro lugar": "another place",
    "✅ *Sesión cerrada*: {mins} min · {sets} sets · {kg} kg movidos":
        "✅ *Session done*: {mins} min · {sets} sets · {kg} kg lifted",
    "🏆 {n} récord: {names}": "🏆 {n} PR: {names}",
    "🏆 {n} récordes: {names}": "🏆 {n} PRs: {names}",
    "Próxima: *{name}*": "Next up: *{name}*",
    "RPE de la sesión: {rpe}/10": "Session RPE: {rpe}/10",
    "No entendí 🤔. Escribe *ayuda* para ver los comandos.": "Didn't get that 🤔. Send *help* to see the commands.",
    "Ese peso ({w} kg) no parece real 🤨": "That weight ({w} kg) doesn't look right 🤨",
    "Anotado: {w} kg hoy ⚖️": "Logged: {w} kg today ⚖️",
    "No hay sesión activa.": "No active session.",
    " Plan de hoy: {focus} ({status}).": " Today's plan: {focus} ({status}).",
    " Escribe *hoy* para ver el plan.": " Send *today* to see the plan.",
    "No pude armar el plan: {error}": "Couldn't build the plan: {error}",
    "Hoy ya entrenaste ✅. Mañana seguimos.": "You already trained today ✅. See you tomorrow.",
    "El plan está vacío 🤷. Escribe *hoy* para regenerarlo.": "The plan is empty 🤷. Send *today* to rebuild it.",
    "💪 ¡Vamos!": "💪 Let's go!",
    "Cambio a {place}:": "Switching to {place}:",
    "Otro enfoque:": "Other focus:",
    "Ok, hoy descanso 😴. La rotación no se pierde: mañana sigue donde iba.":
        "OK, rest day 😴. The rotation stays put: tomorrow picks up where you left off.",
    "Plan en {place}:": "Plan at {place}:",
    "¿Qué tan dura estuvo la sesión, de *1* a *10*?": "How hard was the session, from *1* to *10*?",
    "Sesión {focus}: {done}/{total} ejercicios hechos · ahora: {cur} · {sets} sets en total":
        "{focus} session: {done}/{total} exercises done · now: {cur} · {sets} sets total",
    "¿Qué tan dura estuvo la sesión, de *1* a *10*? (o *no*)": "How hard was the session, from *1* to *10*? (or *no*)",
    "Borrado el último set ({reps}×{w})": "Deleted the last set ({reps}×{w})",
    "No hay sets que borrar.": "No sets to delete.",
    "Corregido: {set}": "Fixed: {set}",
    "No hay sets que corregir.": "No sets to fix.",
    "No entendí la corrección. Ejemplo: *corrige 8x40*": "Didn't get the fix. Example: *fix 8x40*",
    "Va un set más: {sets} en total para {name}.": "One more set: {sets} total for {name}.",
    "No tengo el clip a mano. Mira la técnica aquí: {url}": "No clip at hand. Check the form here: {url}",
    "No tengo los pasos de este ejercicio.": "I don't have the steps for this exercise.",
    "Saltado {name}.": "Skipped {name}.",
    "{name}: {n} sets ✔️": "{name}: {n} sets ✔️",
    "No me quedan alternativas para este. Escribe *salta* para seguir.":
        "No more alternatives for this one. Send *skip* to move on.",
    "estación ocupada": "station busy",
    "sin {equipment}": "no {equipment}",
    "otra opción": "another option",
    "🔄 Anotado: {why}. Cambio a *{name}*.": "🔄 Got it: {why}. Switching to *{name}*.",
    "¿Con cuánto peso fueron esas {reps} reps? Escríbelo como *{example}*.":
        "What weight were those {reps} reps? Send it as *{example}*.",
    "¿Fueron {reps} reps con {w} kg? Escríbelo como *reps x kg*.": "Was that {reps} reps at {w} kg? Send it as *reps x kg*.",
    "No entendí el set. Ejemplos: *10x40*, *8x22,5 rir2*, *12 bw*, *45s*.":
        "Didn't get the set. Examples: *10x40*, *8x22.5 rir2*, *12 bw*, *45s*.",
    "Set {n}: {set}": "Set {n}: {set}",
    "*{name}* listo: {reps}": "*{name}* done: {reps}",
    " Quedó fácil: sube un paso en el siguiente.": " That was easy: go up a step on the next one.",
    "Siguiente: set {n}/{sets}, {reps}": "Next: set {n}/{sets}, {reps}",
    ". Descanso {rest}.": ". Rest {rest}.",
    "¡Terminaste el plan! 🔥 ¿Qué tan dura estuvo la sesión, de *1* a *10*?":
        "Plan complete! 🔥 How hard was the session, from *1* to *10*?",
    "Cerré la sesión por inactividad.": "Closed the session after inactivity.",
    "¿Seguimos o cierro la sesión? (escribe *fin* para cerrar)": "Still going or should I close it? (send *finish* to close)",
    # Job details (scheduler / integrations page)
    "sin grupo configurado": "no group configured",
    "fuera de ventana (el proceso arrancó tarde)": "outside the window (the process started late)",
    "día de descanso": "rest day",
    "ya hay sesión activa": "a session is already active",
    "plan {status}": "plan {status}",
    "enviado": "sent",
    # ── wa/router.py: help ───────────────────────────────────────────────────
    "• *hoy* — el plan de hoy · *2* otro lugar · *3* otro enfoque · *4* descanso":
        "• *today* — today's plan · *2* other place · *3* other focus · *4* rest",
    "• *1* — empezar · durante la sesión: *10x40* (reps x kg), *ocupada*, *otra*, *salta*, *video*, *fin*":
        "• *1* — start · during the session: *10x40* (reps x kg), *busy*, *other*, *skip*, *video*, *finish*",
    "• en la sesión también: *listo* (siguiente ejercicio), *como* (técnica), *+1* (un set más)":
        "• also in the session: *done* (next exercise), *how* (form), *+1* (one more set)",
    "• *borra* (último set), *corrige 8x40*, *no tengo barra* (cambio por equipo)":
        "• *undo* (last set), *fix 8x40*, *no barbell* (swap for equipment)",
    "• *estado* — dónde va la sesión": "• *status* — where the session is at",
    "• *peso 80.5* — registrar peso corporal": "• *weight 80.5* — log body weight",
    "• *peso 80.5* — registrar peso corporal · *cintura 84, pecho 102* — medidas":
        "• *weight 80.5* — log body weight · *waist 84, chest 102* — measurements",
    "• una foto sin texto (o con *progreso*) — foto de progreso":
        "• a photo with no caption (or *progress*) — progress photo",
    "📏 Anotado: {items}": "📏 Logged: {items}",
    "Esto no parece real: {items}": "This doesn't look real: {items}",
    "Por ahora guardo fotos de progreso: mándala sin texto o con *progreso*.":
        "For now I keep progress photos: send it with no caption or with *progress*.",
    "No pude bajar la foto: WhatsApp/WAHA no la descargó. Súbela en Progreso.":
        "Couldn't get the photo: WhatsApp/WAHA didn't download it. Upload it in Progress.",
    "📸 Foto de progreso guardada ({n} este mes).": "📸 Progress photo saved ({n} this month).",
    "📏 Cintura: {cm} cm{delta}": "📏 Waist: {cm} cm{delta}",
    "• cualquier otra cosa — hablas con el coach": "• anything else — you talk to the coach",
    # ── training/ ────────────────────────────────────────────────────────────
    "No encuentro catalog/program_templates.yaml": "Can't find catalog/program_templates.yaml",
    "No hay un programa activo: créalo desde una plantilla.": "No active program: create one from a template.",
    "Variante más exigente por techo de carga. {note}": "Harder variation because of the load ceiling. {note}",
    "Sin ejercicio elegible para: {slots}": "No eligible exercise for: {slots}",
    "{n} planes sin iniciar → skipped": "{n} plans not started → skipped",
    "Calibración: elige un peso con el que hagas ~10 dejando 3 en reserva":
        "Calibration: pick a weight you can do ~10 reps with, leaving 3 in the tank",
    "Última vez: {s} s; intenta sumar 5–10 s": "Last time: {s} s; try adding 5–10 s",
    "Series por tiempo: aguanta con buena técnica dejando 2 en reserva":
        "Timed sets: hold with good form, leaving 2 in the tank",
    "Peso corporal: {lo}–{hi} reps dejando {rir} en reserva": "Bodyweight: {lo}–{hi} reps, leaving {rir} in the tank",
    "Dos sesiones bajo {n} reps: usa una variante más fácil": "Two sessions under {n} reps: use an easier variation",
    "Retroceso: {w} kg tras dos sesiones bajo {n} reps": "Deload: {w} kg after two sessions under {n} reps",
    "Techo de carga: ya haces {n} reps; conviene una variante más exigente (unilateral o pausa)":
        "Load ceiling: you already do {n} reps; time for a harder variation (unilateral or paused)",
    "Tope de carga disponible: extendemos el rango a {lo}–{hi} reps":
        "Max available load: extending the range to {lo}–{hi} reps",
    "llegaste a {n} en todos los sets": "you hit {n} on every set",
    "te sobró reserva (RIR ≥ {rir})": "you had reps to spare (RIR ≥ {rir})",
    "Agrega {w} kg de lastre: {why}": "Add {w} kg: {why}",
    "Menos asistencia: {w} kg; {why}": "Less assistance: {w} kg; {why}",
    "Subimos a {w} kg: {why}": "Up to {w} kg: {why}",
    " con {w} kg": " at {w} kg",
    "Misma carga; suma 1 rep en los sets bajo {n} (última vez: {last}{load})":
        "Same load; add 1 rep on sets under {n} (last time: {last}{load})",
    # ── coach/ ───────────────────────────────────────────────────────────────
    "Fecha: {date} ({iso}).": "Date: {date} ({iso}).",
    "Usuario: {sex}, {age} años, {height} cm, {kg} kg, nivel {level}, objetivo {goal}, {days} días/semana.":
        "User: {sex}, {age} years, {height} cm, {kg} kg, level {level}, goal {goal}, {days} days/week.",
    "Plan de hoy: {focus} ({status}), {n} ejercicios.": "Today's plan: {focus} ({status}), {n} exercises.",
    "ninguno": "none",
    "ninguna": "none",
    "Sesión activa ({state}); ejercicio actual: {cur}; {n} sets hechos.":
        "Active session ({state}); current exercise: {cur}; {n} sets done.",
    "Contexto actual (lo agrega el sistema, no el usuario):": "Current context (added by the system, not the user):",
    "tool desconocida: {name}": "unknown tool: {name}",
    "Error interno en {name}: {error}": "Internal error in {name}: {error}",
    "Ahora mismo no puedo pensar (falló la llamada a Claude). Los comandos siguen funcionando.":
        "I can't think right now (the call to Claude failed). Commands still work.",
    "Eso no lo puedo responder. Pregúntame sobre tu entrenamiento, tu plan o tus exámenes.":
        "I can't answer that. Ask me about your training, your plan or your lab results.",
    "Me extendí demasiado.": "I went on too long.",
    "Lesiones: {injuries}. Notas: {notes}": "Injuries: {injuries}. Notes: {notes}",
    "Sets duros por músculo esta semana: {sets}": "Hard sets per muscle this week: {sets}",
    "Últimos 14 días: {sessions}": "Last 14 days: {sessions}",
    "Plan candidato: {focus}": "Candidate plan: {focus}",
    "alternativas: {alts}": "alternatives: {alts}",
    "(coach simulado) Recibí: {text}": "(simulated coach) Got: {text}",
    "ejercicio no encontrado": "exercise not found",
    "ese ítem no es del plan de hoy": "that item isn't in today's plan",
    "ejercicio no encontrado o deshabilitado": "exercise not found or disabled",
    "{name} no es posible con el equipo de {place}": "{name} isn't possible with the equipment at {place}",
    "no hay sesión activa": "no active session",
    "no hay ejercicio actual": "no current exercise",
    "set fuera de rango: {set}": "set out of range: {set}",
    "entre 1 y 10 sets": "between 1 and 10 sets",
    # ── jobs.py ──────────────────────────────────────────────────────────────
    "⚠️ Fit-OS ya no recibe tus mensajes: el webhook de la sesión de WhatsApp desapareció.":
        "⚠️ Fit-OS no longer receives your messages: the WhatsApp session webhook is gone.",
    "Vuelve a agregarlo: {cmd}.": "Add it back: {cmd}.",
    "sí": "yes",
    # ── Swipe → reply cards, timed items, hand-made plans ──
    " · ritmo en el que puedas hablar": " · a pace you can talk at",
    "*25 min*": "*25 min*",
    "*reps x kg* (10x22)": "*reps x kg* (10x22)",
    "Responde: *25 min* · ocupada · otra · salta · fin": "Reply: *25 min* · busy · other · skip · finish",
    "↩️ Desliza este mensaje y responde: {example} · otra · salta":
        "↩️ Swipe this message and reply: {example} · other · skip",
    "💪 ¡Vamos! Te mando los {n} ejercicios, uno por mensaje. Desliza el de cada ejercicio y responde ahí tus sets "
    "(*8x60 rir2*), en el orden que quieras. Si escribes sin deslizar, va al ejercicio en curso: *{name}*.":
        "💪 Let's go! Here are the {n} exercises, one message each. Swipe an exercise's message and reply with your "
        "sets (*8x60 rir2*), in any order. Anything you write without swiping goes to the current one: *{name}*.",
    "Sigue *{idx}/{total} · {name}*: {sets} × {reps}. Desliza su tarjeta o escribe aquí.":
        "Next *{idx}/{total} · {name}*: {sets} × {reps}. Swipe its card or write here.",
    "El plan de ese día ya está {status}": "That day's plan is already {status}",
    "El plan está vacío": "The plan is empty",
    "No existe el ejercicio {id}": "No exercise {id}",
    # ── "Didn't get that" → what you can write now ──
    "No entendí 🤔.": "Didn't get that 🤔.",
    "Puedes escribir:": "You can write:",
    "Ahora: *{name}*.": "Now: *{name}*.",
    "*8x60* o *8x60 rir2*": "*8x60* or *8x60 rir2*",
    "Estoy esperando el RPE de la sesión: responde de *1* a *10*, o *no* para cerrar sin él.":
        "I'm waiting for the session RPE: reply *1* to *10*, or *no* to close without it.",
    "• {log} — anotar un set (o desliza la tarjeta de otro ejercicio)":
        "• {log} — log a set (or swipe another exercise's card)",
    "• *listo* — siguiente ejercicio · *salta* — saltarlo": "• *done* — next exercise · *skip* — skip it",
    "• *otra* / *ocupada* / *no tengo barra* — cambiar el ejercicio":
        "• *other* / *busy* / *no barbell* — swap the exercise",
    "• *video* · *como* (técnica) · *+1* (un set más)": "• *video* · *how* (technique) · *+1* (one more set)",
    "• *borra* (último set) · *corrige 8x60* · *estado*": "• *undo* (last set) · *fix 8x60* · *status*",
    "• *debo terminar* — cerrar la sesión": "• *gotta go* — close the session",
    "Hoy toca *{focus}*. Puedes escribir:": "Today is *{focus}*. You can write:",
    "• *1* — empezar": "• *1* — start",
    "• *2* — cambiar a {other} · *3* — otro enfoque · *4* — hoy descanso":
        "• *2* — switch to {other} · *3* — other focus · *4* — rest today",
    "• *hoy* — ver el plan otra vez": "• *today* — see the plan again",
    "Hoy ya entrenaste ✅. Puedes escribir:": "You already trained today ✅. You can write:",
    "• *hoy* — armar y ver el plan de hoy": "• *today* — build and see today's plan",
    "• *peso 80.5* · *cintura 84* — registrar medidas": "• *weight 80.5* · *waist 84* — log measurements",
    "• *enfermo* · *de viaje* — marcar el día": "• *sick* · *travel* — mark the day",
    "• *ayuda* — todos los comandos": "• *help* — every command",
    # ── Marked days, finishing early ──
    "🤒 Anotado: hoy enfermo.": "🤒 Logged: sick today.",
    "Van {n} días seguidos.": "{n} days in a row.",
    "El plan de hoy queda en pausa; descansa e hidrátate. Cuando estés mejor escribe *ya estoy bien*.":
        "Today's plan is on hold; rest and drink water. When you feel better write *i'm better*.",
    "✈️ Anotado: hoy de viaje. El plan de hoy queda en pausa; la rotación sigue donde iba.":
        "✈️ Logged: travelling today. Today's plan is on hold; the rotation picks up where it was.",
    "🩹 Anotado: hoy lesionado. Marca la zona en Perfil → lesiones para que el plan la evite.":
        "🩹 Logged: injured today. Mark the area in Profile → injuries so the plan avoids it.",
    "Tienes una sesión abierta: escribe *debo terminar* para cerrarla.":
        "You have a session open: write *gotta go* to close it.",
    "¡Bien! 💪": "Great! 💪",
    "¡Qué bueno! 💪 La primera sesión de vuelta baja ~10 % las cargas y deja 3 en reserva; si algo se siente raro, "
    "para.":
        "Good to hear! 💪 On your first session back drop loads ~10 % and leave 3 in reserve; stop if anything "
        "feels off.",
    "• *enfermo*, *de viaje*, *lesionado* — marca el día (pausa el plan) · *ya estoy bien*":
        "• *sick*, *travel*, *injured* — mark the day (pauses the plan) · *i'm better*",
    "• *debo terminar* — cierra la sesión aunque falten ejercicios":
        "• *gotta go* — close the session even if exercises are left",
    "Quedó pendiente: {names}": "Left for next time: {names}",
    "día marcado: {kind}": "day marked: {kind}",
    "🤒 Estuviste enfermo hace poco. ¿Cómo sigues? Si aún no estás bien escribe *enfermo* y lo dejamos; si "
    "entrenas, baja ~10 % las cargas.":
        "🤒 You were sick recently. How are you? If you're still not well write *sick* and we skip it; if you "
        "train, drop loads ~10 %.",
    # ── Sunday summary ──
    "📊 *Tu semana* · {start} – {end}": "📊 *Your week* · {start} – {end}",
    "Entrenos: {n}/{target}": "Workouts: {n}/{target}",
    "Sets: {sets}{d1} · {kg} kg movidos{d2}": "Sets: {sets}{d1} · {kg} kg lifted{d2}",
    "Cardio: {m} min": "Cardio: {m} min",
    "🏆 Récords: {names}": "🏆 Records: {names}",
    "Poco volumen: {muscles}": "Low volume: {muscles}",
    "Mucho volumen: {muscles}": "High volume: {muscles}",
    "⚖️ Peso: {kg} kg{delta}": "⚖️ Weight: {kg} kg{delta}",
    "⬆️ *Sube la próxima semana*": "⬆️ *Going up next week*",
    "Semana sin entrenos. Escribe *hoy* el próximo día de entreno y arrancamos.":
        "No workouts this week. Write *today* on your next training day and we're back on.",
    "no es domingo": "not Sunday",
    # ── Warm-ups and rest alerts ──
    "🔥 Calentamiento: {steps}": "🔥 Warm-up: {steps}",
    "🔥 Calentamiento: {n} series ligeras antes del primer set, de {reps} reps (≈45 % y 65 % del peso de trabajo)":
        "🔥 Warm-up: {n} light sets before the first one, of {reps} reps (≈45 % and 65 % of the working weight)",
    " (anótalos con *c 10x20* si quieres)": " (log them with *w 10x20* if you like)",
    " y ": " and ",
    "🔥 Calentamiento anotado: {sets} (no cuenta para récords ni volumen)":
        "🔥 Warm-up logged: {sets} (doesn't count for records or volume)",
    "⏱️ Descanso listo. Set {n}/{sets} de *{name}*: {reps}": "⏱️ Rest's over. Set {n}/{sets} of *{name}*: {reps}",
}
