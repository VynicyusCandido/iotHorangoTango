# ADR 0003 — Firmware no contrato v1: biblioteca MQTT, fila de eventos offline e leitor RFID sob o piso

- **Status:** Aceito
- **Data:** 2026-11-10
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

O firmware (`src/garagem/garagem.ino`) ainda segue o formato da N1:
tópicos em texto (`garagem/vaga/N`, `garagem/rfid/N`), LED acionado
pelo que chega do broker (ADR 0001, decisão 4) e reconexão em `while` +
`delay()`. Com o broker fora, o `loop()` fica preso na reconexão: o
ESP32 não lê o ultrassônico nem o RFID, e um carro que chega ou sai
durante a queda não é registrado.

O contrato MQTT v1 (`CONTRATOS.md`, congelado em 27/10/2026) pede:
payload JSON com `v`, `deviceId` e `ts`; QoS 1 em `vaga/{n}/estado`,
`rfid`, `ack` e `status`; e o CP12 (R2) pede regra local sem rede e laço
sem bloqueio.

Com a identificação de carros por tag, o que acontece enquanto o
sistema está fora do ar passa a importar: o backend precisa saber quem
chegou, em que vaga e quando, mesmo que a rede tenha caído nesse
intervalo.

## Decisão

1. **Trocar a PubSubClient pela arduino-mqtt (Joel Gaehwiler).** A
   PubSubClient só publica em QoS 0. A arduino-mqtt publica em QoS 1 e
   tem API parecida. Com QoS 1, cada `publish` espera o PUBACK do
   broker; o tempo máximo é reduzido para 500 ms.
2. **Laço sem bloqueio.** O Wi-Fi reconecta sozinho (auto-reconnect do
   ESP32) e o firmware tenta o broker a cada 5 s, sem `while` nem
   `delay()`. Sensores e LEDs continuam funcionando durante a queda.
3. **LED segue o sensor localmente** (regra local do CP12, R2). Substitui
   a decisão 4 do ADR 0001: o LED não depende mais do broker. Verde =
   livre, vermelho = ocupada, vermelho piscando = sensor em falha,
   apagado = ainda sem leitura.
4. **Fila única de eventos, em ordem de acontecimento.** Toda mudança de
   estado confirmada e toda tag lida entram na mesma fila, com o
   momento em que aconteceram. Com o broker disponível, a fila é
   publicada em ordem (QoS 1) e cada evento só sai da fila depois de
   confirmado. Fila única para preservar a ordem entre tag e estado
   (tag lida antes ou depois de a vaga ficar ocupada).
   - Capacidade: **64 eventos** (~20 visitas de carro sem rede, ~3 KB de RAM).
   - Fila cheia: descarta o evento **mais antigo** e registra no Serial.
   - Ao reconectar, depois de esvaziar a fila, o estado atual de cada
     vaga é republicado (retained), para o caso de o broker ter
     reiniciado e perdido as mensagens retidas.
   - Telemetria, `status` e `ack` não entram na fila: telemetria é
     periódica (QoS 0 no contrato), `status` é republicado ao conectar e
     sem rede não chegam comandos para confirmar.
5. **Horário por NTP.** O ESP32 sincroniza o relógio ao ter Wi-Fi e
   continua contando durante a queda. Evento registrado antes da
   primeira sincronização ganha o horário real na publicação, calculado
   pelo uptime (mesmo boot). Se o relógio nunca sincronizou, o evento
   vai com `"ts": null, "ts_sincronizado": false, "uptime_ms"`, como
   prevê o contrato.
6. **Leitor RFID sob o piso da maquete, por enquanto.** Tag colada
   embaixo do carro; a leitura acontece quando o carro para sobre o
   leitor. O RC522 com tag pequena lê a poucos centímetros, e sob o piso
   a distância é constante. Ganho da antena no máximo. Decisão a
   revisar depois do teste de bancada.
7. **RFID e ultrassônico migram juntos para o v1**, para o firmware não
   publicar metade em v0 e metade em v1.

## Consequências

**Positivas**

- Nenhum evento perdido em quedas curtas: o backend reconstrói o que
  aconteceu com o sistema fora do ar, inclusive a ordem entre tag e
  estado.
- Firmware cumpre o contrato v1 congelado, sem abrir v2.
- LEDs funcionam sem rede.

**Negativas / riscos**

- **Fila só na RAM:** se o ESP32 reiniciar sem rede, os eventos da fila
  se perdem. Gravar em NVS a cada evento desgastaria a flash.
- **Queda longa:** acima de 64 eventos, os mais antigos se perdem.
- **Tentativa de conexão ainda espera:** com o Wi-Fi no ar e o broker
  inacessível, cada tentativa (a cada 5 s) pode segurar o laço por
  alguns segundos até o tempo limite do TCP. A leitura volta em seguida;
  eventos não se perdem, mas uma leitura do ultrassônico pode atrasar.
- **Painel da N1 (`app/index.html`) para de funcionar** com o firmware
  novo, porque assina os tópicos v0. Precisa migrar para o v1 ou ser
  substituído pelo painel servido pelo backend.
- **Reserva, comandos, NVS, telemetria configurável e OTA** ficam para a
  próxima etapa.
- Dependências novas: `MQTT` (arduino-mqtt, 256dpi) e `ArduinoJson` 7.

## Alternativas consideradas

- **Manter a PubSubClient e mudar o contrato para QoS 0 nos tópicos do
  ESP32 (v2):** sem trocar biblioteca, mas abre nova versão do contrato
  logo após congelar. Descartada.
- **Filas separadas para estado e RFID:** perde a ordem entre os dois
  tipos de evento. Descartada.
- **Guardar só o estado atual de cada vaga:** suficiente para o LED e o
  painel, mas perde o histórico da queda (quem chegou, quando).
  Descartada depois da decisão de identificar carros por tag.
- **Leitor RFID na frente da vaga:** depende de o carro parar alinhado,
  a poucos centímetros do leitor. Fica como alternativa se o piso
  atrapalhar a leitura.
