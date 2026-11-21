"""Prueba de Turing de tres participantes. Funciona 100% en red local, sin internet.

El interrogador (el profe) chatea a la vez con dos canales, A y B. Uno lo atiende
una persona conectada en /humano y el otro la IA (Ollama + llama3). El servidor
sortea en secreto cuál canal es cuál. Al terminar el tiempo, el interrogador
dice cuál es la máquina.

Requisitos: Python 3.9+, aiohttp (pip install aiohttp) y Ollama con el modelo llama3.
Ejecutar:  python server.py
"""
import asyncio
import difflib
import json
import os
import random
import re
import socket
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

BASE = Path(__file__).parent


def cargar_env():
    """Lee BASE/.env (líneas CLAVE=valor). Las variables ya definidas en la terminal tienen prioridad."""
    f = BASE / ".env"
    if not f.exists():
        return
    for linea in f.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


cargar_env()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
# Si defines API_KEY se usa una API en la nube (formato OpenAI, p. ej. DeepSeek) en vez de Ollama local.
# La clave se pasa solo por variable de entorno: nunca la escribas en el código ni la subas a GitHub.
API_KEY = os.getenv("API_KEY", "")
API_URL = os.getenv("API_URL", "https://api.deepseek.com/chat/completions")
MODELO = os.getenv("MODELO", "deepseek-flash" if API_KEY else "llama3")
PUERTO = int(os.getenv("PUERTO", "8080"))
DURACION = int(os.getenv("DURACION", "300"))  # segundos por ronda (Turing proponía 5 minutos)

SISTEMA = (BASE / "prompt2.md").read_text(encoding="utf-8")
# Recordatorio al final del contexto: los modelos pequeños atienden mejor lo más reciente
RECORDATORIO = (
    "Recuerda: eres Dani chateando en un juego. Una sola línea corta, tono casual, sin emojis, "
    "sin listas, sin los signos de interrogación ni de exclamación, sin ofrecer ayuda y sin hablar como asistente."
)
# Ejemplos de estilo (ejemplos.json) que se inyectan como si ya hubieran charlado
PARES = []
_ej = BASE / "ejemplos.json"
if _ej.exists():
    PARES = [tuple(p) for p in json.loads(_ej.read_text(encoding="utf-8"))]


def ejemplos_al_azar(n=5):
    """Elige pocos ejemplos y en orden distinto cada vez: si van siempre iguales, el modelo los copia."""
    msgs = []
    for q, r in random.sample(PARES, min(n, len(PARES))):
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": r}]
    return msgs
RESULTADOS = BASE / "resultados.jsonl"

humanos = set()   # personas conectadas en /humano
jueces = set()    # interrogadores conectados (para avisarles si hay persona disponible)
http = None       # ClientSession hacia Ollama
STATS = {"total": 0, "aciertos": 0}   # aciertos = el interrogador identificó a la IA


class Humano:
    def __init__(self, ws):
        self.ws = ws
        self.sesion = None


class Sesion:
    def __init__(self, juez, humano, canal_ia, canal_humano):
        self.juez = juez
        self.humano = humano
        self.canal_ia = canal_ia
        self.canal_humano = canal_humano
        self.hist = []          # {"de": "juez"|"par", "canal": "A"|"B" (solo par), "texto": str}
        self.tarea = None       # respuesta pendiente de la IA
        self.reloj = None
        self.abierta = True     # se puede conversar
        self.abortada = False
        self.inicio = time.time()


# ---------- limpieza de la salida del modelo ----------
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2B00-\u2BFF\uFE0F\u200d]+"
)
EMOTICON = re.compile(r"(?<!\w)[:;=]['-]?[)(DPp3/\\]+(?!\w)")


ASISTENTE = re.compile(
    r"\bassistant\b|como (una |un )?(ia|inteligencia artificial|modelo|asistente)|modelo de lenguaje|asistente virtual"
    r"|en qué (te )?puedo ayudar|puedo ayudarte|no tengo (gustos|emociones|cuerpo|preferencias|experiencias)"
    r"|estoy aquí para|lo siento, pero|no puedo (ayudar|experimentar)|mis instrucciones",
    re.I,
)


