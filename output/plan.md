# Project Plan

## Objective

Desarrollar un sistema CLI de reservas de biblioteca en Python que permita a los usuarios registrarse, buscar libros por título/autor, reservar libros (máximo 3 activos), devolver libros, y ver su historial de reservas. Persistencia en archivos JSON con tolerancia a fallos.

## Architecture

```
biblioteca/
├── biblioteca.py      # Menú CLI, orquestación de flujo
├── models.py          # Dataclasses: Usuario, Libro, Reserva
├── repository.py      # Lectura/escritura JSON con escritura atómica
├── services.py        # Lógica de negocio y validaciones
├── main.py            # Entry point
└── data/
    ├── usuarios.json  # Array de usuarios
    ├── libros.json    # Array de libros (precargado con 10 de prueba)
    └── reservas.json  # Array de reservas
```

## Modules

### models.py
Dataclasses inmutables con los campos:
- **Usuario**: id (str), nombre (str), email (str), password (str)
- **Libro**: id (str), titulo (str), autor (str), isbn (str), reservado_por (str | None)
- **Reserva**: id (str), usuario_id (str), libro_id (str), fecha_reserva (str ISO 8601), fecha_devolucion (str | None)

### repository.py
- `cargar<T>(archivo)` → lista de objetos del tipo
- `guardar<T>(archivo, datos)` → escritura atómica a .tmp + rename
- `generar_id()` → `uuid.uuid4().hex[:8]`
- Auto-creación de archivos con `[]` si no existen al iniciar

### services.py
- `registrar_usuario(nombre, email, password)` → valida email único, min 4 chars password
- `autenticar(email, password)` → retorna Usuario o None
- `buscar_libros(termino)` → filtro case-insensitive en titulo y autor; "" → todos
- `reservar_libro(libro_id, usuario_id)` → valida disponible, max 3 activas, crea Reserva
- `devolver_libro(libro_id, usuario_id)` → valida propiedad, actualiza Reserva y Libro
- `obtener_historial(usuario_id)` → lista de Reservas ordenadas por fecha_reserva DESC
- `contar_reservas_activas(usuario_id)` → count donde fecha_devolucion is None

### biblioteca.py
- Variable `usuario_actual` en memoria (None o Usuario)
- Dos menús: público (sin sesión) y autenticado (con sesión)
- Manejo de input con reintentos para opciones inválidas
- Pausa post-acción exitosa (excepto login)

## Implementation Steps

### Step 1: Estructura base y modelos
**Descripción**: Crear estructura de carpetas, archivos JSON iniciales con datos de prueba, y dataclasses en models.py.
**Dependencias**: Ninguna

- Crear directorio `biblioteca/data/`
- Crear `data/libros.json` con 10 libros de prueba (IDs pre-generados: a1b2c3d4, e5f6g7h8, i9j0k1l2, m3n4o5p6, q7r8s9t0, u1v2w3x4, y5z6a7b8, c9d0e1f2, g3h4i5j6, k7l8m9n0)
- Crear `data/usuarios.json` = `[]`
- Crear `data/reservas.json` = `[]`
- Implementar dataclasses en models.py

### Step 2: Repository
**Descripción**: Implementar capa de persistencia con escritura atómica y auto-creación de archivos.
**Dependencias**: Step 1

- Implementar `cargar` con manejo de archivos faltantes (crear si no existe) y fechas malformadas (ignorar, log warning)
- Implementar `guardar` con patrón: escribir a `.tmp`, flush, fsync, rename
- Implementar `generar_id` con UUID v4 (8 hex caracteres)

### Step 3: Services
**Descripción**: Implementar toda la lógica de negocio y validaciones.
**Dependencias**: Step 1, Step 2

- `registrar_usuario`: validar nombre no vacío, email con formato válido (char@char.char), password mínimo 4 caracteres, email único
- `autenticar`: buscar por email y password, retornar usuario o None
- `buscar_libros`: filtro case-insensitive en titulo y autor; "" retorna todos
- `reservar_libro`: verificar libro existe, no reservado, usuario tiene <3 activas → crear reserva, actualizar libro.reservado_por
- `devolver_libro`: verificar libro existe, reservado_por == usuario_id → actualizar reserva.fecha_devolucion, libro.reservado_por = None
- `obtener_historial`: obtener todas reservas del usuario, ordenar por fecha_reserva DESC

