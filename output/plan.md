# Project Plan: Shopify Draft Order Custom App

## Objective

Desarrollar una aplicación custom para Shopify con backend externo propio que permita a usuarios autorizados generar Draft Orders sin pasar por el checkout estándar, con gestión de productos habilitados, validación de inventario y control de visibilidad por usuario.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           ARQUITECTURA GENERAL                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌──────────────────┐      ┌──────────────────┐      ┌───────────┐ │
│   │  Shopify Store   │      │  Backend Propio  │      │  Shopify  │ │
│   │   (Frontend)     │◄────►│  (API + DB +     │◄────►│  Admin    │ │
│   │                  │      │   Auth)          │      │  API      │ │
│   └────────┬─────────┘      └────────┬─────────┘      └───────────┘ │
│            │                         │                                  │
│            │   Theme App            │                                  │
│            │   Extensions           │                                  │
│            │                         │                                  │
│   ┌────────▼─────────────────────────▼──────────────────────────────┐ │
│   │                    FLUJO DE DATOS                                │ │
│   │                                                                    │ │
│   │  Usuario logueado → Session Token → Backend valida →             │ │
│   │  Consulta permisos → Muestra opción en cart →                    │ │
│   │  Validación inventario → Crear Draft Order → URL/edición         │ │
│   └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

**Componentes técnicos:**

| Capa | Tecnología |
|------|-------------|
| Backend API | Node.js (Express/Fastify) o Python (FastAPI) |
| Base de datos | PostgreSQL |
| Autenticación | JWT + Shopify Customer Account API v2 |
| Integración Shopify | Theme App Extensions |
| Seguridad | HashiCorp Vault para credenciales |
| Despliegue | Contenedores Docker |

---

## Modules

### Módulo 1: Gestión de Usuarios Autorizados

- Registro de usuarios autorizados en DB propia
- Sincronización con clientes de Shopify (vía customer_id)
- Panel admin para gestionar autorización
- Logs de accesos y acciones

### Módulo 2: Gestión de Productos Habilitados

- Tabla de productos/variantes habilitados
- API REST para marcar productos desde panel admin
- Sync con catálogo Shopify (webhooks para cambios)
- Filtros por producto, variante o colección

### Módulo 3: Integración Cart/Basket (Theme App Extension)

- Cart Bootstrap Extension para injectar UI
- Lógica de visibilidad según usuario autorizado
- Botón/acción condicional en el carrito
- Comunicación AJAX con backend propio

### Módulo 4: Validación de Inventario

- Consulta a Shopify Inventory API antes de crear draft
- Validación de stock disponible por ubicación
- Respuesta de error si no hay inventario

### Módulo 5: Creación de Draft Orders

- Endpoint API para generar draft via Shopify Admin API
- Construcción del payload con line items del cart
- Retorno de URL de edición del draft
- Envío de email automático (configurable)

### Módulo 6: Seguridad y Autenticación

- JWT con expiry corto (15 min)
- Rate limiting en endpoints
- IP allowlist (opcional)
- Almacenamiento de credenciales en Vault
- Scopes mínimos de API: read_products, write_draft_orders, read_inventory, read_locations

---

## Implementation Steps

### Fase 1: Fundación (Semanas 1-2)

- [ ] Configurar entorno de desarrollo
- [ ] Crear cuenta de Partner Shopify y app de desarrollo
- [ ] Configurar backend (Node.js/Python) con PostgreSQL
- [ ] Implementar autenticación JWT básica
- [ ] Configurar Vault para almacenamiento de credenciales Shopify
- [ ] Registrar app en Shopify y obtener access token

### Fase 2: Integración Shopify Core (Semanas 3-4)

- [ ] Desarrollar Theme App Extension para cart
- [ ] Implementar Customer Account API v2 para identificación
- [ ] Crear flujo de validación de sesión de usuario
- [ ] Implementar comunicación frontend-backend segura
- [ ] Testing de integración storefront

### Fase 3: Gestión de Datos (Semanas 5-6)

- [ ] Crear schema de DB para usuarios autorizados
- [ ] Crear schema de DB para productos habilitados
- [ ] Implementar webhooks de Shopify para sync de productos
- [ ] Desarrollar panel admin básico para gestión
- [ ] Implementar logs y trazabilidad de acciones

### Fase 4: Lógica de Negocio (Semanas 7-8)

- [ ] Implementar validación de inventario
- [ ] Desarrollar endpoint de creación de Draft Orders
- [ ] Configurar manejo de errores y respuestas
- [ ] Implementar flujo post-draft (URL directa/email)
- [ ] Pruebas end-to-end del flujo completo

### Fase 5: Seguridad y Hardening (Semanas 9-10)

- [ ] Implementar rate limiting
- [ ] Configurar HTTPS y headers de seguridad
- [ ] Revisión de auditoría de seguridad
- [ ] Tests de penetración básicos
- [ ] Documentación técnica

### Fase 6: Despliegue y Entrega (Semanas 11-12)

- [ ] Desplegar backend en producción
- [ ] Instalar app en tienda Shopify de producción
- [ ] Configurar variables de entorno producción
- [ ] Testing de usuario final
- [ ] Documentación para el cliente
- [ ] Transferencia y training

---

## Risks

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Cambios en APIs de Shopify | Media | Alto | Versionar integración, monitorear changelogs |
| Rate limits de Shopify API | Media | Medio | Implementar caché y colas de procesamiento |
| Problemas de rendimiento | Baja | Medio | Optimizar queries, implementar caché Redis |
| Seguridad de credenciales | Alta | Crítico | Usar Vault, rotación de tokens, scopes mínimos |
| Sync de productos fallido | Media | Medio | Reintentos automáticos, alertas de monitoreo |
| Usuario no identificado correctamente | Baja | Alto | Múltiples métodos de validación, logs detallados |
| Inventario desactualizado | Media | Medio | Webhooks en tiempo real, fallback a polling |

---

## Timeline

```
Semana:   1    2    3    4    5    6    7    8    9   10   11   12
          │    │    │    │    │    │    │    │    │    │    │    │
FASE 1    ████████████████
FASE 2              █████████████████
FASE 3                          █████████████████
FASE 4                                    █████████████████
FASE 5                                              ██████████████
FASE 6                                                        ████████████████

Entrega: Semana 12
```

**Duración total estimada: 12 semanas**

**Hitos clave:**
- Semana 4: Integración storefront funcional
- Semana 8: Flujo completo operativo
- Semana 12: Entrega en producción

---

## Notas Adicionales

- El timeline asume equipo de 2 desarrolladores senior
-scope adicional puede requerir ajustes en timeline
- Se recomienda fase de mantenimiento post-lanzamiento (horas adicionales)
- Documentación de API будет entregada junto con el código