def limpiar(t: str) -> str:
    """Aplica por código las reglas de formato del prompt, por si el modelo las rompe."""
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    t = re.sub(r"^\s*(assistant|user)\b\s*:?\s*", "", t, flags=re.I)                    # etiquetas de rol
    t = re.sub(r"^\s*(asistente|usuario|respuesta|pregunta|dani)\s*:\s*", "", t, flags=re.I)
    t = EMOJI.sub("", t)
    t = EMOTICON.sub("", t)
    t = re.sub(r"^\s*(?:[-•*]|\d+[.)])\s+", "", t, flags=re.M)   # listas
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)                  # encabezados
    t = re.sub(r"[*_`~>]+", "", t)                               # markdown
    for c in "¿?¡!()":
        t = t.replace(c, "")
    t = re.sub(r"^[^\wáéíóúüñÁÉÍÓÚÜÑ.…]+", "", t)                   # símbolos sueltos al inicio
    t = re.sub(r"\s*\n+\s*", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip().strip('"“”').strip()
    partes = re.split(r"(?<=[.…])\s+", t)                        # máximo dos oraciones
    return " ".join(partes[:2]).strip()


def repetida(texto, previas):
    """True si el texto es casi igual a una respuesta anterior o a un ejemplo."""
    norm = lambda x: re.sub(r"\W+", " ", x.lower()).strip()
    t = norm(texto)
    return any(difflib.SequenceMatcher(None, t, norm(p)).ratio() > 0.85 for p in previas)


async def preguntar_api(mensajes, temp, extra):
    """API en la nube compatible con OpenAI (DeepSeek por defecto)."""
    payload = {
        "model": MODELO,
        "messages": [{"role": "system", "content": SISTEMA}] + ejemplos_al_azar() + mensajes
                    + [{"role": "system", "content": RECORDATORIO}] + (extra or []),
        "temperature": temp, "top_p": 0.9, "max_tokens": 120,
        "presence_penalty": 0.4, "frequency_penalty": 0.3, "stream": False,
    }
    if os.getenv("PENSAR") != "1":                 # sin modo de razonamiento: más rápido y responde directo
        payload["thinking"] = {"type": "disabled"}
    async with http.post(API_URL, json=payload, headers={"Authorization": f"Bearer {API_KEY}"}) as r:
        data = await r.json()
    if "choices" not in data:
        raise RuntimeError(data)
    return data["choices"][0]["message"].get("content") or ""


async def preguntar(mensajes, temp=0.8, extra=None):
    if API_KEY:
        return await preguntar_api(mensajes, temp, extra)
    payload = {
        "model": MODELO,
        "stream": False,
        "keep_alive": "30m",
        "messages": [{"role": "system", "content": SISTEMA}] + ejemplos_al_azar() + mensajes
                    + [{"role": "system", "content": RECORDATORIO}] + (extra or []),
        "options": {"temperature": temp, "top_p": 0.9, "num_predict": 80, "repeat_penalty": 1.15,
                    "presence_penalty": 0.4, "frequency_penalty": 0.3, "num_ctx": 4096},
    }
    if os.getenv("DESACTIVAR_THINK") == "1":      # para modelos con modo de razonamiento (qwen3)
        payload["think"] = False
    async with http.post(OLLAMA_URL, json=payload) as r:
        data = await r.json()
    return data["message"]["content"]


async def enviar(ws, data):
    try:
        await ws.send_json(data)
    except Exception:
        pass


def estado():
    libre = any(h.sesion is None for h in humanos)
    return {"type": "estado", "humano_disponible": libre, "duracion": DURACION}


async def avisar_estado():
    for j in list(jueces):
        await enviar(j, estado())


# ---------- lógica de rondas ----------
async def reloj(ses):
    await asyncio.sleep(DURACION)
    ses.abierta = False
    if ses.tarea and not ses.tarea.done():
        ses.tarea.cancel()
    await enviar(ses.juez, {"type": "time_up"})
    await enviar(ses.humano.ws, {"type": "time_up"})


async def iniciar(ws):
    libres = [h for h in humanos if h.sesion is None]
    if not libres:
        return None
    h = random.choice(libres)
    canales = ["A", "B"]
    random.shuffle(canales)                       # sorteo oculto: quién es A y quién es B
    ses = Sesion(ws, h, canal_ia=canales[0], canal_humano=canales[1])
    h.sesion = ses
    ses.reloj = asyncio.create_task(reloj(ses))
    await enviar(h.ws, {"type": "assigned"})
    await avisar_estado()
    return ses


def liberar(ses):
    ses.abierta = False
    for t in (ses.tarea, ses.reloj):
        if t and not t.done():
            t.cancel()
    h = ses.humano
    if h.sesion is ses:
        h.sesion = None
        if h in humanos:
            asyncio.create_task(enviar(h.ws, {"type": "waiting"}))
    asyncio.create_task(avisar_estado())


def registrar(ses, voto):
    ok = voto == ses.canal_ia
    STATS["total"] += 1
    STATS["aciertos"] += ok
    linea = {"modo": "tres", "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
             "canal_ia": ses.canal_ia, "voto": voto, "acierto": ok,
             "duracion_s": round(time.time() - ses.inicio), "conversacion": ses.hist}
    with RESULTADOS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    return ok


def cargar_stats():
    if not RESULTADOS.exists():
        return
    for linea in RESULTADOS.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(linea)
            if d.get("modo") == "tres":
                STATS["total"] += 1
                STATS["aciertos"] += d["acierto"]
        except Exception:
            pass


RITMO = float(os.getenv("RITMO", "1"))   # >1: la IA "escribe" más lento. <1: más rápido


async def generar(ses):
    """Pide la respuesta al modelo (con reintentos) y devuelve el texto ya limpio."""
    msgs = []
    for m in ses.hist:
        if m["de"] == "juez":
            msgs.append({"role": "user", "content": m["texto"]})
        elif m["canal"] == ses.canal_ia:
            msgs.append({"role": "assistant", "content": m["texto"]})
    previas = [m["texto"] for m in ses.hist if m["de"] == "par" and m["canal"] == ses.canal_ia]
    previas += [r for _, r in PARES]
    for intento in range(3):                       # reintenta si suena a asistente o se repite
        extra = [] if intento == 0 else [{"role": "system", "content":
                 "Tu respuesta anterior no sirvió. Responde distinto y específico a lo último que te escribieron, "
                 "con tus propias palabras y sin copiar frases anteriores."}]
        try:
            crudo = await preguntar(msgs, 0.8 + 0.1 * intento, extra)
            texto = limpiar(crudo)
            print("[IA]", repr(crudo), "->", repr(texto))
        except Exception as e:
            print("Error al hablar con Ollama:", e)
            texto = ""
        if texto and not ASISTENTE.search(texto) and not repetida(texto, previas):
            return texto
    return random.choice(["mmm no sé", "jaja a ver", "pues no sé la verdad", "ahorita no sé qué decirte"])


async def responder_ia(ses):
    """Imita el ritmo de una persona SIN avisos de "escribiendo" en ningún canal, para que el juez
    no pueda distinguir por ahí. Lo único visible es cuándo llega el mensaje."""
    inicio = time.time()
    gen = asyncio.create_task(generar(ses))        # el modelo piensa mientras corre el "tiempo humano"
    try:
        pregunta = next((m["texto"] for m in reversed(ses.hist) if m["de"] == "juez"), "")
        texto = await gen
        # leer la pregunta + teclear la respuesta (~0.1 s por carácter), como una persona
        total = (random.uniform(1.5, 3.5) + 0.02 * len(pregunta)
                 + len(texto) * random.uniform(0.09, 0.16) + random.uniform(0.3, 1.0)) * RITMO
        resto = min(total, 20 * RITMO) - (time.time() - inicio)
        if resto > 0:
            await asyncio.sleep(resto)
        if not ses.abierta:
            return
        ses.hist.append({"de": "par", "canal": ses.canal_ia, "texto": texto})
        await enviar(ses.juez, {"type": "msg", "canal": ses.canal_ia, "text": texto})
    except asyncio.CancelledError:
        gen.cancel()
        raise


# ---------- websockets ----------
async def ws_juez(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    jueces.add(ws)
    await enviar(ws, estado())
    ses = None
    try:
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(m.data)
            except ValueError:
                continue
            tipo = d.get("type")
            if tipo == "start" and ses is None:
                ses = await iniciar(ws)
                if ses:
                    await enviar(ws, {"type": "started", "duracion": DURACION})
                else:
                    await enviar(ws, {"type": "error",
                                      "text": "No hay ninguna persona disponible en este momento."})
            elif tipo == "msg" and ses and ses.abierta:
                texto = str(d.get("text", ""))[:500].strip()
                if not texto:
                    continue
                ses.hist.append({"de": "juez", "texto": texto})
                await enviar(ses.humano.ws, {"type": "msg", "text": texto})     # a la persona
                if ses.tarea and not ses.tarea.done():
                    ses.tarea.cancel()
                ses.tarea = asyncio.create_task(responder_ia(ses))              # y a la IA
            elif tipo == "guess" and ses and not ses.abortada and d.get("guess") in ("A", "B"):
                ok = registrar(ses, d["guess"])
                await enviar(ws, {"type": "result", "ia": ses.canal_ia, "correct": ok, "stats": STATS})
                liberar(ses)
                ses = None
    finally:
        jueces.discard(ws)
        if ses:
            liberar(ses)
    return ws


async def ws_humano(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    h = Humano(ws)
    humanos.add(h)
    await enviar(ws, {"type": "waiting"})
    await avisar_estado()
    try:
        async for m in ws:
            ses = h.sesion
            if m.type != WSMsgType.TEXT or not ses or not ses.abierta:
                continue
            try:
                d = json.loads(m.data)
            except ValueError:
                continue
            if d.get("type") == "msg":
                texto = str(d.get("text", ""))[:500].strip()
                if texto:
                    ses.hist.append({"de": "par", "canal": ses.canal_humano, "texto": texto})
                    await enviar(ses.juez, {"type": "msg", "canal": ses.canal_humano, "text": texto})
    finally:
        humanos.discard(h)
        ses = h.sesion
        if ses:
            # No se dice quién se fue (revelaría cuál canal era la persona): la ronda no cuenta.
            ses.abortada = True
            h.sesion = None
            await enviar(ses.juez, {"type": "aborted"})
            liberar(ses)
        await avisar_estado()
    return ws


# ---------- http ----------
async def api_stats(request):
    return web.json_response({**STATS, "humanos_conectados": len(humanos)})


async def al_iniciar(app):
    global http
    http = ClientSession(timeout=ClientTimeout(total=180))

    async def calentar():  # prueba la conexión (y carga el modelo en la GPU si es Ollama)
        try:
            await preguntar([{"role": "user", "content": "hola"}])
            print(f"Modelo {MODELO} listo ({'API en la nube' if API_KEY else 'Ollama local'}).")
        except Exception as e:
            print("No pude conectar con el modelo:", e,
                  "\nRevisa la clave y el nombre del modelo." if API_KEY else
                  "\nRevisa que Ollama este abierto y con el modelo descargado.")
    asyncio.create_task(calentar())


async def al_cerrar(app):
    await http.close()


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no envía nada, solo elige la interfaz de red
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


@web.middleware
async def sin_cache(request, handler):
    """El navegador no guarda copias de las páginas: así siempre ves la última versión."""
    resp = await handler(request)
    if not resp.prepared:
        resp.headers["Cache-Control"] = "no-store"
    return resp


def main():
    cargar_stats()
    app = web.Application(middlewares=[sin_cache])
    app.on_startup.append(al_iniciar)
    app.on_cleanup.append(al_cerrar)

    async def juez(request):
        return web.FileResponse(BASE / "static" / "juez.html")

    async def humano(request):
        return web.FileResponse(BASE / "static" / "humano.html")

    app.router.add_get("/", juez)
    app.router.add_get("/humano", humano)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/ws/juez", ws_juez)
    app.router.add_get("/ws/humano", ws_humano)
    app.router.add_static("/static", BASE / "static")
    ip = ip_local()
    if API_KEY:
        print(f"\nModelo: {MODELO} por API en la nube ({API_URL})")
    else:
        print("\nNo hay API_KEY: se usara Ollama local. Para usar la API pon API_KEY en el archivo .env")
    print(f"\nInterrogador (el profe):  http://{ip}:{PUERTO}/")
    print(f"Persona que responde:     http://{ip}:{PUERTO}/humano\n")
    try:
        web.run_app(app, host="0.0.0.0", port=PUERTO, print=None)
    except OSError as e:
        if getattr(e, "errno", None) in (10048, 98, 48):
            print(f"\nEl puerto {PUERTO} ya esta en uso: hay otro servidor abierto. Cierra esa ventana o usa otro "
                  f"puerto (PowerShell: $env:PUERTO=\"8081\"; o pon PUERTO=8081 en .env).")
        else:
            raise


if __name__ == "__main__":
    main()
