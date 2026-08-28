# C-Suite — Contexto para Claude Code

Equipo ejecutivo virtual multi-agente. Backend Python (FastAPI) + frontend Next.js 15.
El "Ejecutivo" es una persona única y coherente respaldada por 8 sub-agentes especialistas.

> **Origen** — derivado de [OpenExecutive](https://github.com/SenteLabsAI/OpenExecutive)
> (Apache 2.0). Ver [NOTICE](NOTICE) para la atribución y el registro de cambios.

---

## Reglas de trabajo

| Regla | Detalle |
|---|---|
| **Sin coautoría de máquina** | Nunca añadir `Co-Authored-By: Claude` ni "Generated with Claude Code" a commits, PRs o docs. |
| **Idioma** | Código, nombres y prompts del sistema en inglés. Documentación y commits en español. |
| **Commits** | Conventional commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`. |
| **Ramas** | Nunca commitear directo a `main`. Rama por cambio: `feat/…`, `fix/…`. |
| **Sin stubs** | Solo código que funciona. Nada de `TODO` como entrega. |

### Antes de dar por terminado

1. `make lint` — `ruff` + `mypy` en verde
2. `make test` — pytest en verde
3. Si cambiaste algo documentado en `/architecture`, actualiza el JSON correspondiente (ver abajo)

---

## Comandos

```bash
make install      # uv sync (core) + npm install (ui)
make dev          # FastAPI :8000 + Next.js :3000
make stop         # matar ambos puertos
make test         # pytest
make lint         # ruff check + mypy
make eval         # suite de evals contra localhost
make docker       # docker compose up --build
```

Python usa **`uv`**, no pip ni poetry:

```bash
cd packages/core && uv sync && source .venv/bin/activate
```

---

## Estructura

```
packages/core/          Python: lógica de agentes, API, CLI
packages/ui/            Next.js 15
packages/core/company/  Datos de MI empresa — GITIGNORED, nunca commitear
evals/                  Escenarios de eval + runner con LLM como juez
docker/                 Dockerfile + docker-compose.yml
docs/                   Documentación técnica
fixtures/               Empresas de ejemplo para demos
```

---

## Cómo funciona el sistema de agentes

1. El mensaje llega al `Executive` (orquestador en `orchestrator/executive.py`)
2. El Executive usa tool-use de la API para llamar a `consult_specialist`
3. En preguntas multi-dominio, varios especialistas corren **en paralelo**
4. Cada especialista: prompt de dominio (`prompts/domain_prompts.py`) → recupera chunks de ChromaDB → devuelve análisis
5. El Executive sintetiza todo en una respuesta coherente
6. **La arquitectura interna NUNCA se expone al usuario**

---

## Prompt caching — crítico

El sistema está diseñado alrededor del caching de prompts. **Romper el caching = 10x el coste.**

> **Nunca pongas contenido dinámico en bloques del system prompt que tengan `cache_control`.**

Orden de construcción en `prompts/cache_manager.py`:

1. Definiciones de tools (ordenadas por nombre — **obligatorio** que estén ordenadas)
2. Constante de persona del Executive (`prompts/executive_persona.py` — **nunca** interpolar con f-strings)
3. Bloque de perfil de empresa (`memory/company_profile.py`)
4. Resumen del índice de conocimiento

El contexto RAG va en el **turno del usuario**, no en el system prompt.

---

## Añadir un especialista nuevo

1. Crear `packages/core/openexecutive/agents/tu_agente.py`:

   ```python
   from openexecutive.agents.base import BaseAgent

   class YourAgent(BaseAgent):
       name = "your_agent"
       domain = "your_domain"
       model = "claude-sonnet-4-6"

       def get_system_prompt(self) -> str:
           from openexecutive.prompts.domain_prompts import YOUR_AGENT_PROMPT
           return YOUR_AGENT_PROMPT
   ```

2. Añadir `YOUR_AGENT_PROMPT` a `prompts/domain_prompts.py`
3. Registrar en `orchestrator/router.py`: entrada en `SPECIALIST_REGISTRY` **y** valor en el enum de `SPECIALIST_TOOLS[0]["input_schema"]["properties"]["specialist"]["enum"]`
4. Documentos de conocimiento en `knowledge/your_domain/`
5. Evals: `evals/scenarios/your_domain_001.yaml` y `_002.yaml`
6. Si introduce un patrón nuevo (tool, ruta de routing, contrato de memoria), actualizar `architecture/architecture-facts.yaml`. Las adiciones puras a `SPECIALIST_REGISTRY` se reflejan solas.

---

## Estilo de código

- **Python** — `ruff` (lint), `mypy` (tipos), `pytest` (tests)
- **Pydantic v2** en todo: `model_config = ConfigDict(...)`, nunca `class Config`
- Llamadas a la API: siempre `anthropic.AsyncAnthropic()`
- Todos los `analyze()` de agentes son `async`
- Sin contenido dinámico en bloques cacheados del system prompt

---

## Tests

> **Gotcha local:** si `BACKEND_SHARED_SECRET` está en tu shell, los tests de app
> completa con `TestClient` devuelven `401` en vez del status esperado. Ejecuta
> con la variable desactivada para igualar CI:
> `env -u BACKEND_SHARED_SECRET uv run pytest tests/unit/`

> **Lint de UI:** `packages/ui` no tiene config de ESLint — `npm run lint` abre un
> prompt interactivo. La puerta de lint/tipos de la UI es `npm run build`.

```bash
pytest packages/core/tests/unit/ -v          # sin llamadas a la API
pytest packages/core/tests/integration/ -v   # requiere ANTHROPIC_API_KEY
cd evals && python run_evals.py --scenarios scenarios/ --output results/
```

---

## Página /architecture

Se sirve desde contenido **estático escrito a mano** en
`packages/core/openexecutive/architecture/prebuilt/<section_id>.json` — un archivo por
sección declarada en `architecture/sections.py` (`SECTIONS`). El backend
(`api/routes/architecture.py`) solo lee esos archivos: **nada en esta ruta llama a un LLM.**

`architecture/architecture-facts.yaml` es la fuente de verdad curada sobre el *porqué*
del sistema. No alimenta a ningún generador en runtime — son las notas autoritativas
que se leen al reescribir una sección.

**El fallo más común:** añadir comportamiento nuevo bajo un tema existente (p. ej. una
integración nueva) y dejar la página obsoleta en silencio. Nada lo fuerza. Trata cualquier
cambio que altere lo que una sección ya describe como actualización obligatoria.

| Cambio | Sección a actualizar |
|---|---|
| Canal de integración nuevo o comportamiento cambiado | `integrations` |
| Primitiva de workflow nueva o entrada nueva en `WORKFLOW_REGISTRY` | `workflows` |
| Cambio en el layout de caché (bloques, TTLs, qué se cachea) | `caching` |
| Invariante o guardarraíl nuevo | la sección afectada (normalmente `overview` / `agents`) |
| Patrón de routing nuevo o routing de especialistas cambiado | `agents`, `lifecycle` |
| Cambio de schema en una tabla documentada | `schemas` |
| Endpoint añadido, quitado, renombrado o con response-shape distinto | `api` + las secciones que lo nombren |
| Módulo top-level nuevo en `packages/core/openexecutive/` | `SectionSpec` en `sections.py` + entrada en `packages/ui/src/app/architecture/page.tsx` (los IDs deben coincidir) + `prebuilt/<id>.json` nuevo |

Cada `prebuilt/<id>.json` tiene las claves `section_id`, `title`, `markdown`, `mermaid`
(string o `null`) y `generated_at`. El Markdown **no** debe incluir el encabezado de la
sección (la UI renderiza el título). Validar con `python -m json.tool`.

---

## Datos de empresa y secretos

`packages/core/company/` está **gitignored**. Nunca commitear datos de empresa.
`.env` también está gitignored — usa `.env.example` como plantilla.

- `company/profile.yaml` — perfil estructurado (lo llena el wizard de onboarding)
- `company/docs/` — documentos subidos (indexados en ChromaDB)

**Variables:** ver `.env.example` (47 en total). Obligatoria: `ANTHROPIC_API_KEY`.
Todo lo demás — Slack, Discord, Telegram, Google Chat, Honcho, OpenRouter, modelos
locales — es opcional.

---

## Despliegue

Los `fly.*.toml` heredados apuntan a apps de Fly.io que **no son mías**
(`openexec-api-dev`, `openexec-ui-dev`). Antes de desplegar hay que renombrarlas:
los nombres de app en Fly son globales y chocarán.

La skill `flyctl` en `.claude/skills/` todavía referencia las apps originales —
actualízala en el mismo cambio en que renombres las apps.

---

## Skills disponibles en el repo

| Skill | Para qué |
|---|---|
| `anvil` | Workflow de código basado en evidencia con revisión adversarial. Opcional, útil en cambios grandes. |
| `flyctl` | Operar las apps de Fly (logs, SSH, secrets). **Requiere actualizar los nombres de app.** |
| `openexec-api` | Golpear los endpoints del backend con curl. |

---

## Sincronizar con el upstream

```bash
git fetch upstream
git log --oneline HEAD..upstream/main   # ver qué hay de nuevo
git merge upstream/main                 # o cherry-pick selectivo
```