### Step 4: CLI - Menú Público
**Descripción**: Implementar flujo de autenticación y navegación sin sesión.
**Dependencias**: Step 2, Step 3

Menú público:
1. Registrarse → solicitar nombre, email, password con validación inline
2. Iniciar sesión → solicitar email, password → establecer usuario_actual
3. Ver libros disponibles → mostrar todos con estado (disponible/reservado por X)
4. Salir

- Validar input de opción (número fuera de rango, letras) → "Opción no válida", reprint
- Login exitoso → mostrar "Bienvenido, {nombre}" y transicionar a menú autenticado

### Step 5: CLI - Menú Autenticado
**Descripción**: Implementar operaciones por usuario.
**Dependencias**: Step 2, Step 3, Step 4

Menú autenticado:
1. Buscar libros → término → mostrar resultados numerados → seleccionar por número (0=cancelar) → reservar
2. Reservar libro → solicitar ID o usar selección tras búsqueda
3. Devolver libro → solicitar ID de libro → validar y devolver
4. Mi historial → mostrar tabla ASCII con todas las reservas (fecha_reserva, fecha_devolucion, título, estado)
5. Cerrar sesión → usuario_actual = None, volver a menú público
6. Salir → terminar programa

- Tras acción exitosa (excepto login) → pausa "Presiona ENTER para continuar..."
- Mensajes de error específicos para cada caso de fallo

### Step 6: Main y pruebas manuales
**Descripción**: Entry point y verificación del sistema completo.
**Dependencias**: Todos los anteriores

- Implementar main.py que inicialice y ejecute el CLI
- Verificar flujo completo: registro → login → búsqueda → reserva → devolución → historial
- Verificar casos de error: libro reservado, límite 3, email duplicado, etc.

## Decisions

| Decisión | Justificación |
|----------|---------------|
| UUID v4 para IDs | Unicos sin contadores, idempotentes entre ejecuciones |
| Sesión en memoria | CLI simple, no requiere persistencia de sesión |
| Escritura atómica (.tmp + rename) | Evita corrupción si proceso se interrumpe a mitad de escritura |
| ASCII only en output | Compatibilidad total con cualquier terminal |
| Búsqueda vacía retorna todos | Comportamiento esperado de filtro "ver todo" |
| Sin baja de usuario | No estaba en requisitos originales |
| Sin validación DNS de email | Scope simple, no requiere complejidad adicional |
| ISBNs ficticios en datos de prueba | No se usa para funcionalidad, solo estética |

## Risks

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| JSON corrupto por escritura incompleta | Pérdida de datos | Escritura atómica con fsync garantiza integridad |
| Concurrencia de acceso a archivos | Datos inconsistentes | No aplica: CLI mono-usuario, un proceso a la vez |
| Terminal sin soporte ASCII extendido | Caracteres rotados | Output usa solo ASCII puro (letras, números, símbolos básicos) |
| Datos de fecha malformados en JSON | Entradas corruptas ignoradas | Log warning, sistema continúa funcionando |
| Límite de archivos abiertos (SO) | Error de persistencia | Cerrar archivos inmediatamente tras lectura/escritura |

## Timeline

**Estimación total**: 4-6 horas de desarrollo

- Step 1 (estructura): 30 min
- Step 2 (repository): 1 hora
- Step 3 (services): 1.5 horas
- Step 4 (menú público): 1 hora
- Step 5 (menú autenticado): 1.5 horas
- Step 6 (integración y pruebas): 1 hora

## Expected Outcome

Sistema funcional que permite:
- ✓ Registro con validación de email único
- ✓ Login/logout con sesión en memoria
- ✓ Búsqueda case-insensitive por título/autor
- ✓ Reserva con validación de disponibilidad y límite de 3 activas
- ✓ Devolución con validación de propiedad
- ✓ Historial ordenado por fecha
- ✓ Persistencia JSON con tolerancia a fallos
- ✓ Output compatible con cualquier terminal

El sistema puede ser ejecutado con `python main.py` y funciona sin dependencias externas (solo stdlib de Python).