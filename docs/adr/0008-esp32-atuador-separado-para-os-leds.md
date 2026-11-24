# ADR 0008 — ESP32 atuador separado para os LEDs

- **Status:** Não adotada (24/11/2026). A equipe não tinha cabo micro-USB
  para o segundo ESP32; os LEDs voltaram ao ESP32 único (firmware 0.13.0),
  como na decisão 1 da [ADR 0001](0001-rfid-e-app-motorista.md). O código
  de `src/atuador` fica no repositório como referência, sem uso na maquete.
- **Data:** 2026-11-24
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

A ADR 0001 decidiu por um único ESP32 operando sensores, leitores RFID e
LEDs, e descartou ESP32 separados por função pela quantidade de placas,
fontes e cabos. Na montagem da maquete, a placa única concentrou os 21
fios nas duas fileiras de pinos; mexer em um componente soltava outro,
e o RC522 da vaga 1 passou a falhar por mau contato.

A equipe tem duas placas (a reserva já estava na BOM). O R13 do CP12
(múltiplos dispositivos) estava pendente justamente por haver um só
`deviceId` ([pendências](../pendencias-n3.md)).

## Decisão

1. **Dois ESP32 com papéis distintos:**
   - **Sensores** (`src/garagem`, firmware 0.12.0): HC-SR04, RC522,
     reserva e comandos, como antes. Deixa de acionar LEDs.
   - **Atuador** (`src/atuador`, firmware 0.1.0): só os LEDs RGB das
     vagas, nos mesmos pinos de antes (32/33/16 e 27/13/17).
2. **Ligação pelo broker MQTT, sem tópico novo:** o atuador assina
   `garagem/v1/<sensor>/vaga/+/estado` e `garagem/v1/<sensor>/status`,
   ambos retidos. O estado publicado já é o efetivo (sensor + reserva +
   falha), então a cor sai direto dele. O `deviceId` do sensor vai no
   `secrets.h` do atuador (`SENSOR_DEVICE_ID`).
3. **Estado seguro sem ligação:** sem Wi-Fi, sem broker ou com o sensor
   offline (LWT), todas as vagas ficam em **vermelho piscando** — o mesmo
   sinal de falha do sensor. Nunca verde sem certeza.
4. **Usuário MQTT próprio (`atuador`):** lê só estado e status e publica
   só status. Não lê comandos.
5. **O atuador publica o próprio `status`** (online com `firmware` e
   `"vagas": []`, LWT offline). Lista vazia porque ele não mede vagas: o
   painel o mostra em Dispositivos, o alerta de offline vale para ele,
   e nenhum cartão de vaga é duplicado. Sem mudança no contrato v1.

## Consequências

- **R13 atendido:** dois dispositivos com `deviceId` próprio no broker,
  no banco e no painel.
- **R2 (regra local sem rede) atendido só em parte:** o LED depende do
  broker. Sem ligação, o atuador cai no estado seguro (vermelho
  piscando), mas não mostra mais o estado real da vaga até a rede
  voltar. A regra local continua valendo para o estado da vaga e a
  reserva, que seguem no ESP32 dos sensores.
- Latência sensor → LED passa a incluir o broker (dezenas a centenas de
  ms no Wi-Fi local); imperceptível para o motorista.
- Duas placas, duas fontes USB, dois `secrets.h` para manter.
- O CI compila os dois sketches.

## Alternativas descartadas

- **ESP-NOW direto entre as placas (com MQTT para o backend):** manteria
  o LED sem roteador, mas exige o mesmo canal de rádio do hotspot, pareamento
  por MAC e um segundo protocolo para manter. A equipe preferiu a
  simplicidade do broker, com o estado seguro cobrindo a falta de rede.
- **Continuar com uma placa e reorganizar a fiação:** não atende o R13.
- **Fio direto entre as placas (GPIO/UART):** acopla as placas
  fisicamente, que é o problema que se queria resolver.
