# Contrato MQTT — HorangoTango Park

- **Versão:** v1 — **congelado em 27/10/2026** (CP12, entrega completa)
- **Anterior:** v0 (CP12, entrega parcial de 20/10/2026)

Qualquer mudança depois do congelamento gera nova versão (v2, ...)
registrada neste arquivo, com o campo `v` dos payloads incrementado.

## Convenções

- Prefixo de todos os tópicos: `garagem/v1/{id}/`, onde `{id}` é o
  `deviceId` (ex.: `esp32-garagem-240ac4000110`, derivado do MAC).
- Payload em JSON UTF-8. Todo payload tem `v` (versão do contrato) e
  `ts` (ISO 8601 UTC). Mensagens publicadas pelo dispositivo têm
  também `deviceId`.
- Sem hora sincronizada (NTP indisponível), o dispositivo envia
  `"ts": null, "ts_sincronizado": false, "uptime_ms": <ms desde o boot>`.
- Grandezas com unidade vão como `{ "valor": <número>, "unidade": "<un>" }`.
- O backend é o único publicador de `cmd`.

## Árvore de tópicos

| Tópico | Direção | Conteúdo | QoS | Retain | Motivo |
|---|---|---|:-:|:-:|---|
| `garagem/v1/{id}/telemetria` | ESP32 → | distância de cada vaga (cm), RSSI, uptime — a cada 10 s | 0 | não | Periódica; perder uma amostra não importa. |
| `garagem/v1/{id}/vaga/{n}/estado` | ESP32 → | `livre` / `ocupada` / `reservada` / `erro` | 1 | sim | Mudança de estado não pode se perder; novo assinante recebe o atual. |
| `garagem/v1/{id}/rfid` | ESP32 → | UID lido e vaga | 1 | não | Evento: não deve ser reentregue como se fosse novo. |
| `garagem/v1/{id}/cmd` | backend → | `reservar`, `cancelar_reserva`, `configurar` | 1 | não | Comando antigo não pode ser executado ao reconectar. |
| `garagem/v1/{id}/ack` | ESP32 → | `aceito` / `rejeitado` / `executado` + `commandId` | 1 | não | Cada confirmação é um evento único. |
| `garagem/v1/{id}/status` | ESP32 / LWT → | `online` / `offline`, versão do firmware | 1 | sim | Last Will; a interface sabe se o dispositivo está vivo. |

## Payloads

### `telemetria`

```json
{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:00Z",
  "vagas": [
    { "vaga": 1, "distancia": { "valor": 12.4, "unidade": "cm" } },
    { "vaga": 2, "distancia": { "valor": 182.0, "unidade": "cm" } }
  ],
  "rssi": { "valor": -61, "unidade": "dBm" },
  "uptime": { "valor": 384210, "unidade": "ms" } }
```

Leitura inválida de uma vaga: `"distancia": null`.

### `vaga/{n}/estado`

```json
{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:05Z",
  "vaga": 1, "estado": "ocupada", "distancia": { "valor": 12.4, "unidade": "cm" } }
```

`estado`: `livre`, `ocupada`, `reservada` ou `erro` (5 leituras
inválidas seguidas; `distancia` vai `null`).

### `rfid`

```json
{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:07Z",
  "vaga": 1, "uid": "04A1B2C3" }
```

`uid` em hexadecimal maiúsculo, sem separadores.

### `cmd`

```json
{ "v": 1, "commandId": "c-0042", "ts": "2026-10-27T21:31:00Z",
  "acao": "reservar", "vaga": 1, "duracao_min": 15 }
```

### `ack`

```json
{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:31:01Z",
  "commandId": "c-0042", "resultado": "rejeitado", "erro": "VAGA_OCUPADA" }
```

Para cada comando o dispositivo publica `aceito` (ou `rejeitado` com
`erro`) e, depois de agir no atuador, `executado`. O backend marca o
comando como **confirmado** quando recebe o `vaga/{n}/estado`
correspondente (ex.: `reservada`).

### `status`

```json
{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:29:50Z",
  "status": "online", "firmware": "1.0.0", "vagas": [1, 2] }
```

Last Will (registrado na conexão, retained):
`{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "status": "offline" }`.

## Comandos aceitos e faixas

| `acao` | Campos | Faixas |
|---|---|---|
| `reservar` | `vaga`, `duracao_min` | `vaga` 1–2; `duracao_min` 1–60 |
| `cancelar_reserva` | `vaga` | `vaga` 1–2 |
| `configurar` | `limiar_ocupa_cm`, `limiar_libera_cm`, `duracao_reserva_min` | `limiar_ocupa_cm` 2–200; `limiar_libera_cm` > `limiar_ocupa_cm` + 2; `duracao_reserva_min` 1–60 |

A configuração é gravada em NVS e sobrevive à reinicialização.

**Intertravamento:** `reservar` só é aceito com a vaga `livre`.

## Códigos de erro

| Código | Quando |
|---|---|
| `VAGA_OCUPADA` | `reservar` em vaga ocupada. |
| `VAGA_EM_ERRO` | `reservar` em vaga com sensor em falha. |
| `VAGA_INEXISTENTE` | `vaga` fora de 1–2. |
| `SEM_RESERVA` | `cancelar_reserva` em vaga sem reserva. |
| `PARAMETRO_INVALIDO` | Campo fora da faixa ou ausente. |
| `ACAO_DESCONHECIDA` | `acao` não listada acima. |
| `COMANDO_DUPLICADO` | `commandId` já processado (não reexecuta). |
| `PAYLOAD_INVALIDO` | JSON malformado ou `v` diferente de 1. |
