# ADR 0001 — Adicionar RFID ao ultrassônico em um único ESP32 e criar app do motorista

- **Status:** Proposto
- **Data:** 2026-10-20
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

Hoje cada vaga tem um sensor ultrassônico HC-SR04 ligado a um ESP32
(`src/sensor/sensor.ino`). O ESP32 mede a distância, aplica histerese
(25/35 cm) e confirmação por 3 leituras, e publica `livre` / `ocupada` /
`erro` em `garagem/vaga/N` (retained). Um segundo ESP32 (`src/atuador`)
acende os LEDs e o app web (`app/index.html`) mostra as vagas livres.

O ultrassônico diz *se* há um carro na vaga, mas não *qual* carro. Para
oferecer funções ao motorista (saber onde parou, benefícios para
usuários cadastrados) é preciso identificar o veículo.

Foi avaliado trocar o ultrassônico por RFID. A troca foi descartada:
nem todo carro terá tag, e um carro sem tag precisa ser detectado do
mesmo jeito. Só o sensor físico garante isso.

O projeto terá **duas vagas**. Cada vaga tem 1 HC-SR04, 1 leitor RFID,
1 LED RGB (verde = livre, vermelho = ocupada). O broker Mosquitto e o painel de
configuração/monitoramento rodam no mesmo servidor; o app do motorista
roda no celular de cada motorista.

## Decisão

1. **Um único ESP32 opera as duas vagas e todos os periféricos:**
   2× HC-SR04, 2× RC522 (13,56 MHz, SPI, 3,3 V, mesmo barramento com
   pinos SS separados) e 2 LEDs RGB (catodo comum; só os canais
   vermelho e verde são usados, 2 GPIOs por vaga). Os sketches `sensor.ino` e
   `atuador.ino` passam a ser um só firmware.
2. **O ultrassônico continua sendo a única fonte do estado da vaga.**
   Histerese, confirmação e tratamento de `erro` não mudam. Carro com ou
   sem tag ocupa a vaga do mesmo jeito.
3. **O RFID publica toda leitura, sem depender do estado da vaga.** A
   cada tag lida, o ESP32 publica o UID em `garagem/rfid/N` (não
   retained, é um evento). No firmware, a parte do RFID não lê nem
   altera o estado da vaga.
4. **Os LEDs continuam sendo acionados pelo broker, não direto pelo
   sensor.** O ESP32 assina `garagem/vaga/#` e acende os LEDs a partir da
   mensagem recebida, mesmo sendo ele quem publicou. Assim os LEDs
   mostram o que o broker distribuiu para todos (o mesmo que painel e
   app veem), e a demonstração "derrubar o broker para os LEDs pararem"
   continua válida. Sem conexão com o broker, o ESP32 apaga os LEDs
   (estado desconhecido).
5. **Cada subscriber assina só o que lhe interessa:**

   | Subscriber                    | `garagem/vaga/#` (ultrassônico) | `garagem/rfid/#` (RFID) |
   |-------------------------------|:-------------------------------:|:-----------------------:|
   | LEDs (no próprio ESP32)        | sim                            | não                     |
   | Painel de configs (servidor)   | sim                            | sim                     |
   | App do motorista (celular)     | sim                            | sim                     |

   Cruzar leitura de tag com estado da vaga (ex.: tag lida antes de o
   ultrassônico confirmar o carro) é responsabilidade do app e do
   painel, não do firmware.
6. **Contrato MQTT:**
   - `garagem/vaga/N` → `livre` / `ocupada` / `erro` (retained) — sem
     mudança;
   - `garagem/rfid/N` → UID da tag (evento) — novo;
   - `garagem/no/<id>/status` → `online` / `offline` (Last Will) e
     `garagem/no/<id>/vagas` → `1,2` — sem mudança. Como é um nó só, um
     Last Will cobre ultrassônico e RFID.

   O UID fica **fora** de `garagem/vaga/#` de propósito: o firmware faz
   `atoi()` do resto do tópico, e `garagem/vaga/1/tag` seria lido como
   estado da vaga 1.
7. **Privilégios das tags: ainda não definidos.** Este ADR cria só a
   identificação. Quais benefícios uma tag dá (ex.: vaga reservada,
   prioridade, acesso a áreas restritas, cobrança diferenciada) e onde
   essa regra fica (app, painel ou um serviço à parte) serão decididos
   em um ADR futuro.
8. **Adicionar um app do motorista** (web, mesmo modelo do
   `app/index.html`: MQTT sobre WebSocket, sem build), servido pelo
   servidor e aberto no navegador do celular. O `app/index.html` atual
   passa a ser o painel de configurações/monitoramento.

Fluxo resultante (detalhes em
[fluxograma-firmware.md](../fluxograma-firmware.md)):

