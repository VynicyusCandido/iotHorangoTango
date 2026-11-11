# Pendências para fechar a N3

Situação em 10/11/2026. Entrega da N3: **01/12/2026** (release `v1.0`,
README executável e defesa). Requisitos (R1–R18) e proposta em
[CP12.md](CP12.md); contrato MQTT em [CONTRATOS.md](../CONTRATOS.md).

## Já pronto (firmware e broker — Luis)

- Firmware no contrato v1: leitura das vagas, RFID, fila de eventos
  offline, LED com regra local, comandos (`reservar`,
  `cancelar_reserva`, `configurar`), reserva com LED azul e configuração
  em NVS ([ADR 0003](adr/0003-firmware-v1-fila-de-eventos.md),
  [ADR 0004](adr/0004-comandos-reserva-e-nvs.md)).
- Broker Mosquitto 2.1.2 no Docker Compose, com usuário/senha e ACL
  (usuários `dispositivo`, `backend` e `monitor`).

> **Pré-requisito:** os PRs #10 (broker) e #11 (comandos) foram
> mesclados nas branches de base, não na `main`. Falta um PR de
> `feat/broker-v1` para `main` (responsável: Luis) antes de começar.

## Gustavo — hardware, bancada e OTA

| # | Tarefa | Req. | Até |
|:-:|---|:-:|:-:|
| G1 | Montar a maquete com fiação fixa: 2 HC-SR04, 2 RC522 **sob o piso**, 2 LED RGB com o canal azul (GPIO 16 e 17), divisor no Echo ([mapa de pinos](mapa-de-pinos.md)) | R1 | 17/11 |
| G2 | Esquema elétrico (Fritzing) e BOM completa em `docs/` | R1 | 17/11 |
| G3 | Bancada do firmware: os dois RC522 com `leitor OK`; tag lida através do piso; calibrar os limiares com `configurar` e conferir após reiniciar; teste de queda; teste de reserva (roteiros no README) | R1, R2 | 17/11 |
| G4 | OTA: gravar nova versão pela rede (ArduinoOTA, senha no `secrets.h`) e conferir a versão nova no tópico `status` | R17 | 24/11 |
| G5 | Ter commit próprio no repositório (exigência da entrega) | R11 | 17/11 |

## João Pedro — backend, banco e regras

| # | Tarefa | Req. | Até |
|:-:|---|:-:|:-:|
| J1 | Simulador do ESP32 (`tools/simulador.py`): publica estado, RFID, telemetria e status no contrato v1 e responde a comandos — permite desenvolver backend e painel sem hardware | — | 13/11 |
| J2 | Backend FastAPI + aiomqtt assinando `garagem/v1/#` com o usuário `backend`; validação com Pydantic; mensagens inválidas gravadas com o motivo; horário real de eventos com `"ts": null` calculado pela telemetria (ADR 0003) | R4 | 17/11 |
| J3 | PostgreSQL 16 + Alembic: `dispositivos`, `medicoes`, `estados_vaga`, `leituras_rfid`, `comandos`, `alertas`, `usuarios`, `mensagens_invalidas` | R5 | 17/11 |
| J4 | `postgres` e `backend` no `compose.yaml`; tudo sobe com `docker compose up -d` | R12 | 17/11 |
| J5 | API REST: estado atual das vagas, histórico, dispositivos (online/offline, último contato), alertas, tags lidas; publicar a documentação OpenAPI cedo para o painel | R4, R6 | 17/11 |
| J6 | Comandos: `POST /api/comandos` publica `cmd` e registra enviado → recebido (aceito/rejeitado + motivo) → executado → confirmado (estado `reservada`) | R7 | 17/11 |
| J7 | Login: usuário/senha com bcrypt e token JWT; só autenticado envia comando | R9 | 17/11 |
| J8 | Perfis: **operador** (vê estado, histórico e alertas) e **administrador** (reserva, configura, gerencia tags), verificados em cada rota; registrar a justificativa de não usar TLS no MQTT | R16 | 24/11 |
| J9 | Regra remota: reserva sem o carro chegar no tempo → `cancelar_reserva` com o ciclo do J6 + alerta; sensor em `erro` ou dispositivo `offline` por mais de 1 min → alerta | R8 | 24/11 |
| J10 | Telegram: bot avisa sensor em erro, dispositivo offline > 1 min e reserva expirada | R15 | 24/11 |
| J11 | `pytest`: validação do contrato (válidos e inválidos), ciclo de comando e regra de expiração | R18 | 24/11 |

## Vynicyus — interface, testes de falha e entrega

| # | Tarefa | Req. | Até |
|:-:|---|:-:|:-:|
| V1 | Painel do operador (web, servido pelo backend, fala só com a API): login, estado e distância de cada vaga, online/offline e último contato, alertas ativos, tags lidas, botão de reserva com o andamento do comando, formulário de configuração (limiares e tempo de reserva). Substitui o `app/index.html` da N1 | R6, R7 | 17/11 |
| V2 | Gráficos (Chart.js) com dados do banco: ocupação por hora do dia, tempo médio de permanência por vaga, uptime do dispositivo e tempo em `erro` | R14 | 24/11 |
| V3 | App do motorista (web responsivo no celular): vagas livres em tempo real e a vaga onde está o carro dele (pela tag) | R6 | 24/11 |
| V4 | Interface respeitando os perfis (operador não vê reserva nem configuração) | R16 | 24/11 |
| V5 | GitHub Actions: `pytest` do backend e compilação do firmware com `arduino-cli` a cada push/PR | R18 | 24/11 |
| V6 | Falhas testadas, cada uma com provocação, esperado, observado e evidência (log, vídeo ou print): queda do Wi-Fi, broker fora, sensor desconectado, comando inválido/duplicado; extra: RC522 desconectado | R10 | 24/11 |
| V7 | README executável (pré-requisitos, `.env.example`, `docker compose up`, gravação do firmware) e release/tag `v1.0` | R11 | 01/12 |

## Decisões da equipe

| Decisão | Responsável | Até |
|---|---|:-:|
| **R13 (vários dispositivos):** levar ao professor — manter 1 ESP32 (não atende) ou usar a placa reserva com uma vaga por ESP32 (o firmware e o broker já suportam) | Equipe | 17/11 |
| Posição do leitor RFID: confirmar "sob o piso" depois do G3 | Gustavo | 17/11 |

## Dependências

- **J1 (simulador)** destrava V1 e J2 sem esperar a maquete.
- **J5 (OpenAPI)** define o que o painel (V1) consome; combinar cedo.
- **G3 (bancada)** valida o firmware de que todo o resto depende; achados
  vão para o Luis.
- **V6 (falhas)** precisa de G1 e J2 prontos.
