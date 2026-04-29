**Idiomas:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ 17db78c -->

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

## 🚀 Funcionalidades

| Funcionalidad | Descripción |
|---|---|
| 🔍 **Retrieval híbrido** | Fusión sparse (BM25) + dense (MiniLM) afinada, schema fijo D-25 |
| 📚 **Chunker Markdown** | Consciente de headers, chunks de 200-token con tope suave de 250-token (T-1) |
| 🤖 **Servidor MCP** | Transportes `stdio` (default) y `http`, SDK oficial `mcp` |
| 🪝 **Hooks multi-cliente** | session-start de Claude Code, session-start de OpenCode, MDC de Cursor |
| 🧰 **Instalación de un comando** | Patcheo atómico de config con auto-backup y rollback |
| 🩺 **`supamem doctor`** | Probar Qdrant, resolver cadena de config, exponer drift de versión |
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

> **Última versión:** `v0.1.3` está publicado en [PyPI](https://pypi.org/project/supamem/). Lanzado vía
> Trusted Publisher OIDC — cada wheel tiene atestación de procedencia.

---

## 🎯 Superficie CLI

| Comando | Propósito |
|---|---|
| `supamem init` | Bootstrap greenfield — prueba Qdrant, crea colección, escribe `.supamem/config.toml` |
| `supamem install --client <name>` | Patchear config de cliente (`claude-code`, `cursor`, `opencode`) — atómico con backup |
| `supamem index` | Embeber memorias de dev en Qdrant usando el pipeline tuned-hybrid fijo (D-25) |
| `supamem mcp-server` | Correr el servidor MCP (`--transport stdio` default; `--transport http` para HTTP) |
| `supamem hook <client>` | Hooks de sesión/edición por cliente (llamados por el cliente) |
| `supamem doctor` | 🩺 Probar Qdrant, imprimir cadena de config resuelta, reportar drift de versión |
| `supamem stats` | Contadores Welford schema-v2 desde `.supamem/state/` |
| `supamem migrate` | Migración brownfield desde una colección `dev_memory` preexistente |
| `supamem eval` | Correr el arnés de regresión contra el corpus dorado de 33 consultas |
| `supamem uninstall --client <name>` | Revertir `supamem install` limpiamente |

Cada comando de larga ejecución muestra un **spinner en vivo** con tiempo transcurrido para que siempre
sepas que está trabajando. Usa `--help` en cualquier subcomando para detalles.

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