```
            ┌──────────────── ESP32 (vagas 1 e 2) ────────────────┐
HC-SR04 ×2 ─┤ ultrassônico ──► publica garagem/vaga/N ─────────────┼──┐
RC522   ×2 ─┤ RFID         ──► publica garagem/rfid/N ─────────────┼──┤
LED RGB ×2 ◄┤ LEDs         ◄── assina  garagem/vaga/# ◄────────────┼──┤
            └──────────────────────────────────────────────────────┘  │
                                                                      ▼
                          Servidor: Mosquitto ──┬──► Painel de configs (mesmo servidor)
                                                └──► App do motorista (celular)
```

## Consequências

**Positivas**

- Vaga ocupada sempre detectada, com ou sem tag.
- Carros com tag passam a ser identificados, o que abre caminho para
  privilégios e histórico de uso.
- Menos hardware: 1 ESP32, 1 fonte, 1 cabo USB. Montagem e alimentação
  mais simples para a maquete de duas vagas.
- Contrato MQTT preservado: painel atual e clientes MQTT de teste
  continuam funcionando.

**Negativas / riscos**

- **Ponto único de falha:** se o ESP32 trava, reinicia ou perde o
  Wi-Fi, as duas vagas ficam sem dados, sem RFID e com LEDs apagados.
  O broker publica `offline` (Last Will) e painel/app mostram "sem
  dados".
- **Um firmware faz tudo:** atualizar ou depurar o RFID exige regravar
  o ESP32 inteiro. O loop precisa ser não bloqueante — o `pulseIn()` do
  HC-SR04 espera até 30 ms por sensor; RFID e MQTT não podem ficar
  esperando atrás disso.
- **Pinos:** o mapa atual conflita com o SPI (`sensor.ino` usa 5 e 18,
  `atuador.ino` usa 22 e 23 — todos usados pelo RC522). Novo mapa em
  [mapa-de-pinos.md](../mapa-de-pinos.md). GPIO 6–11 (flash) não podem ser usados e 34–39
  são só entrada (bons para Echo).
- **Distância dos fios:** os dois HC-SR04 e os dois RC522 saem do mesmo
  ESP32. SPI e o pulso do ultrassônico não toleram fios longos
  (acima de ~30–50 cm), então as duas vagas precisam ficar próximas da
  placa.
- **Interferência entre os HC-SR04:** os dois sensores devem disparar
  um de cada vez (o loop atual já faz assim) e não podem apontar um
  para o outro.
- **Consumo:** 2 RC522 + 2 HC-SR04 + LEDs ficam dentro do que a porta
  USB fornece, mas o RC522 deve ser alimentado em 3,3 V, nunca em 5 V.
- A correlação tag ↔ vaga ocupada fica nos subscribers (app e painel),
  e cada um precisa aplicar a mesma regra (ex.: janela de tempo entre a
  leitura e a confirmação do ultrassônico). Se os privilégios exigirem
  uma decisão única e confiável, essa regra deve ir para um serviço
  central — tema do ADR de privilégios.
- Evento não retained: app ou painel aberto depois da leitura não a
  recebe. Se precisarem do "carro atual da vaga" ao abrir, será preciso
  um tópico retained ou um serviço que guarde o histórico.
- O UID da tag trafega em claro e o broker aceita conexão anônima
  (`allow_anonymous true`). UID não é segredo (pode ser clonado) e
  qualquer cliente no broker lê ou publica eventos falsos em
  `garagem/rfid/#`. Aceitável enquanto a tag só identifica; **antes** de
  a tag conceder privilégios, será preciso autenticação/ACL no Mosquitto
  (só o ESP32 publica em `garagem/rfid/#` e `garagem/vaga/#`), TLS e
  tags com autenticação (ex.: MIFARE DESFire) em vez de confiar só no
  UID.
- Celulares dos motoristas precisam alcançar o servidor (porta 9001
  WebSocket). Na demonstração, mesma rede Wi-Fi; fora dela, endereço
  público, `wss://` e autenticação.
- README precisa ser atualizado (um sketch, novo mapa de pinos,
  biblioteca `MFRC522`).

## Alternativas consideradas

- **Manter só o ultrassônico:** sem custo novo, mas não identifica o
  motorista — não atende o objetivo do app.
- **Trocar o ultrassônico por RFID:** identifica o motorista, mas carro
  sem tag (ou que esquece de passar) não é detectado e a vaga aparece
  livre estando ocupada. Descartada por isso.
- **ESP32 separados por função** (ultrassônico, RFID e LEDs em placas
  diferentes): isola falhas e permite atualizar cada parte sozinha, mas
  triplica placas, fontes e cabos. Para duas vagas lado a lado, a
  equipe preferiu a simplicidade de uma placa só.
- **LEDs acionados direto pelo sensor, sem passar pelo broker:** reage
  mais rápido e funciona sem rede, mas os LEDs poderiam mostrar algo
  diferente do que painel e app veem, e o projeto deixa de demonstrar
  a comunicação via MQTT.
- **Publicar o UID dentro de `garagem/vaga/N`** (ex.: payload JSON):
  quebraria o painel atual e os LEDs, que esperam texto puro
  `livre`/`ocupada`/`erro`.
