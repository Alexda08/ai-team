# Project Plan

## Objective

Desarrollar un sistema de gestión de tareas (todo-list) en Python con persistencia JSON, validación estricta, sistema de undo, CLI interactivo y tests unitarios completos.

## Architecture

```
├── task.py              # Modelo de datos Task
├── storage.py           # Capa de persistencia JSON
├── task_manager.py      # Lógica de negocio + Undo
├── cli.py               # Interfaz de línea de comandos
├── main.py              # Punto de entrada
└── tests/
    ├── __init__.py
    └── test_task_manager.py  # Suite completa de tests
```

**Patrón arquitectónico:** MVC simplificado con capa de persistencia separada. El TaskManager actúa como fachada central que orquesta todas las operaciones.

**Decisión de diseño:** Se usará un patrón Command para el sistema de undo, donde cada operación guarda su estado previo como un comando ejecutable inverso.

## Modules

| Módulo | Responsabilidad | API Pública |
|--------|-----------------|-------------|
| `task.py` | Definición de estructura Task, enum Status/Priority | `Task`, `Status`, `Priority` |
| `storage.py` | Lectura/escritura JSON, gestión de archivo | `JSONStorage.load()`, `JSONStorage.save()` |
| `task_manager.py` | CRUD, validación, filtrado, undo | `create()`, `get()`, `update()`, `delete()`, `filter()`, `undo()` |
| `cli.py` | Menú interactivo, formateo de salida | `run_cli()` |

## Implementation Steps

### Step 1: Modelo de Datos (task.py)

**Descripción:** Definir las clases `Task`, `Status` (enum: pending/in_progress/done) y `Priority` (enum: low/medium/high). Incluir validación básica en `__post_init__`.

**Dependencias:** Ninguna

```python
@dataclass
class Task:
    id: str  # UUID
    title: str
    description: str
    status: Status
    priority: Priority
    created_at: datetime
    updated_at: datetime
```

---

### Step 2: Capa de Persistencia (storage.py)

**Descripción:** Implementar clase `JSONStorage` que maneje lectura/escritura atómica del archivo JSON. Incluir métodos `load()` y `save()` con manejo de archivo inexistente/corrupto.

**Dependencias:** Step 1

**Consideraciones:** Usar escritura atómica con archivo temporal para prevenir corrupción de datos en escrituras parciales.

---

### Step 3: TaskManager - CRUD Básico (task_manager.py, Parte 1)

**Descripción:** Implementar la clase `TaskManager` con operaciones `create()`, `get(id)`, `list_all()`, `update()`, `delete()`. Cargar datos desde storage en `__init__`.

**Dependencias:** Step 1, Step 2

**Notas:** Create debe generar UUID y timestamps automáticamente. Update debe modificar solo `updated_at`.

---

### Step 4: Validación Estricta (task_manager.py, Parte 2)

**Descripción:** Agregar validación en todas las operaciones:
- `create()`: título no vacío, título único (sin duplicados por título)
- `update()`: campos válidos (status/priority en enum), título único si cambia
- `delete()`: tarea debe existir

**Dependencias:** Step 3

**Decisiones:** Lanzar excepciones personalizadas (`ValidationError`, `NotFoundError`, `DuplicateError`) para facilitar testing y mensajes de error claros.

---

### Step 5: Sistema de Filtrado (task_manager.py, Parte 3)

**Descripción:** Implementar método `filter(status=None, priority=None, search=None)` que retorne lista de tareas. `search` debe ser case-insensitive y buscar en `title` y `description`. Filtros son acumulativos (AND).

**Dependencias:** Step 4

---

### Step 6: Sistema de Undo (task_manager.py, Parte 4)

**Descripción:** Implementar pila de comandos para undo. Cada operación (create/update/delete) guarda:
- **create:** guardar tarea completa → undo la elimina
- **update:** guardar estado anterior → undo restaura
- **delete:** guardar tarea completa → undo la recrea

Método `undo()` ejecuta el inverso del último comando. Lanzar `UndoError` si no hay operaciones.

**Dependencias:** Step 4

**Decisión:** Limitar historial a 50 operaciones para evitar consumo excesivo de memoria.

---

### Step 7: CLI Interactivo (cli.py)

**Descripción:** Implementar menú con opciones numeradas:
1. Crear tarea (pedir title, description, priority)
2. Listar tareas (mostrar en tabla formateada)
3. Ver tarea por ID
4. Actualizar tarea (seleccionar campo a modificar)
5. Eliminar tarea (confirmar)
6. Filtrar tareas (submenú: status/priority/texto)
7. Deshacer última operación
8. Salir

**Dependencias:** Step 5, Step 6

**Notas:** Manejar input vacío, Ctrl+C graceful, errores con mensajes legibles.

---

### Step 8: Punto de Entrada (main.py)

**Descripción:** Script simple que ejecuta `run_cli()` desde `cli.py`.

**Dependencias:** Step 7

---

### Step 9: Tests Unitarios (tests/test_task_manager.py)

**Descripción:** Suite completa con pytest cubriendo:
- **Edge cases de validación:**
  - Crear tarea con título vacío → debe lanzar ValidationError
  - Crear tarea con título duplicado → debe lanzar DuplicateError
  - Update con status inválido → debe lanzar ValidationError
  - Delete de tarea inexistente → debe lanzar NotFoundError
- **Edge cases de undo:**
  - Undo sin operaciones previas → debe lanzar UndoError
  - Undo de delete → debe restaurar tarea completa
  - Undo multiple (create + delete)
- **Filtros combinados:**
  - Filtrar por status Y priority
  - Filtrar por status Y texto
  - Filtrar por texto que matchea solo description
  - Filtro sin resultados
- **Persistencia:**
  - Datos persisten entre instancias de TaskManager

**Dependencias:** Step 8 (puede crearse en paralelo, no depende de ejecución)

**Decisión:** Usar fixtures de pytest con tmp_path para crear archivos JSON temporales por test.

---

### Step 10: README y documentación

**Descripción:** Documentar uso del CLI, estructura del proyecto, y cómo ejecutar tests.

**Dependencias:** Step 9

## Risks

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Corrupción del archivo JSON por escritura concurrente | Baja | Alto | Escritura atómica con archivo temporal |
| Memoria excesiva por historial de undo | Baja | Medio | Limitar a 50 comandos |
| Input malicioso en CLI (inyección, bytes nulos) | Baja | Medio | Sanitizar input, usar .strip() |
| Tests lentos por I/O en cada test | Media | Bajo | Usar tmp_path, mock de storage si necesario |

## Timeline

Estimación total: **~8-10 horas** para desarrollador intermedio

| Step | Horas estimadas |
|------|-----------------|
| 1. Modelo de datos | 0.5 |
| 2. Persistencia | 1 |
| 3. CRUD básico | 1.5 |
| 4. Validación | 1 |
| 5. Filtrado | 1 |
| 6. Undo | 1.5 |
| 7. CLI | 1.5 |
| 8. main.py | 0.25 |
| 9. Tests | 2 |
| 10. README | 0.5 |
| **Total** | **~10.75** |