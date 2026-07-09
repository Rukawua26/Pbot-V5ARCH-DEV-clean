# VPS Checklist - Sniper AI v118

> Guía de despliegue en VPS para modo PAPER/SHADOW.
> No operar en REAL sin completar el runbook operativo (`ops/runbook.md`) primero.

---

## 1. Prerequisitos del VPS

| item | Minimo | Recomendado |
| :--- | :--- | :--- |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| RAM | 2 GB | 4 GB |
| Disco | 20 GB SSD | 40 GB SSD |
| Python | 3.12 | 3.12 |
| Docker | 24.0+ | 26.0+ |
| Swap | 2 GB | 4 GB |

### Verificar swap
```bash
swapon --show
# Si no hay swap, crear:
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

---

## 2. Variables .env obligatorias

```env
# === MODO ===
PAPER_MODE=true
ALLOW_REAL_TRADING=false

# === SHADOW ===
SHADOW_VALIDATION_ENABLED=true
SHADOW_VALIDATION_CAMPAIGN=shadow_macro_fvg_consensus_v1

# === DASHBOARD ===
SNIPER_API_KEY=<minimo 32 chars aleatorios>
SNIPER_CONTROL_API_KEY=<minimo 32 chars aleatorios>
SNIPER_DASHBOARD_HOST=127.0.0.1
SNIPER_DASHBOARD_AUTOSTART=true

# === TELEGRAM (opcional pero recomendado) ===
TELEGRAM_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>

# === RIESGO ===
DAILY_LOSS_LIMIT=2.0
MAX_ENTRY_SL_PCT=3.50
SHOCK_MIN_DIST_PCT=0.18

# === WS RECONCILE ===
WS_RECONCILE_TIMEOUT_SECONDS=30.0
WS_RECONCILE_MIN_INTERVAL_SECONDS=30.0

# === TELEMETRÍA ===
SNIPER_DISABLE_FILE_TELEMETRY=0
EXECUTION_EVENTS_MAX_BYTES=5242880
EXECUTION_EVENTS_BACKUPS=3
```

### Generar API keys seguras
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Despliegue con Docker

### Construir imagen
```bash
docker build -t sniper-ai .
```

### Ejecutar con restart automatico
```bash
docker run -d \
  --name sniper-bot \
  --restart=unless-stopped \
  --env-file .env \
  -v sniper-data:/app/data \
  -v sniper-logs:/app/logs \
  -v sniper-models:/app/models \
  -p 127.0.0.1:8000:8000 \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --memory=2g \
  --memory-swap=4g \
  sniper-ai
```

### Parametros explicados
| Parametro | proposito |
| :--- | :--- |
| `--restart=unless-stopped` | Reinicia el bot si crashea, pero no si lo paras manualmente. |
| `--env-file .env` | Carga variables sin exponerlas en el comando. |
| `-v sniper-data:/app/data` | Persiste DB entre reinicios. |
| `-v sniper-logs:/app/logs` | Persiste logs entre reinicios. |
| `-v sniper-models:/app/models` | Persiste modelos ML entre reinicios. |
| `-p 127.0.0.1:8000:8000` | Solo localhost; no exponer dashboard a internet. |
| `--log-opt max-size=10m` | Limita logs Docker a 10 MB por archivo. |
| `--log-opt max-file=3` | Mantiene maximo 3 archivos de log rotativos. |
| `--memory=2g` | Limita RAM del contenedor. |
| `--memory-swap=4g` | Permite 2 GB de swap adicional. |

---

## 4. Healthcheck

### Verificar salud del bot
```bash
curl -s http://127.0.0.1:8000/api/v1/health | python3 -m json.tool
```

### Respuesta esperada (healthy)
```json
{
    "status": "healthy",
    "reason": "OK",
    "state_age_s": 1.2,
    "ws_reconciliation_in_progress": false,
    "halt_system_active": false,
    "circuit_breaker_active": false,
    "is_paused": false,
    "timestamp": 1234567890.12
}
```

### Estados y acciones
| status | significance | accion |
| :--- | :--- | :--- |
| `healthy` | Snapshot fresco, sin protecciones activas. | Nada. |
| `degraded` | Snapshot > 3s o protection flag activo. | Revisar dashboard y logs. |
| `unhealthy` | Snapshot > 10s, corrupto o inexistente. | Reiniciar contenedor. |

### Healthcheck en Dockerfile
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/api/v1/health || exit 1
```

---

## 5. Logs y rotacion

### Verificar tamaño de logs
```bash
docker logs sniper-bot --tail 50
du -sh /var/lib/docker/containers/*/ 2>/dev/null | sort -rh | head -5
```

### Limpiar logs Docker si crecen demasiado
```bash
truncate -s 0 $(docker inspect --format='{{.LogPath}}' sniper-bot)
```

### Rotacion automatica (docker-compose)
```yaml
services:
  sniper-bot:
    image: sniper-ai
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

---

## 6. Monitoreo continuo

### Cron job para verificar salud cada 5 min
```bash
# /etc/cron.d/sniper-health
*/5 * * * * root curl -sf http://127.0.0.1:8000/api/v1/health > /dev/null || docker restart sniper-bot
```

### Verificar espacio en disco
```bash
df -h /
# Si disco > 80%, limpiar logs viejos:
find /var/lib/docker/containers/ -name "*.log" -mtime +7 -delete
```

---

## 7. Backup

### Backup diario de la DB
```bash
# /etc/cron.d/sniper-backup
0 3 * * * root docker exec sniper-bot python -c "
import shutil, datetime, os
src = '/app/data/brain.db'
dst = f'/app/backups/brain_{datetime.date.today().isoformat()}.db'
os.makedirs('/app/backups', exist_ok=True)
shutil.copy2(src, dst)
print(f'Backup: {dst}')
" >> /var/log/sniper-backup.log 2>&1
```

---

## 8. Actualizaciones

### Actualizar el bot
```bash
git pull origin master
docker build -t sniper-ai .
docker stop sniper-bot
docker rm sniper-bot
docker run -d --name sniper-bot \
  --restart=unless-stopped \
  --env-file .env \
  -v sniper-data:/app/data \
  -v sniper-logs:/app/logs \
  -v sniper-models:/app/models \
  -p 127.0.0.1:8000:8000 \
  --log-opt max-size=10m --log-opt max-file=3 \
  --memory=2g --memory-swap=4g \
  sniper-ai
```

### Verificar despues de actualizar
```bash
curl -s http://127.0.0.1:8000/api/v1/health | python3 -m json.tool
docker logs sniper-bot --tail 20
```

---

## 9. Troubleshooting rapido

| Sintoma | Causa probable | Solucion |
| :--- | :--- | :--- |
| Dashboard no responde | Bot crasheo o puerto ocupado | `docker restart sniper-bot` |
| `status: unhealthy` | Snapshot stale > 10s | Revisar logs del bot |
| `status: degraded` | HALT o circuit breaker activo | Ver `ops/runbook.md` seccion 2 |
| Logs crecen rapido | Rotacion no configurada | Añadir `--log-opt max-size=10m` |
| DB corrupta | Disco lleno o crash | Restaurar de backup |
| Bot no reconecta WS | Red VPS hacia Binance caida | `ping api.binance.com` |

---

_Ultima actualizacion: 2026-07-08_
_Version checklist: v1_
