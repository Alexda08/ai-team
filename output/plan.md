# Project Plan

## Objective

Implementar un sistema de subida, listado y eliminación de imágenes que sea:
- Simple de mantener y operar
- Preparado para añadir autenticación futura
- Con límites técnicos explícitos documentados

**Límites de operación:**
- ≤5 uploads concurrentes
- 1 sola instancia
- ~10,000 archivos máximo
- 5MB por archivo

---

## Architecture

```
app/
├── main.py                     # Entry point FastAPI
├── routes/
│   └── files.py                # Endpoints REST
├── services/
│   └── file_service.py         # Lógica de negocio
├── models/
│   └── file.py                 # Esquema de respuesta
├── db/
│   └── database.py             # Conexión SQLite + WAL mode
├── utils/
│   └── validators.py           # Validación MIME real
└── storage/uploads/            # Archivos en disco
```

---

## Modules

| Módulo | Responsabilidad |
|--------|-----------------|
| `routes/files.py` | POST, GET, DELETE endpoints |
| `services/file_service.py` | Upload, list, delete con retry y atomicidad |
| `db/database.py` | SQLite con WAL, conexión pooling |
| `utils/validators.py` | Magic bytes + MIME type + sanitización |

---

## Implementation Steps

1. **Setup proyecto base**
   - Crear estructura de carpetas
   - Instalar dependencias: `fastapi`, `uvicorn`, `python-multipart`, `Pillow`

2. **Implementar validación de archivos**
   - Magic bytes verification (jpeg, png, gif, webp)
   - Sanitización de nombre de archivo y user_id
   - Límite 5MB

3. **Implementar base de datos SQLite**
   - Tabla `files`: id, user_id, name, path, size, uploaded_at
   - WAL mode para mejor concurrencia
   - Índices en user_id

4. **Implementar lógica de negocio**
   - Upload con retry exponencial (3 intentos)
   - Delete en orden: disco → DB (atomicidad)
   - List con paginación (default 50)

5. **Implementar endpoints**
   - `POST /files` - Upload con validación
   - `GET /files` - List con ?page, ?limit
   - `GET /files/{id}` - Descargar archivo
   - `DELETE /files/{id}` - Eliminar archivo

6. **Documentar BUG CONOCIDO**
   - X-User-ID header permite acceso a archivos ajenos
   - NO es "preparado para auth" - es exploit activo

---

## Risks

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Concurrencia >5 uploads | Degradación rendimiento | Documentar límite, escalar a PostgreSQL si se excede |
| Disco lleno | Upload falla | Check espacio antes de escribir |
| Auth exploit activo | Usuario ve/borra archivos ajenos | Documentado como bug conocido |
| SQLite en multi-instancia | Archivos inconsistentes | Límite a 1 instancia por ahora |

---

## Timeline

| Fase | Descripción | Estimación |
|------|-------------|------------|
| 1 | Setup + validación | 1-2 horas |
| 2 | SQLite + CRUD | 2-3 horas |
| 3 | Endpoints + testing | 2 horas |
| 4 | Documentación + deploy | 1 hora |
| **Total** | | **6-8 horas** |

---

## Known Bug (MUST READ)

```python
# ⚠️ BUG CONOCIDO - EXPLOIT ACTIVO
# Sin autenticación real, cualquier usuario puede:
# - Cambiar header X-User-ID para ver archivos de otros
# - Borrar archivos de usuarios arbitrarios
# Esto NO es una "limitación temporal" - es un exploit de seguridad
# Para producción: implementar JWT/session y remover el fallback
```