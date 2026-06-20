# Runbook: Chaos Validation Matrix

Este runbook valida si el runtime se degrada de forma segura ante ambigüedad y fallos de exchange.

## Escenarios cubiertos

1. `create_ack_timeout_recovered_by_client_id`
   - Esperado: recuperación por `clientOrderId` sin duplicar exposición.

2. `ioc_ambiguous_fill_confirmed`
   - Esperado: la orden IOC ambigua se confirma como cerrada tras consulta.

3. `chase_limit_hard_floor_stuck`
   - Esperado: `exit_state=STUCK`, nunca falso positivo de cierre.

4. `no_price_market_exit_escalation`
   - Esperado: un único market exit tras superar el umbral de no-price.

5. `concurrent_timeout_restore`
   - Esperado: todas las llamadas concurrentes ven timeout override correcto y el exchange restaura su timeout al final.

6. `order_lookup_not_found`
   - Esperado: `OrderNotFound` resuelve a `None`, no se eleva como fallo de transporte.

7. `exchange_502_retry_recovers`
   - Esperado: un `ExchangeNotAvailable`/502 transitorio se recupera con retry acotado.

8. `rate_limit_close_retries_reduce_only`
   - Esperado: rate-limit en cierre reintenta de forma acotada y termina en orden reduce-only/cierre.

## Ejecución

```bash
./.venv/bin/python tools/chaos_matrix.py
./.venv/bin/python tools/recovery_drill.py
```

`tools/recovery_drill.py` cubre actualmente:

- adopcion de posicion huerfana solo despues de adjuntar `HARD SL`.
- fallo al adjuntar `HARD SL` termina en `HALT` y estado `ADOPTED_UNPROTECTED`.
- `fetch_positions` ambiguo durante reinicio termina en `HALT`.

## Criterio de aprobación

- `failed == 0`
- Si falla un escenario, no promocionar a `REAL`.
- Corregir la invariante rota y repetir.
