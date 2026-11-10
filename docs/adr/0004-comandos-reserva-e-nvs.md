# ADR 0004 — Comandos, reserva com LED azul e configuração em NVS no firmware

- **Status:** Aceito
- **Data:** 2026-11-10
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

O firmware v1 (ADR 0003) já publica estado, RFID, telemetria e status
no contrato MQTT v1, mas não recebe comandos. O CP12 pede, do lado do
dispositivo:

- **R7:** ciclo de comando `reservar` / `cancelar_reserva` /
  `configurar` com `ack` (`aceito`, `rejeitado` com motivo, `executado`)
  e intertravamento;
- **R2:** configuração persistente em NVS (limiares e tempo de reserva,
  alteráveis pelo comando `configurar`);
- **§5.3:** estado *reservada* com LED azul e timeout contado no
  dispositivo, mesmo sem rede.

O contrato v1 (`CONTRATOS.md`, congelado em 27/10/2026) deixa alguns
casos sem resposta definida.

## Decisão

1. **O ESP32 assina `garagem/v1/{id}/cmd` com QoS 1 e sessão limpa.**
   Comando publicado com o dispositivo offline não é entregue depois,
   como pede o contrato ("comando antigo não pode ser executado ao
   reconectar"). A ACL do broker garante que só o backend publica em
   `cmd`.
2. **Respostas pela fila de eventos.** O callback do MQTT não publica
   nada: `ack`s e mudanças de estado entram na fila única (ADR 0003),
   que garante a ordem `aceito` → estado → `executado`. A biblioteca
   arduino-mqtt não deve publicar de dentro do callback.
3. **Validação, na ordem:** JSON malformado ou sem `commandId` →
   `PAYLOAD_INVALIDO` com `commandId: null`; `commandId` já visto (últimos
   16) → `COMANDO_DUPLICADO`, sem executar; `v` ≠ 1 →
   `PAYLOAD_INVALIDO`; ação desconhecida → `ACAO_DESCONHECIDA`; `vaga`
   ausente ou não inteira → `PARAMETRO_INVALIDO`; vaga de outro
   dispositivo → `VAGA_INEXISTENTE`; campo fora da faixa →
   `PARAMETRO_INVALIDO`.
4. **Estado publicado = sensor + reserva.** Falha do sensor vence tudo
   (`erro`); senão, reserva ativa → `reservada`; senão, o estado do
   sensor. LED: verde livre, vermelho ocupada, **azul reservada**,
   vermelho piscando em falha. O canal azul (GPIO 16 e 17) passa a ser
   obrigatório.
5. **Lacunas do contrato, resolvidas sem mudar o formato:**
   - `reservar` em vaga **já reservada** → `VAGA_OCUPADA` (vaga
     indisponível). Um código novo (`VAGA_RESERVADA`) exigiria contrato v2.
   - `reservar` com o estado ainda **desconhecido** (antes da primeira
     leitura) → `VAGA_EM_ERRO`: sem certeza, não reserva.
   - Payload inválido sem `commandId` → `ack` com `"commandId": null`.
   - `duracao_min` é **opcional** em `reservar`; sem ele, vale o
     `duracao_reserva_min` configurado. Sem isso, o parâmetro
     configurável não teria uso.
   - `configurar` exige os três campos; `limiar_libera_cm` também não
     pode passar de 400 cm (alcance do HC-SR04), senão a vaga nunca
     voltaria a livre.
6. **A reserva termina** quando o carro chega (sensor confirma
   `ocupada`), o tempo acaba (contado no ESP32 com `millis()`, mesmo sem
   rede), chega `cancelar_reserva` ou o sensor falha (`erro`). O backend
   também cancela reservas vencidas (CP12, R8); se o ESP32 já tiver
   expirado a reserva, o `cancelar_reserva` do backend recebe
   `SEM_RESERVA`, o que é inofensivo.
7. **A reserva fica só na RAM.** Se o ESP32 reiniciar, a vaga volta ao
   estado do sensor e o backend percebe pelo novo estado publicado.
8. **Configuração em NVS** (`Preferences`, namespace `garagem`):
   `limiar_ocupa_cm`, `limiar_libera_cm` e `duracao_reserva_min`.
   Carregada no boot; valores gravados inválidos são ignorados e os
   padrões (25 cm, 35 cm, 15 min) entram no lugar. `configurar` aplica
   na hora e grava.
9. **Histórico de `commandId` só na RAM** (últimos 16). Após reinício, o
   broker não reentrega comandos antigos (sessão limpa), então não há
   duplicado a detectar.

## Consequências

**Positivas**

- Lado do dispositivo do R7 completo: intertravamento, `ack` com motivo
  e confirmação pelo estado `reservada`.
- Calibração da maquete sem regravar o firmware.
- Reserva expira mesmo com a rede fora.

**Negativas / riscos**

- **Reserva perdida no reinício** do ESP32 (decisão 7).
- **`VAGA_OCUPADA` para vaga reservada** é menos claro para o usuário
  final; o painel pode mostrar "indisponível".
- Fila cheia (64) descarta o evento mais antigo, que pode ser um `ack`.
- Mais 2 GPIOs (16 e 17) e 2 resistores; sobram só 21, 36 e 39 livres.

## Verificação

Teste no PC, com o firmware compilado contra simulações do hardware, do
MQTT e da NVS (36 verificações): boot, reserva aceita na ordem certa,
LED azul, todos os códigos de erro, chegada do carro, expiração com o
broker fora, cancelamento, sensor em falha, `configurar` gravando na NVS
e mudando a classificação, duração padrão e recarga da configuração no
boot. Falta a verificação no ESP32 físico.
