# Project Plan

## Objective

Crear un script Python sencillo y robusto que lea archivos TXT desde la línea de comandos y muestre su contenido por consola, con manejo de errores apropiado.

## Architecture

```
┌─────────────────────────────────┐
│         leer_txt.py             │
├─────────────────────────────────┤
│  1. Validación de argumentos     │
│     (sys.argv)                  │
│  2. Apertura de archivo          │
│     (encoding UTF-8)            │
│  3. Lectura y salida            │
│     (print)                     │
│  4. Manejo de excepciones       │
│     (3 tipos de error)          │
└─────────────────────────────────┘
```

## Modules

| Componente | Responsabilidad |
|------------|-----------------|
| `main()` | Control de flujo, validación de argumentos, código de salida |
| `open()` con `with` | Apertura segura con cierre automático |
| Try/except blocks | Manejo de FileNotFoundError, UnicodeDecodeError, PermissionError |

## Implementation Steps

1. **Crear archivo `leer_txt.py`** con la estructura del código aprobado
2. **Probar con archivo existente** → verificar salida correcta
3. **Probar con archivo inexistente** → verificar mensaje de FileNotFoundError
4. **Probar con archivo con encoding diferente** → verificar mensaje de UnicodeDecodeError
5. **Probar sin permisos de lectura** → verificar mensaje de PermissionError
6. **Documentar uso** en comentarios o README

## Risks

| Riesgo | Probabilidad | Mitigación |
|--------|--------------|------------|
| Archivos muy grandes (memoria) | Baja | Aceptado para caso "sencillo" |
| Encoding no UTF-8 | Media | Mensaje de error claro ya implementado |
| Argumentos faltantes | Media | Validación en `main()` con mensaje de uso |

## Timeline

| Paso | Tiempo estimado |
|------|-----------------|
| Creación del script | 5 min |
| Pruebas (4 escenarios) | 10 min |
| Documentación | 5 min |
| **Total** | **20 min** |