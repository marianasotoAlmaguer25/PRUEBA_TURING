# Prueba de Turing de tres participantes

Todo corre en tu red local. No necesita internet una vez instalado.

## Cómo es la ronda

El interrogador (el profe) abre la página principal y ve dos chats, A y B. Cada pregunta que escribe llega a los dos. Uno de los canales lo contesta una persona conectada en /humano y el otro la IA (llama3 con prompt2.md). El servidor sortea en secreto cuál es cuál en cada ronda. Cuando termina el tiempo (5 minutos por defecto), el interrogador marca cuál cree que es la máquina y el sistema revela la respuesta y acumula estadísticas.

Hace falta que haya al menos una persona conectada en /humano para poder iniciar una ronda.

## Preparación (una sola vez, con internet)

1. Instala Ollama y descarga el modelo:  ollama pull llama3
2. Instala la dependencia:  python -m pip install aiohttp
   Para llevarla a un equipo sin internet:  python -m pip download aiohttp -d ruedas
   y allá:  python -m pip install --no-index --find-links ruedas aiohttp

llama3 de 8B cuantizado ocupa unos 4.7 GB y cabe en una 4050 de 6 GB. Si va lento o se queda sin memoria usa un modelo más chico: ollama pull llama3.2:3b y arranca con la variable MODELO=llama3.2:3b

## Uso

1. Abre Ollama.
2. Ejecuta:  python server.py
3. La consola muestra dos direcciones:
   - http://IP:8080/         interrogador (el profe)
   - http://IP:8080/humano   persona que responde (otro equipo)
4. Conecta todos los equipos a la misma red (router sin internet o hotspot del celular). Si Windows pregunta por el firewall, permite el acceso en redes privadas, o abre el puerto 8080. Abre siempre las direcciones http, no los HTML con doble clic.

## Variables opcionales

MODELO (llama3), DURACION (300 segundos), PUERTO (8080), OLLAMA_URL (http://127.0.0.1:11434/api/chat)
Ejemplo en PowerShell:  $env:DURACION="180"; python server.py

## Resultados

Cada veredicto se guarda en resultados.jsonl con la conversación completa. Estadísticas en vivo: /api/stats
Si la persona se desconecta a media ronda, la ronda se cancela y no cuenta.

## Tipografía

El diseño usa Inter. Para que se vea igual sin internet, descarga el woff2 variable una vez y guárdalo como static/fonts/Inter.woff2. Sin ese archivo se usa Segoe UI u otra fuente del sistema.

## Notas

- El prompt se lee de prompt2.md al arrancar. Edítalo y reinicia el servidor.
- El servidor limpia por código cada respuesta de la IA (signos prohibidos, emojis, listas, markdown, máximo dos oraciones) y simula un tiempo de escritura.

## Si la IA suena a asistente

- prompt2.md define el personaje (Dani). Cambia nombre, gustos y datos a tu gusto, pero deja siempre a la IA con gustos y opiniones propias; si le prohíbes inventar, contesta como asistente ("como IA no tengo gustos").
- ejemplos.json son pares pregunta y respuesta que se inyectan como si ya hubieran charlado. Agrega los tuyos con las preguntas típicas que hace el profe: son lo que más influye en el estilo.
- El servidor descarta y reintenta (hasta 3 veces) si la respuesta suena a asistente.
- Probar otro modelo: ollama pull NOMBRE y arrancar con $env:MODELO="NOMBRE"; python server.py
  Con modelos que razonan (qwen3) agrega también $env:DESACTIVAR_THINK="1"

## Identidad compartida

La persona de /humano ve una ficha del personaje (Dani) con los mismos datos que usa la IA en prompt2.md, para que ambas respondan igual a "cómo te llamas" y similares, y el profe no pueda deducirlo por nombres de compañeros o de la clase. Si cambias el personaje en prompt2.md, cambia también esa ficha en static/humano.html.

## Sin indicador de "escribiendo"

Ningún canal muestra "escribiendo...": ese aviso delataba cuál era la IA, porque aparecía a destiempo respecto a la persona. La IA solo retrasa su mensaje a un ritmo humano (leer y teclear); se ajusta con la variable RITMO.

## Usar una API en la nube (DeepSeek) en vez de Ollama

Sin GPU ni descarga del modelo, pero necesita internet. En la misma ventana, antes de arrancar:

    set API_KEY=tu_clave
    python server.py

Por defecto usa https://api.deepseek.com y el modelo deepseek-flash (V4.1 Flash). Para otra API compatible con OpenAI (Groq, OpenRouter...) define también API_URL y MODELO. Si quieres el modo de razonamiento: set PENSAR=1 (más lento, no recomendado para chat).
La clave es secreta: no la escribas en el código ni la subas a GitHub. Sin API_KEY se usa Ollama local como siempre.

## Configurar la clave sin escribirla cada vez (.env)

Copia .env.example como .env, pega tu clave en la línea API_KEY= y arranca con: py server.py
El archivo .env está en .gitignore y no se sube a GitHub. Las variables de la terminal tienen prioridad sobre .env.

Ojo con la sintaxis según la terminal: en PowerShell es  $env:API_KEY="tu_clave"  y en el Símbolo del sistema (cmd) es  set API_KEY=tu_clave
