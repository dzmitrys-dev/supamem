**Idiomas:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ 73f3e33 -->

> Esta traducción fue generada con asistencia de IA. Las correcciones de hablantes nativos son bienvenidas vía PR.

<div align="center">

# 🧠 supamem

**Memoria dual respaldada por Qdrant para agentes de codificación con IA**

*Da a Claude Code, Cursor y OpenCode una memoria semántica + estructural persistente en cada proyecto.*

[![PyPI](https://img.shields.io/pypi/v/supamem?style=flat-square&logo=pypi&logoColor=white&color=blue)](https://pypi.org/project/supamem/)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-success?style=flat-square)](LICENSE)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.10%2B-DC382D?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![MCP](https://img.shields.io/badge/MCP-1.13%2B-9333EA?style=flat-square)](https://modelcontextprotocol.io/)
[![Powered by SoftChat](https://img.shields.io/badge/Powered%20by-SoftChat-FF4D8D?style=flat-square)](https://app.softchat.ru)

</div>

---

> ### 👋 Construido por [**Dzmitry Sukhau**](https://www.linkedin.com/in/dzmitrys/) — Arquitecto de Soluciones / Software AI-native / CTO
>
> Disponible para **consultoría** sobre productos de IA, **integración de IA en productos existentes** y **automatización de procesos de negocio**.
>
> Si estás lanzando funcionalidades LLM, evaluando pipelines de retrieval, endureciendo sistemas agénticos, o construyendo un producto AI-first desde cero — hablemos.
>
> [![LinkedIn — Dzmitry Sukhau](https://img.shields.io/badge/LinkedIn-Dzmitry%20Sukhau-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)
> &nbsp;&nbsp;
> [![Open to Consulting](https://img.shields.io/badge/Open%20to-Consulting%20%26%20Architecture-22C55E?style=for-the-badge&logo=anthropic&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)

---

## ✨ ¿Qué es supamem?

`supamem` es una CLI de un solo binario que cablea una **capa de memoria de nivel productivo** para
cualquier asistente de codificación con IA. Suéltalo en un repo nuevo, ejecuta `supamem init`, y tus
agentes ganan al instante:

- 🔍 **Búsqueda semántica** sobre notas del proyecto, ADRs, decisiones y conversaciones pasadas (retrieval híbrido sparse+dense)
- 🤖 **Servidor MCP** con el que cualquier cliente compatible (Claude Code, Cursor, OpenCode) puede hablar
- 🪝 **Hooks por cliente** que cargan automáticamente la memoria relevante al iniciar la sesión y al editar archivos
- 📊 **Estadísticas Welford** para ver qué memoria realmente se está recordando
- 🧪 **Arnés de evaluación** con un corpus dorado de 33 consultas para detectar regresiones de retrieval

Probado en producción dentro de [SoftChat](https://app.softchat.ru) (Fases 80.1–80.5) antes de ser
extraído en un paquete independiente que cualquier equipo puede adoptar.

---

## 🎯 Por qué existe supamem

**El problema:** Los agentes de codificación no tienen memoria entre sesiones. Cada vez que abres
una conversación nueva en Claude Code / Cursor / OpenCode, el modelo no tiene contexto sobre tu
codebase, decisiones pasadas, ADRs, problemas conocidos o convenciones. Así que o:

1. **Re-pegas 5–15 KB de contexto** al inicio de cada sesión (lento, propenso a errores, costoso), o
2. Dejas que el agente **dé tumbos** — recorre el repo con grep, hace preguntas redundantes, olvida
   las decisiones de la semana pasada, y redescubre los mismos problemas que documentaste hace seis meses.

**La solución:** Una capa de memoria semántica + estructural persistente que recupera automáticamente
los *correctos* 1–2 KB de contexto para el prompt *actual* — sin pegar manualmente, sin re-explicar,
sin reventar el contexto.

> **Bench Fase 80.1 (33 goldens etiquetados, sesiones reales de Claude Code):**
> **−78.5% tokens vs retrieval ingenuo de documento entero** con el mismo recall, **p95 73 ms** end-to-end.
>
> La evaluación completa es la misma que corrimos dentro de SoftChat para fijar el pipeline productivo.
> Metodología: 33 consultas representativas → 4 brazos de retrieval comparados (baseline_union,
> tuned_current, tuned_hybrid, mem0_vector) → token count + IC de recall + latencia medidos por brazo.

### 📊 Consumo de tokens: agente con memoria vs sin memoria

Los números siguientes son por **sesión típica de 30 turnos en Claude Code** asumiendo un codebase
real con ~50 ADRs / insights / rules (≈ lo que ship SoftChat). YMMV — pero la *proporción* entre brazos
se mantiene.

| Enfoque | Tokens/turno | Tokens/sesión 30-turnos | Notas |
|---|---:|---:|---|
| ❌ Sin capa de memoria | **≈ 0** auto-inyectados, pero pegas contexto manualmente | **30,000–80,000** (pegado manual, repetido) | Gastas carga cognitiva en copiar en lugar de construir |
| ⚠️ RAG ingenuo (embed de documento entero) | ~5,800 / turno | **~174,000** | Inflado, recupera archivos grandes cuando solo necesitabas un párrafo |
| ✅ **supamem `tuned_hybrid`** | **~1,250 / turno** | **~37,500** | Mismo recall, **−78.5% tokens** vs RAG ingenuo |

### 💰 Ahorro aproximado en costo de inferencia

Tarifa pública de Anthropic API (Mar 2026):
**Sonnet 4.6 = $3 / Mtok input** · **Opus 4.7 = $15 / Mtok input**.

| Modelo | Tokens ahorrados/sesión vs RAG ingenuo | Costo ahorrado/sesión | Mensual (110 sesiones) |
|---|---:|---:|---:|
| Sonnet 4.6 | **136,500** | **$0.41** | **~$45/dev** |
| Opus 4.7 | **136,500** | **$2.05** | **~$225/dev** |

Un equipo de 10 ingenieros corriendo Opus ahorra **~$2,250/mes** solo en tokens de input — sin contar
el costo de iteración más lenta, decisiones perdidas, y tiempo gastado re-pegando contexto.
El ahorro de tokens de output (menos alucinaciones, menos turnos de ida y vuelta) se compone encima.

### 🥊 vs las alternativas

| | Sin memoria | RAG ingenuo | mem0 / hechos atómicos | **supamem (tuned_hybrid)** |
|---|:---:|:---:|:---:|:---:|
| Auto-inyección al iniciar sesión | ❌ | ⚠️ | ✅ | ✅ |
| Retrieval híbrido sparse+dense | ❌ | ❌ | ❌ | ✅ |
| Preservación de identificadores de código | ❌ | ✅ | ❌ (descarta nombres) | ✅ |
| Schema fijo + eval golden | ❌ | ❌ | ❌ | ✅ |
| Multi-cliente (Claude/Cursor/OpenCode) | ❌ | ❌ | ⚠️ | ✅ |
| Latencia p95 | n/a | ~120 ms | ~80 ms | **73 ms** |
| Inflación de tokens | Alta (manual) | Más alta | Baja pero con pérdida | **Más baja con recall completo** |

**¿Por qué híbrido?** BM25 captura *identificadores exactos* (`ChatService.generate`, nombres de
env-vars, rutas de archivo) que los embeddings densos difuminan. Dense captura *intención semántica*
("¿cómo manejamos los webhooks de billing?") que BM25 se pierde. La fusión RRF combina ambos rankings
para obtener lo mejor de cada uno.

**¿Por qué no mem0?** La extracción de hechos atómicos de mem0 pierde identificadores de código —
el recall en el bench de 33 consultas fue **0.015** (efectivamente cero). Genial para memoria estilo
CRM personal, no para retrieval consciente del código.

---

## ⚡️ Quickstart de 60 segundos

```bash
# 1. Instalar (uv es la ruta más rápida)
uv tool install supamem

# 2. Iniciar Qdrant (una vez, ~30s)
docker run -d -p 6333:6333 -p 6334:6334 -v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest

# 3. Bootstrap de tu proyecto
cd your-project
supamem init

# 4. Cablearlo a tu cliente de IA
supamem install --client claude-code   # o cursor / opencode

# 5. Confirmar que todo está sano
supamem doctor
```

Eso es todo. Abre Claude Code (o tu cliente preferido) dentro del proyecto — la herramienta de memoria
ya está en el menú. ✨

---

## 👀 Velo funcionar — `supamem live`

Ejecuta `supamem live` en una terminal lateral para ver cada llamada de retrieval en tiempo real — ideal junto a Claude Code / Cursor / OpenCode para visibilidad instantánea de las inyecciones silenciosas del hook PreToolUse (silenciosas por diseño: así ahorran tokens).

![supamem live dashboard](docs/media/supamem-live.svg)

El **banner SessionStart** (v0.1.4+) también lanza una línea de estado en tu cliente IA al abrir la sesión: `🧠 supamem v0.1.4 · <collection> · <N> chunks · audit <path>` — auto-detecta Claude Code / Cursor / OpenCode vía variables de entorno.

> 🎬 **Demo interactiva:** [`supamem-live.cast`](docs/media/supamem-live.cast) — pégalo en [asciinema.org/player](https://asciinema.org/) o ejecuta localmente `asciinema play docs/media/supamem-live.cast`.

---

## 🚀 Funcionalidades

| Funcionalidad | Descripción |
|---|---|
| 🔍 **Retrieval híbrido** | Fusión sparse (BM25) + dense (MiniLM) afinada, schema fijo D-25 |
| 🎯 **Reranker consciente del código** | Cross-encoder `mxbai-rerank-base-v2` (Apache-2.0) re-puntúa los candidatos de `tuned_hybrid` por defecto. Desactívalo con `retrieval.reranker = "off"` para volver al comportamiento previo a v0.2.4a1. (Phase 8, RERANK-01..04) |
| ⏳ **Validez temporal por fuente** | Cada chunk lleva `valid_from`/`valid_to`; reindexar un archivo modificado supersede atómicamente los chunks previos y el filtro de retrieval excluye los puntos superados en todos los backends. Decay de recencia opcional solo para transcripciones (apagado por defecto). Auto-GC tras `retention_days = 90` (`0` = conservar siempre / colecciones de auditoría). (Phase 9, TEMP-01..03) |
| 📚 **Chunker Markdown** | Consciente de headers, chunks de 200-token con tope suave de 250-token (T-1) |
| 🤖 **Servidor MCP** | Transportes `stdio` (default) y `http`, SDK oficial `mcp` |
| 🪝 **Hooks multi-cliente** | session-start de Claude Code, session-start de OpenCode, MDC de Cursor |
| 🧰 **Instalación de un comando** | Patcheo atómico de config con auto-backup y rollback |
| 🩺 **`supamem doctor`** | Probar Qdrant, resolver cadena de config, exponer drift de versión |
| 👀 **`supamem live`** | Dashboard Rich-Live siguiendo el audit JSONL — visibilidad en tiempo real de las llamadas de retrieval (v0.1.4+) |
| 🎬 **Banner SessionStart** | Banner cross-cliente de una línea inyectado al abrir sesión (Claude Code / Cursor / OpenCode), v0.1.4+ |
| 📊 **Contadores Welford** | Trackear tasa de recall, latencia, volumen de queries por proyecto |
| 🧪 **Arnés de eval** | Corpus dorado de 33 consultas + detección de regresión |
| 🔁 **Migración brownfield** | Detectar `dev_memory` existente y migrar de forma no destructiva |
| 🎨 **CLI estiloso** | Spinners, paneles y color con Rich para que siempre veas el progreso |

---

## 📋 Prerrequisitos

Solo necesitas dos cosas: **Python 3.12+** y **Qdrant**. Todo lo demás es opcional.

<details>
<summary><b>🐍 Python 3.12+ &nbsp;·&nbsp; click para expandir comandos de instalación</b></summary>

```bash
# macOS (Homebrew)
brew install python@3.12

# Linux (Ubuntu/Debian)
sudo apt install python3.12 python3.12-venv

# Windows (PowerShell)
winget install Python.Python.3.12
```

Recomendamos fuertemente instalar [`uv`](https://docs.astral.sh/uv/) — el gestor de paquetes Python más rápido:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

</details>

<details>
<summary><b>🗄️ Qdrant 1.10+ &nbsp;·&nbsp; base de datos vectorial (requerida)</b></summary>

La ruta más simple es Docker:

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $HOME/.qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

O con `docker compose`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./qdrant_data:/qdrant/storage"]
    restart: unless-stopped
```

¿Sin Docker? Corre un cluster gestionado en [Qdrant Cloud](https://cloud.qdrant.io/) (con tier gratuito)
y apunta `supamem` a la URL vía `supamem init`.

</details>

<details>
<summary><b>🤖 Un cliente compatible con MCP &nbsp;·&nbsp; elige al menos uno</b></summary>

| Cliente | Instalación | Notas |
|---|---|---|
| [Claude Code](https://claude.com/claude-code) | `npm install -g @anthropic-ai/claude-code` | Soporte MCP de primera clase |
| [Cursor](https://cursor.com/) | Descargar de cursor.com | Usa reglas MDC + MCP |
| [OpenCode](https://opencode.ai/) | `curl -fsSL https://opencode.ai/install \| bash` | TUI open-source, MCP nativo |

</details>

---

## 📦 Instalación

```bash
# Recomendado: uv (más rápido, aislado)
uv tool install supamem

# Alternativa: pipx (también aislado)
pipx install supamem

# pip plano (en un venv)
pip install supamem
```

Verificar:

```bash
supamem --version
```

Deberías ver un banner de colores y la línea de créditos. 🎨

> **Última versión:** `v0.1.5` está publicado en [PyPI](https://pypi.org/project/supamem/). Lanzado vía
> Trusted Publisher OIDC — cada wheel tiene atestación de procedencia.

### Modelos en caché en la instalación

`supamem install <client>` y `supamem init` descargan proactivamente todos los
prerrequisitos ML (MiniLM ~90 MB, BM25 ~10 MB, mxbai-rerank-base-v2 ~1 GB) con
una barra de progreso. Las invocaciones CLI en frío después de la instalación
(`supamem --help`, `supamem doctor`, `supamem --version`) no disparan ningún
egreso de red. ¿Primer arranque sin red? Pasa `--skip-models` y luego ejecuta
`supamem repair` cuando la red esté disponible.

Los modelos viven bajo `platformdirs.user_cache_dir("supamem")/models/`
(sobreescribible con `SUPAMEM_CACHE_DIR`).

### Alcance de los subagentes (v0.2.5+)

Si usas subagentes de Claude Code provistos por GSD, superpowers, hookify, o cualquier
plugin que clave una whitelist `tools:` en sus definiciones de agentes, esos agentes no
pueden alcanzar el servidor MCP de supamem a menos que `mcp__supamem__*` esté en la
whitelist — incluso si la sesión padre tiene supamem conectado. Los subagentes solo
heredan las herramientas que su frontmatter lista.

`supamem install` y `supamem repair` aplican este parche por ti automáticamente:

```bash
supamem install --client claude-code   # patches ~/.claude/agents/ + <project>/.claude/agents/
supamem repair                         # re-applies if a plugin overwrites your agents
```

El parche es idempotente (correr dos veces no produce cambios), preserva tu estilo YAML
(CSV vs lista), y omite con advertencia los archivos de agentes enlazados simbólicamente.
Los archivos con una línea `tools:` faltante o vacía tienen herencia completa según la
semántica de Claude Code y se dejan intactos.

El manifiesto de respaldo vive en `~/.cache/supamem/agent_patches.json`. Reviértelo
limpiamente con:

```bash
supamem unpatch-agents
```

Pasa `--skip-patch-agents` para optar por no aplicarlo en cualquiera de
`install` / `init` / `repair`.

#### Desinstalar supamem

```bash
supamem unpatch-agents      # restore agent whitelists first
pip uninstall supamem
```

No existe un hook portátil de desinstalación en `pip` / `uv` / `pipx` en 2026, así que
los dos pasos son el contrato soportado. `supamem doctor` muestra la ruta del manifiesto
y el recordatorio para que descubras este flujo de manera natural.

---

## 🎯 Superficie CLI

| Comando | Propósito |
|---|---|
| `supamem init` | Bootstrap greenfield — prueba Qdrant, crea colección, escribe `.supamem/config.toml` |
| `supamem install --client <name>` | Patchear config de cliente (`claude-code`, `cursor`, `opencode`) — atómico con backup. v0.2.5+: auto-parchea `~/.claude/agents/` y `<project>/.claude/agents/` para añadir `mcp__supamem__*` a las whitelists `tools:` restrictivas; opta por no con `--skip-patch-agents`. |
| `supamem index` | Embeber memorias de dev en Qdrant usando el pipeline tuned-hybrid fijo (D-25) |
| `supamem mcp-server` | Correr el servidor MCP (`--transport stdio` default; `--transport http` para HTTP) |
| `supamem hook <client>` | Hooks de sesión/edición por cliente (llamados por el cliente) |
| `supamem doctor` | 🩺 Probar Qdrant, imprimir cadena de config resuelta, reportar drift de versión |
| `supamem stats` | Contadores Welford schema-v2 desde `.supamem/state/` |
| `supamem live` | 👀 Dashboard en vivo siguiendo el audit JSONL — pipe-safe (JSONL plano cuando no hay TTY); maneja rotación, redimensionado, Ctrl-C |
| `supamem migrate` | Migración brownfield desde una colección `dev_memory` preexistente |
| `supamem eval` | Correr el arnés de bench. `--suite goldens` (por defecto, corpus dorado de 33 consultas para regresión) o `--suite longmemeval_s` (descarga perezosa de LongMemEval_S, ~3 GB en la primera ejecución; el camino rápido de CI es un subconjunto de 10 preguntas estratificado por eje, las ~500 preguntas completas requieren `--full`). v0.3.0a4+: emite una pasada scoped + unscoped por pregunta; el gate de publicación es **scoped-only** ([ADR-0001](docs/adr/0001-scoped-only-bench-gate.md)). Nuevo `--suite longmemeval_scoped_smoke` empaquetado (≤5 preguntas, sin descarga perezosa) para CI. Emite un envelope JSON estilo MTEB a `~/.supamem/eval/<utc-iso>.json`. El juez por defecto es heurístico (offline); pasa `--judge ollama:<model>` para un juez Ollama local — los endpoints SaaS son rechazados (D-07). Extra opcional: `pip install supamem[eval]` para la tríada RAGAS (v0.3.0a2+). Modo legado `--regress` preservado. |
| `supamem uninstall --client <name>` | Revertir `supamem install` limpiamente |
| `supamem unpatch-agents` | 🔄 Revertir los parches de alcance de subagentes (v0.2.5+). Restaura los archivos de agentes a su forma anterior al parche según el manifiesto en `~/.cache/supamem/agent_patches.json`. Omite con advertencia los archivos que hayas editado desde entonces. Córrelo ANTES de `pip uninstall supamem` para una desinstalación limpia. |

Cada comando de larga ejecución muestra un **spinner en vivo** con tiempo transcurrido para que siempre
sepas que está trabajando. Usa `--help` en cualquier subcomando para detalles.

---

## 📜 Ingesta de transcripciones (v0.2.2a1+)

supamem puede indexar tu **historial de sesiones de Claude Code** como chunks tipo cajón de Q+A junto al corpus Markdown de tu proyecto, exponiendo decisiones pasadas y trazas de uso de herramientas en `dual_memory_search`. Desactivado por defecto — actívalo con `--transcripts`.

```bash
# Indexa transcripciones de Claude Code desde la ubicación por defecto (~/.claude/projects/)
supamem index --transcripts

# O apunta a un directorio específico
supamem index --transcripts /path/to/sessions/

# Omite el corpus regular del proyecto e indexa solo transcripciones
supamem index --transcripts --transcripts-only

# Limita a sesiones recientes (por defecto: 180 días; --since 0 desactiva el filtro)
supamem index --transcripts --since 30d
```

Configura bajo `[supamem.transcript]` en `.supamem/config.toml`:

```toml
[supamem.transcript]
default_root           = "~/.claude/projects/"
since_days             = 180
tool_payload_max_chars = 2000
chunk_soft_max_tokens  = 600
include_paths_glob     = []
exclude_paths_glob     = []   # excluye sesiones sensibles, p. ej. ["**/banking-*.jsonl"]
```

> ⚠ **Las transcripciones pueden contener secretos.** Claves de API, tokens y otras credenciales a veces acaban pegadas en sesiones de Claude Code. v0.2.2a1 **no incluye redacción** — revisa tu colección Qdrant en `~/.cache/supamem` antes de compartirla. Excluye manualmente sesiones sensibles con `exclude_paths_glob`. La redacción está planificada para v0.3 vía un futuro grupo de plugins `supamem.redactor`.

Formatos de transcripción soportados actualmente: **JSONL de Claude Code** (Cursor SQLite y exportación de ChatGPT quedan diferidos a plugins posteriores).

---

## 🔎 Recuperación con filtro (v0.2.3a1+)

Filtra los resultados de recuperación por categoría de ruta de código mediante el parámetro
`where` en `dual_memory_search` (y el alias `qdrant_find`):

```python
# Solo chunks clasificados como código backend
dual_memory_search(query="auth flow", where={"room": "backend"})

# OR entre varios rooms (Qdrant MatchAny)
dual_memory_search(query="rate limit", where={"room": ["backend", "tests"]})
```

Cada chunk indexado lleva `payload.room` — uno de `backend`, `frontend`, `tests`, `docs`,
`scripts`, `config`, `migrations`, `types` o `null`. La clasificación es **igualdad exacta
por componente de ruta** (separando por `/`) — un fichero en `data/chest_xray/img.png`
**nunca** se clasifica como `tests`. En `where`, múltiples claves se combinan con AND;
los valores tipo lista dentro de una clave se combinan con OR.

Sobrescribe el mapa de palabras clave por defecto en `.supamem/config.toml`:

```toml
[supamem.classifier.rooms]
tests      = ["tests", "test", "__tests__"]
backend    = ["src", "backend", "api"]
frontend   = ["frontend", "web", "client", "components"]
# La prioridad la determina el orden de las claves — gana la primera coincidencia.
# Poner `tests` antes de `backend` hace que tests/backend/api_test.py se clasifique como `tests`.
```

`supamem doctor` muestra el mapa de rooms activo con la procedencia `[source: ...]`,
el `classifier_hash` almacenado y un histograma por room (incluyendo un cubo `null`
para los chunks sin coincidencia).

Cambiar `[supamem.classifier.rooms]` desencadena un **barrido de reclasificación** único
en el siguiente `supamem index` — `set_payload` de Qdrant por room, **coste cero de
re-embedding**. Las colecciones anteriores a v0.2.3 se migran automáticamente en la
primera invocación de `index` tras la actualización.

Los chunks de transcripción (chunker == `transcript`) se clasifican como `room = null`
por construcción — fíltralos mediante la clave existente `payload.chunker`.

---

## 🎯 Reranker consciente del código (v0.2.4a1+)

Cada consulta `tuned_hybrid` ahora re-puntúa los candidatos fusionados por RRF a
través de un cross-encoder (`mixedbread-ai/mxbai-rerank-base-v2`, Apache-2.0,
~1 GB) **por defecto**. Mayor precisión en consultas centradas en código; la
salida de emergencia v0.2.0 es `retrieval.reranker = "off"`, que restaura el
comportamiento idéntico byte-a-byte previo a Phase 8.

```toml
[supamem.retrieval]
reranker = "mxbai_v2"  # default en v0.2.4a1+; "off" restaura el comportamiento previo a Phase 8

[supamem.retrieval.reranker]
model_id         = "mixedbread-ai/mxbai-rerank-base-v2"
top_n            = 50   # tamaño del pool de rerank; se ajusta al número de candidatos fusionados
prefetch_per_arm = 50   # ampliado desde 20 cuando el reranker está activo
batch_size       = 16
```

Cuando el reranker está activo, `tuned_hybrid` amplía `PREFETCH_LIMIT` a 50 por
brazo, salta el multiplicador de recencia T-4 (cross-encoder + recency-prior es
contraproducente para retrieval de código según PROJECT.md), y ejecuta el
cosine-dedup T-5 + token-budget T-8 DESPUÉS del rerank.
`RetrievedChunk.rerank_score` lleva el logit del cross-encoder cuando el
reranker está activo; el `score` primario también se reemplaza por él.

`supamem doctor` añade un panel **Reranker** después del panel Retrieval
existente: nombre del reranker activo, model_id, ruta de caché, tamaño en disco
+ detección de descarga parcial, latencia de la última carga, p50/p95 de las
últimas 100 consultas, y dispositivo detectado (cuda/mps/cpu). Cuando la caché
está parcial o corrupta, ejecuta `supamem repair` — el punto canónico de
self-heal dirigido por doctor que re-descarga archivos del modelo, re-sincroniza
`share/`, repara los bloques administrados de CLAUDE.md/AGENTS.md y restaura la
configuración del cliente. Idempotente.

Terceros pueden registrar rerankers personalizados mediante el nuevo grupo de
entry-point `supamem.reranker` (4º grupo junto a retrieval / embedder / chunker):

```toml
[project.entry-points."supamem.reranker"]
my_reranker = "my_pkg.module:MyReranker"
```

Protocolo de plugin: `rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]`.
Carga perezosa del modelo en la primera llamada; el calentamiento eager corre a
través del pipeline de fetch de install/init/repair.

---

## ⏳ Validez temporal por fuente (v0.3.0a1+)

Cada chunk indexado lleva un campo binario `valid_to`:

- `valid_to = null` → vigente
- `valid_to ≤ now()` → superado (filtrado fuera de toda búsqueda)

Cuando un archivo cambia y lo reindexas, el indexador, atómicamente:

1. Hace scroll de todos los chunks existentes para esa ruta de archivo.
2. Aplica `set_payload(valid_to = now())` a cada uno (cierra la ventana de validez previa).
3. Hace upsert de los nuevos chunks bajo UUIDs derivados de un hash de contenido,
   con `valid_to = null`.

Los chunks viejos y nuevos coexisten en Qdrant; solo los nuevos se devuelven en retrieval
hasta que el barrido de auto-GC borra los viejos pasados de `retention_days`. El filtro
de retrieval se construye en un único sitio y lo heredan todos los backends
(`tuned_hybrid` ambas ramas Prefetch, `dense`, `bm25`, `qdrant_find`,
`dual_memory_search`) — usa el `IsEmptyCondition` de Qdrant (NO `IsNullCondition`,
ver [Qdrant#5342](https://github.com/qdrant/qdrant/issues/5342): `IsNull` no matchea
campos ausentes).

Configura en `.supamem/config.toml`:

```toml
[supamem.retrieval.temporal]
retention_days = 90          # 0 = conservar siempre (compliance / auditoría)
```

### Decay de recencia solo para transcripciones (opt-in, apagado por defecto)

El código, los ADRs y la documentación no "envejecen". Las transcripciones, sí — los
turnos antiguos de soporte con APIs deprecadas distraen al agente del diálogo actual.
Phase 9 incorpora un knob de decay multiplicativo con piso (multiplicative-floor) que
corre **solo** sobre chunks de transcripción, después del rerank, y nunca se activa
automáticamente para código / ADR / docs:

```toml
[supamem.retrieval.recency.per_source.transcript]
enabled        = true            # default false
half_life_days = 14.0
alpha          = 0.7             # piso: la transcripción más vieja conserva 0.7x del score
```

Ejemplo trabajado con los defaults bloqueados (`alpha = 0.7`, `half_life_days = 14`):

| Age (days) | Multiplier         |
|------------|--------------------|
| 0          | 1.000              |
| 7          | 0.924              |
| 14         | 0.850              |
| 28         | 0.775              |
| ∞          | 0.700 (floor at α) |

El ranking de código / ADR / docs queda byte-idéntico cuando se cambia el knob —
verificado por un test end-to-end de identidad de bytes (criterio TEMP-03).

Referencias: [Customers.ai recency-weighted scoring](https://customers.ai/recency-weighted-scoring),
[Snowflake Cortex Search scoring docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/cortex-search-customize-scoring).

### Doctor

`supamem doctor` muestra un panel `Temporal validity` (entre Reranker y Subagent
reachability) que lista contadores de live / superseded / awaiting_gc / future-dated,
desglose por fuente, `valid_from` más antiguo y más reciente, y la procedencia de
`retention_days`. Solo lectura por construcción; nunca cambia el exit code de doctor.

### Migración

El primer `supamem index` post-upgrade rellena `valid_to=null` en puntos legados
(controlado por una clave reservada del manifest, idempotente en sucesivas corridas).
Defensa en profundidad junto al filtro `IsEmpty` de runtime.

> ⚠ **El retention por defecto es destructivo** para usuarios que vienen de v0.2.x
> con colecciones modo auditoría de más de 90 días. Setea
> `[supamem.retrieval.temporal] retention_days = 0` para deshabilitar el auto-GC.

---

## 🔭 Backend de retrieval con filtro (v0.3.0a3+)

`filtered_dense` es un backend de retrieval *scoped+capped* que envuelve a
`tuned_hybrid` con un filtro `where` y un tope de preview por hit. Úsalo cuando quieras
forzar a nivel de backend "dame resultados rankeados acotados a *este* path/room, con
los previews recortados a *N* caracteres antes incluso de salir de Qdrant".

```toml
[supamem.retrieval]
backend = "filtered_dense"

[supamem.retrieval.filtered_dense]
preview_chars = 240   # default 240; 0 deshabilita la truncación por completo
```

La selección espeja a la de cualquier otro backend (`tuned_hybrid`, `dense`, `bm25`) —
registrado vía el grupo de plugins `supamem.retrieval`; cambiar es un edit puramente de
config, sin tocar código. El cap de transporte MCP (`mcp.caps.max_preview_chars`) sigue
aplicándose por encima del cap de backend; ambos se desactivan independientemente
seteándolos a `0`.

### Filtro `where` — magic keys

`dual_memory_search` (y el alias `qdrant_find`) aceptan un parámetro
`where: dict[str, str | list[str]]` que se traduce a un filtro de payload de Qdrant.
Más allá de la key `room` de Phase 7, se reconocen dos magic keys nuevas:

```python
# 1. path_prefix — match exacto de segmentos de path, anclado a la izquierda
dual_memory_search(query="auth flow", where={"path_prefix": "src/supamem/retrieval"})

# OR sobre múltiples prefijos (Qdrant MatchAny)
dual_memory_search(
    query="rate limit",
    where={"path_prefix": ["src/supamem", "tests/test_filtered_dense.py"]},
)

# 2. valid_to: "now" — alias no-op de la cláusula temporal always-on (Phase 9)
dual_memory_search(query="session", where={"valid_to": "now"})
```

Semántica:

- **`path_prefix`** está anclado a la izquierda en bordes de segmento `/`. El indexer
  guarda `payload.path_prefixes: list[str]` por chunk (p. ej.
  `src/supamem/retrieval/filters.py` →
  `["src", "src/supamem", "src/supamem/retrieval", "src/supamem/retrieval/filters.py"]`).
  `path_prefix="src/supa"` **no** matchea `src/supamem/...` porque `"src/supa"` no es un
  segmento prefijo almacenado — solo bordes completos de segmento `/` matchean (espeja
  la semántica de paths del filesystem).
- **`valid_to: "now"`** se acepta como alias no-op documentando la cláusula temporal
  always-on de Phase 9. Cualquier otro valor lanza `ValueError` — las consultas con
  time-travel quedan fuera de scope. Usá `retention_days` para controlar qué chunks
  históricos permanecen en la colección.

Múltiples keys de `where` se AND-ean; los valores en lista dentro de una key se OR-ean
(`MatchAny`).

| Key | Semántica |
|-----|-----------|
| `room` | Phase 7 — facet del clasificador de coding-path (`backend`, `frontend`, `tests`, ...). String o lista. Lo escribe `supamem index` por chunk. |
| `path_prefix` | Phase 11 — match exacto left-anchored por segmentos de path contra `payload.path_prefixes`. String o lista. Lo escribe `supamem index` por chunk. |
| `valid_to` | Phase 9 — solo acepta `"now"` como alias no-op de la cláusula temporal always-on. Cualquier otro valor lanza `ValueError`. |
| `session_id` | **Solo bench** — lo escribe la ingestión LongMemEval (`supamem.eval.longmemeval_ingest`); es key pass-through. **`supamem index` NO lo escribe.** Lo usa la pasada scoped del bench Phase 14 contra la colección dedicada `supamem_eval_longmemeval_s`. Ver [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md). |
| `repo` | **Solo bench** (v0.3.0a5+) — lo escribe la ingestión `coderag` (`supamem.eval.coderag.ingest`); key pass-through. Valores: `"supamem"`, `"fastapi"`. **`supamem index` NO lo escribe.** Lo usa el reporte de tres columnas (`supamem_only` / `fastapi_only` / `combined`) de Phase 15 contra `supamem_eval_coderag`. Ver [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md). |
| `axis` | **Solo bench** (v0.3.0a5+) — lo escribe la ingestión `coderag`; key pass-through. Valores: `"code_fact"`, `"decision_rationale"`. **`supamem index` NO lo escribe.** Lo usa la agregación de métricas por eje. Ver [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md). |

### coderag (recuperación de código; Phase 15 — nuevo gate de publicación de Phase 13, v0.3.0a5+)

`supamem eval --suite coderag [--full] [--out PATH] [--peer mem0]` ejecuta
un haystack determinista de dos repositorios (supamem self +
[fastapi](https://github.com/fastapi/fastapi) externo, ambos anclados a
commit-SHAs) con consultas auto-generadas a partir del historial de PRs
(eje `code_fact`) y de las secciones Problem/Why de los ADRs (eje
`decision_rationale`; **supamem-only** en el pin v1 — fastapi no tiene
`docs/adr/` en ese SHA, así que el reporte de tres columnas colapsa en
ese eje: `fastapi_only=null`, `combined=supamem_only`).

Reporta `Recall@k`, `MRR`, `nDCG@10` y latencia p50/p95 en **forma de
tres columnas** — `supamem_only` / `fastapi_only` / `combined` — por
eje, haciendo auditable la circularidad de auto-referencia.

**Gate de publicación.** Phase 13 publica cuando
`supamem eval --suite coderag --full` reporta no-regresión vs el
baseline medido (ranking ≥ baseline − ε; latencia p95 ≤ baseline + ε
**Y** ≤ 500 ms techo duro). Los pisos numéricos congelados están en
[ADR-0002](docs/adr/0002-coderag-eval-philosophy.md) §7.

**Baseline mem0 peer.** [mem0](https://github.com/mem0ai/mem0) corre
como fila paralela con una única configuración por defecto (sin
matriz de tuning), ingesta los documentos en su PROPIA colección
Qdrant `supamem_eval_coderag_mem0` (separada de `supamem_eval_coderag`
— mem0 posee su esquema). Punto de referencia, nunca gate. Instalar
con `pip install supamem[peers-mem0]`.

**LongMemEval degradado.** Desde v0.3.0a5 LongMemEval_S completo es
on-demand-only; el fixture de 5 preguntas `longmemeval_scoped_smoke`
sigue en PR-CI. Diagnóstico: LongMemEval mide memoria de largo plazo
conversacional, mientras supamem indexa chunks de código consumidos
por agents de coding — el gate estaba **mal alineado con la carga de
trabajo**, no la herramienta. Ver
[ADR-0002](docs/adr/0002-coderag-eval-philosophy.md).

### Migración

Los chunks legados (indexados antes de v0.3.0a3) no tienen `path_prefixes`. El primer
`supamem index` post-upgrade corre una pasada one-shot de scroll y `set_payload` que
back-fillea `path_prefixes` por chunk — pure metadata update, **cero costo de
re-embedding**, idempotente en runs subsiguientes. **No** se requiere `--force` reindex.

### Panel doctor

`supamem doctor` agrega un panel "Filtered-dense backend" que muestra el `preview_chars`
resuelto con la línea de procedencia `[source: ...]`. Read-only por construcción; nunca
voltea el exit code del doctor.

---

## 📊 Benchmarks (v0.3.0a4+)

**Cambio metodológico.** `supamem eval --suite longmemeval_s` emite tanto una
pasada **unscoped** como una **scoped** por pregunta. La pasada scoped usa
un `where` filter por pregunta derivado de los session ids del haystack de
LongMemEval (`{"session_id": [...]}`), ejercitando los payloads de filtro
del lado del indexer (`room`, `path_prefix`, `valid_to`, `session_id`)
agregados a lo largo de las Phases 7 / 9 / 11 / 14. La decisión del gate
publicado (delta de `tokens_per_correct_answer` vs el baseline v0.1.5) lee
la pasada **scoped**; unscoped se reporta en el mismo envelope para
transparencia y nunca gating. Ver [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md)
para el racional completo.

**Caveat de reproducibilidad.** Los números scoped pueden no reproducirse
en invocaciones unscoped por defecto de `dual_memory_search` /
`qdrant_find`. Los usuarios que quieran números comparables deben pasar un
`where={...}` explícito contra una colección cuyos chunks lleven el payload
correspondiente — esta es una disclosure metodológica, no un defecto.

**Corpus baseline.** El baseline v0.1.5 fue **re-capturado** contra una
colección de bench dedicada (`supamem_eval_longmemeval_s`). Los números
absolutos pre-Phase-14 no son directamente comparables a los números
post-Phase-14 — el corpus cambió. El número original de la devdocs
collection se preserva como `legacy_devdocs_unscoped_tpca` en
`eval/baselines/v0.1.5.json` para referencia histórica pero **NO** entra
al gate.

**FUTURE-24 (rerank composition rework)** es un sibling unblocker
trackeado por separado. La pasada scoped de Phase 14 corre con rerank-OFF
para que el delta scoped-vs-unscoped medido atribuya limpiamente al
scoping. Las claims públicas sobre las ganancias de scoping **no** se
extrapolan a "y una vez que el rerank composition también se arregle, el
gate cerrará por X% más".

**Smoke fixture.** Un fixture empaquetado en
`src/supamem/eval/datasets/longmemeval_scoped_smoke.json` (≤5 preguntas,
≤200 KB, self-contained) está expuesto como el nuevo suite
`longmemeval_scoped_smoke` — corre en CI sin disparar la descarga
perezosa de ~3 GB.

---

## 🚫 Lo que supamem **NO** hace

`supamem` **NO** auto-inyecta contexto de identity / wake-up / prelude en las llamadas
del agente — el retrieval siempre se solicita vía una query explícita. No hay un tier
oculto de "identidad del agente", ni un payload de wake-up que empuje contexto ambient
al modelo en SessionStart, ni un MCP tool que dispare retrieval cuando la `query` está
vacía.

Esto está bloqueado por dos lados:

1. **Nivel schema (v0.3.0a3+):** el parámetro `query` de cada tool de retrieval es
   `Field(..., min_length=1, max_length=...)` — requerido, no vacío, enforzado por
   schema en el momento del tool registration. Una `query` vacía se rechaza con un
   error de validación MCP estructurado, jamás se sustituye silenciosamente con
   contexto por default.
2. **Nivel test (FILT-02):** `tests/test_no_identity_tier.py` es un test de regresión
   enforzado por CI que falla la build si algún MCP tool registrado matchea
   `(?i)(wake[_-]?up|identity|prelude|inject)` O si algún tool de retrieval pierde
   `query` de su `required` / pierde `minLength >= 1` en su JSON Schema.

Si querés contexto de supamem cargado en session-open, el hook de banner de SessionStart
existente es la superficie soportada — inyecta una sola línea de estado (collection,
conteo de chunks, path del audit-log), nunca tira resultados de retrieval al modelo de
forma silenciosa. El modelo todavía tiene que llamar a `dual_memory_search` para leer
el corpus.

---

## 🪛 Cableando a tu cliente

<details>
<summary><b>Claude Code</b></summary>

```bash
supamem install --client claude-code
```

Agrega una entrada en `~/.claude.json` bajo `mcpServers` y registra un hook session-start en
`~/.claude/hooks/`. Previsualiza sin aplicar con `--dry-run`.

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
supamem install --client cursor
```

Patchea `.cursor/mcp.json` y escribe `.cursor/rules/dual-memory.mdc`.

</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
supamem install --client opencode
```

Actualiza `~/.config/opencode/opencode.json` y escribe un hook session-start en
`~/.config/opencode/hooks/`.

</details>

> ✨ **v0.2.0 — instalación multi-proyecto (default cambia a per-workspace).** `supamem install` ahora escribe por defecto en `<repo>/.mcp.json` (project scope de Claude Code) y `<repo>/.cursor/mcp.json` (per-workspace de Cursor), inyectando `SUPAMEM_PROJECT_ROOT` automáticamente. Para migrar desde un install global legacy: ejecuta `supamem repair` desde cada workspace habilitado con supamem — limpia entradas globales obsoletas y reinstala en project scope.
>
> Gate de búsqueda obligatorio (opt-in, solo Claude Code): `supamem install --client claude-code --enforce-search` registra un PreToolUse gate que DENIEGA `Edit/Write/MultiEdit` cuando no hay `mcp__supamem__dual_memory_search` en el turno actual del usuario. Bypass por sesión: `SUPAMEM_GATE_DISABLE=1`. La API de hooks de Cursor no admite todavía un evento fail-closed pre-edit — en su lugar inyectamos un `agentMessage` advisory vía `beforeSubmitPrompt`; desactiva con `SUPAMEM_ADVISORY_DISABLE=1`.
>
> El banner SessionStart ahora lleva un indicador de salud de 1 carácter (`✓` / `⚠`) y añade `update v0.X.Y available` cuando el daemon local de update-check tiene una versión nueva en caché.

> 🛟 **¿MCP lanzado desde el cwd equivocado?** Algunos hosts (Cursor, ciertos wrappers de IDE) lanzan el subproceso MCP desde `$HOME` en lugar del workspace, lo que hace que supamem caiga a la collection por defecto (`dev_memory_tuned_hybrid`) y devuelva 404 de Qdrant.
> Define `SUPAMEM_PROJECT_ROOT=/abs/path/to/workspace` en la configuración MCP del host (por ejemplo el bloque `env` de `~/.cursor/mcp.json`, o `~/.claude.json` bajo `mcpServers.supamem.env`).
> Si no está definida, supamem recorrerá los directorios padre buscando `.supamem/config.toml` o `pyproject.toml` con `[tool.supamem]` — y emitirá una advertencia de una línea en stderr si no encuentra ninguno.
> Verifícalo con `supamem doctor` desde la raíz del repo: la collection resuelta debe coincidir con la que devuelve `dual_memory_search` desde tu cliente MCP.

---

## 🧠 Cómo funciona

```text
┌─────────────────┐    MCP/stdio     ┌─────────────────┐    REST    ┌─────────────┐
│ Claude / Cursor │ ───────────────► │  supamem MCP    │ ─────────► │   Qdrant    │
│   / OpenCode    │ ◄─────────────── │     server      │ ◄───────── │  (vectores) │
└─────────────────┘                  └─────────────────┘            └─────────────┘
        │                                    ▲
        │ hook session-start                 │ retrieval tuned-hybrid
        ▼                                    │ (BM25 + MiniLM fusión)
┌─────────────────┐                          │
│ supamem hook    │ ─────────────────────────┘
│  (auto-recall)  │
└─────────────────┘
```

- **Indexer** chunkea Markdown por header (T-1 chunker, target 200-token / soft max 250)
- **Embedders** producen vectores sparse (BM25) y dense (MiniLM-L6)
- **Retrieval** corre ambos brazos en paralelo, fusiona con reciprocal rank fusion, retorna top-k
- **Servidor MCP** expone `dual_memory_search` (lectura) y `dual_memory_write` (escritura/persistencia
  idempotente de memoria del agente) — más `qdrant_find` y `qdrant_store` como aliases drop-in para
  usuarios que vengan del upstream `mcp-server-qdrant` (deshabilitar con `SUPAMEM_QDRANT_ALIASES=0`)
- **Hooks** llaman `supamem hook <client>` en el momento correcto, así la memoria carga transparentemente

---

## 🤝 Contribuir

¡Bienvenidos los PRs! Inicio rápido:

```bash
git clone https://github.com/dzmitrys-dev/supamem.git
cd supamem
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```

¿Vienes de un setup `dev_memory` in-tree? Ver [MIGRATION.md](MIGRATION.md).

---

## 📜 Licencia

MIT — ver [LICENSE](LICENSE).

---

<div align="center">

### 💜 Entregado con cariño por

<a href="https://app.softchat.ru"><b>SoftChat</b></a> &nbsp;·&nbsp; <a href="https://softskillz.ai"><b>SoftSkillz</b></a>

*Plataforma de chat con IA en ruso &nbsp;·&nbsp; Ingeniería de productos AI-first*

`supamem` fue extraído del stack de memoria productivo de SoftChat para que cada equipo pueda correr
sobre el mismo pipeline probado en batalla. Si hace tus agentes más inteligentes, danos una ⭐ —
y mira lo que construimos con él.

<sub>Hecho con cariño en Bielorrusia &nbsp;🇧🇾&nbsp; · &nbsp;<a href="https://app.softchat.ru">app.softchat.ru</a> &nbsp;·&nbsp; <a href="https://softskillz.ai">softskillz.ai</a></sub>

</div>
