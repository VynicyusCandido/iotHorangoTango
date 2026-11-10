# iotHorangoTango

Sistema IoT de monitoramento de vagas de estacionamento, usando
sensores ultrassônicos, leitores RFID, ESP32 e comunicação via MQTT.

## Sobre o projeto

Este repositório contém o projeto desenvolvido para a disciplina de
Internet das Coisas (N1) da Universidade Católica de Santa Catarina. O
sistema monitora em tempo real a ocupação das vagas de uma garagem
inteligente: um sensor ultrassônico HC-SR04 detecta a presença de
veículo em cada vaga, e um leitor RFID RC522 identifica os carros que
possuem tag. Um único ESP32 processa as leituras das duas vagas,
publica o estado (livre/ocupada) e as tags lidas em um broker MQTT, e
aciona um LED RGB por vaga a partir do que recebe do broker. Dessa
forma, motoristas podem consultar previamente quais vagas estão
disponíveis antes de entrar no estacionamento, e os LEDs orientam quem
já está circulando pela garagem.

O RFID é um complemento: carro sem tag é detectado normalmente pelo
ultrassônico. A tag apenas identifica o veículo, para que motoristas
cadastrados recebam privilégios (ainda a definir). A decisão está
registrada em [docs/adr/0001-rfid-e-app-motorista.md](docs/adr/0001-rfid-e-app-motorista.md).

**Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
Angelico, Vynicyus Candido.

## Problema, usuário e rumo do projeto (N1 → N3)

- **Problema:** em garagens, o motorista perde tempo procurando vaga e o
  operador não sabe em tempo real quantas vagas estão livres.
- **Usuários:** o **motorista** (consulta vagas livres no celular e é
  guiado pelos LEDs) e o **operador/administrador** do estacionamento
  (monitora, configura e reserva vagas).
- **Estado atual (herdado da N1):** ESP32 com HC-SR04 publicando o
  estado de cada vaga em MQTT, LEDs indicando livre/ocupada via broker,
  app web de vagas livres e broker Mosquitto local. Já evoluído neste
  repositório: firmware único para 2 vagas com RFID (identificação do
  carro com tag) e LED RGB.
- **Para onde vai (N3):** produto **HorangoTango Park** — backend
  FastAPI que valida e grava tudo no PostgreSQL, painel do operador com
  login, histórico e gráficos, reserva de vaga com confirmação do
  hardware (LED azul), app do motorista, contrato MQTT em JSON
  versionado, regra local sem rede no firmware, alertas no Telegram,
  OTA, Docker Compose e CI.
- **Proposta completa (CP12):** [docs/CP12.md](docs/CP12.md).

## Como funciona

Um ESP32 cuida das duas vagas (`src/garagem/garagem.ino`) e segue o
contrato MQTT v1 ([CONTRATOS.md](CONTRATOS.md)). Sensores e LEDs
funcionam sem rede; o que acontece com o sistema fora do ar vai para
uma fila e é publicado quando o broker volta
([ADR 0003](docs/adr/0003-firmware-v1-fila-de-eventos.md)).

**Ultrassônico — estado da vaga**

1. **Leitura dos sensores** — a cada 1 s o ESP32 dispara o HC-SR04 de
   cada vaga, um de cada vez (para um não captar o eco do outro), e
   mede a distância até o objeto à frente (o chão da vaga, ou um
   veículo estacionado).
2. **Histerese** — abaixo de 25 cm a vaga passa a ocupada, acima de
   35 cm passa a livre, e entre os dois mantém o estado atual. Isso
   evita que uma leitura oscilando perto do limite fique trocando o
   estado.
3. **Confirmação** — o estado só muda depois de 3 leituras seguidas
   concordando (cerca de 3 s), o que filtra ruído e alguém passando na
   frente do sensor.
   **Falha no sensor:** leituras sem eco ou fora da faixa do HC-SR04
   (2–400 cm) são descartadas, nunca contam como "livre". Após 5
   seguidas, a vaga passa a `erro`.
4. **Evento** — cada mudança confirmada vira um evento na fila, com o
   momento em que aconteceu.

**RFID — identificação do carro**

5. **Leitura das tags** — a cada 100 ms o ESP32 consulta o RC522 de
   cada vaga. O leitor fica **sob o piso** da maquete e a tag, embaixo
   do carro. Cada tag lida vira um evento na fila; a mesma tag lida de
   novo no mesmo leitor em menos de 3 s é ignorada.
6. **Independente da vaga** — o RFID não lê nem altera o estado da
   vaga. Cruzar a tag com a vaga ocupada é papel do backend.
7. **Leitor travado** — a cada 5 s o ESP32 confere se os RC522 respondem
   e, se algum parou, reinicia os dois (o pino de reset é comum).

**LEDs — regra local**

8. O LED de cada vaga segue o estado do sensor no próprio ESP32, **com
   ou sem rede**: verde = livre, vermelho = ocupada, vermelho piscando =
   sensor em falha, apagado = ainda sem leitura.

**Fila de eventos e publicação**

9. **Fila única, em ordem** — mudanças de estado e tags lidas ficam na
   mesma fila (até 64 eventos; cheia, descarta o mais antigo). Com o
   broker disponível, os eventos são publicados em ordem, com QoS 1, e
   só saem da fila depois que o broker confirma o recebimento.
10. **Horário** — o ESP32 sincroniza o relógio por NTP ao ter Wi-Fi e
    continua contando durante a queda. Cada evento leva o horário em que
    aconteceu (`ts`), não o da publicação. Se o relógio nunca
    sincronizou, vai `"ts": null` com o `uptime_ms`, como prevê o
    contrato.
11. **Reconexão sem bloqueio** — o Wi-Fi reconecta sozinho e o firmware
    tenta o broker a cada 5 s, sem parar o laço. Ao conectar, publica o
    `status` (`online`), esvazia a fila e republica o estado atual de
    cada vaga. Se o ESP32 cair, o broker publica `offline` sozinho (Last
    Will).
12. **Telemetria** — a cada 10 s, com o broker disponível: distância de
    cada vaga, RSSI do Wi-Fi e uptime.

Durante os testes, todas as leituras, tags e eventos também podem ser
acompanhados pelo monitor serial do ESP32.

## Arquitetura

```
            ┌──────────────── ESP32 (vagas 1 e 2) ─────────────────┐
HC-SR04 ×2 ─┤ ultrassônico ─┐                                       │
RC522   ×2 ─┤ RFID ─────────┼─► fila de eventos ─► publica (QoS 1) ─┼──┐
LED RGB ×2 ◄┤ LEDs ◄────────┘  (regra local, sem rede)              │  │
            └───────────────────────────────────────────────────────┘  │
                                                                       ▼
                                                  Servidor: Mosquitto ──► backend (N3)
```

Tópicos (`{id}` = `deviceId`, ex.: `esp32-garagem-240ac4000110`, derivado
do MAC). Payloads completos em [CONTRATOS.md](CONTRATOS.md).

| Tópico                            | Conteúdo                                         | QoS | Retained |
|-----------------------------------|--------------------------------------------------|:---:|:--------:|
| `garagem/v1/{id}/vaga/{n}/estado` | `livre` / `ocupada` / `erro` + distância (JSON)  | 1   | sim      |
| `garagem/v1/{id}/rfid`            | vaga + UID da tag, ex.: `04A1B2C3` (JSON)        | 1   | não      |
| `garagem/v1/{id}/telemetria`      | distâncias, RSSI, uptime — a cada 10 s (JSON)    | 0   | não      |
| `garagem/v1/{id}/status`          | `online` / `offline` (Last Will), firmware, vagas | 1   | sim      |

Exemplo (`garagem/v1/esp32-garagem-240ac4000110/rfid`):

```json
{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-11-10T21:30:07Z",
  "vaga": 1, "uid": "04A1B2C3" }
```

Reserva de vaga (estado `reservada`, LED azul) e os tópicos `cmd` / `ack`
entram na próxima etapa.

- **Broker:** Mosquitto no servidor (ou HiveMQ público para testes sem
  RFID)
- **Painel da N1 (`app/index.html`):** ainda lê os tópicos antigos
  (`garagem/vaga/N`) e **não mostra os dados do firmware v1**. Será
  substituído pelo painel servido pelo backend.
- **App do motorista:** em desenvolvimento

Fluxogramas do firmware em
[docs/fluxograma-firmware.md](docs/fluxograma-firmware.md).

> Em um broker público como o HiveMQ, os tópicos `garagem/#` são
> compartilhados com qualquer pessoa — inclusive os UIDs das tags. Use o
> Mosquitto local sempre que o RFID estiver ligado.

## Hardware necessário

- 1× ESP32 DevKit de 30 pinos (ESP32-WROOM-32, USB-C, CP2102) — de
  preferência mais 1 de reserva
- 2× sensor ultrassônico HC-SR04 (ou HC-SR04P, versão 3,3 V)
- 2× leitor RFID RC522 + tags de **13,56 MHz** (MIFARE 1K; tags de
  125 kHz não funcionam)
- 2× LED RGB catodo comum (ou módulo RGB com resistores)
- Resistores: 2× 220 Ω (vermelho) e 2× 68–100 Ω (verde) para os LEDs;
  2× 1 kΩ + 2× 2 kΩ para o divisor de tensão do Echo (dispensável com
  HC-SR04P)
- Protoboards (a placa ocupa quase toda a largura de uma de 830 furos;
  use duas lado a lado), jumpers, cabo USB-C **de dados** e fonte USB
  5 V / 1 A
- Servidor (PC, notebook ou Raspberry Pi) e roteador Wi-Fi **2,4 GHz**
  na mesma rede do ESP32 e dos celulares

> **O RC522 funciona só em 3,3 V** — ligado em 5 V, queima. As duas
> vagas precisam ficar perto do ESP32 (fios de até ~40 cm), porque SPI e
> o pulso do ultrassônico não toleram fios longos.

## Software necessário

- Arduino IDE (ou PlatformIO / arduino-cli)
- Suporte à placa ESP32 instalado no gerenciador de placas
- Bibliotecas:
  - `MQTT` (Joel Gaehwiler, arduino-mqtt) — comunicação MQTT com QoS 1
  - `ArduinoJson` (Benoit Blanchon), versão 7 — payloads JSON
  - `MFRC522` (GithubCommunity) — leitores RFID
  - `WiFi` e `SPI` (nativas do ESP32)
- No Windows, se a porta COM não aparecer, instale o driver CP210x da
  Silicon Labs
- Servidor: Docker com `docker compose` (broker Mosquitto 2.1.2; backend
  e banco nas próximas etapas)

## Como executar

### 1. Clonar o repositório

```bash
git clone https://github.com/<usuario>/iotHorangoTango.git
cd iotHorangoTango
```

### 2. Montar o circuito

Siga o [mapa de pinos](docs/mapa-de-pinos.md). Resumo:

| Vaga | HC-SR04 Trig / Echo | RC522 SS | LED RGB R / G |
|:----:|:-------------------:|:--------:|:-------------:|
| 1    | GPIO 25 / 34        | GPIO 5   | GPIO 32 / 33  |
| 2    | GPIO 26 / 35        | GPIO 4   | GPIO 27 / 13  |

Os dois RC522 dividem o SPI (SCK 18, MISO 19, MOSI 23) e o RST (22),
alimentados no 3V3. Os HC-SR04 vão no VIN (5 V), com o divisor de
tensão no Echo em hardware físico. GND comum a todos os componentes.

### 3. Instalar as dependências

Na Arduino IDE, abra o gerenciador de bibliotecas e instale `MQTT`
(Joel Gaehwiler), `ArduinoJson` 7.x (Benoit Blanchon) e `MFRC522`
(GithubCommunity). A `PubSubClient` não é mais usada. Confirme
que a placa ESP32 está selecionada em **Ferramentas → Placa**.

### 4. Configurar credenciais e broker

**Wi-Fi:** a rede e a senha ficam num arquivo `secrets.h` dentro da
pasta do sketch, que **não vai para o git** (está no `.gitignore`).
Copie o modelo e preencha:

```bash
cp src/garagem/secrets.example.h src/garagem/secrets.h
```

```cpp
#define WIFI_SSID     "SUA_REDE"
#define WIFI_PASSWORD "SUA_SENHA"
```

Sem o `secrets.h`, a compilação para com a mensagem
`Falta secrets.h: copie secrets.example.h para secrets.h ...`.

> Nunca coloque a senha direto no `.ino`: o repositório é público. A
> senha continua indo para o ESP32 na compilação, só não fica no GitHub.

**MQTT:** o broker também fica no `secrets.h`. Sem estas linhas, o
firmware usa `broker.hivemq.com:1883` sem usuário:

```cpp
#define MQTT_HOST     "192.168.1.100"   // IP do servidor com Mosquitto
#define MQTT_PORT     1883
#define MQTT_USER     "garagem"         // vazio = conexão anônima
#define MQTT_PASSWORD "SENHA_DO_BROKER"
```

O `deviceId` é gerado automaticamente a partir do MAC do ESP32
(`esp32-garagem-<mac>`) e aparece no monitor serial ao ligar. Para
simulação no Wokwi, use `WIFI_SSID "Wokwi-GUEST"` e `WIFI_PASSWORD ""`
no `secrets.h`.

### 5. Ajustar os limiares de detecção

Defina no código as distâncias que separam "livre" de "ocupada",
conforme a altura de montagem do sensor sobre a vaga. Mantenha uma
folga entre os dois valores (a faixa de histerese):

```cpp
const float LIMIAR_OCUPA_CM  = 25.0;  // abaixo disto: ocupada
const float LIMIAR_LIBERA_CM = 35.0;  // acima disto: livre
const int   LEITURAS_CONFIRMACAO = 3; // leituras seguidas para trocar o estado
const int   FALHAS_PARA_ERRO     = 5; // leituras inválidas seguidas até "erro"
```

Os valores padrão são para carro real. Na maquete (carrinho de
brinquedo, sensor a ~15 cm do chão), meça e ajuste — algo como ocupada
abaixo de 10 cm e livre acima de 13 cm.

### 6. Compilar e carregar

Abra `src/garagem/garagem.ino`, escolha a porta em
**Ferramentas → Porta** e clique em **Upload**. Abra o monitor serial
(115200 baud). Ao ligar, ele mostra se cada RC522 respondeu
(`RFID vaga N: leitor OK` ou `LEITOR NAO RESPONDE`) e o `deviceId`, e
depois cada mudança de estado, tag lida, queda e volta da rede.

### 7. Monitorar via MQTT

Conecte-se ao mesmo broker com um cliente MQTT para acompanhar as
publicações:

- **Cliente web:** MQTTX Web (https://mqttx.app/web) ou HiveMQ Web
  Client
- **Host:** `broker.hivemq.com` — **Porta:** `8884` (WebSocket + SSL,
  para páginas https) ou `8000` (WebSocket sem SSL, para páginas http)
- Assine `garagem/#` para ver estados das vagas e tags chegando em
  tempo real.

## Broker local com Mosquitto (Docker Compose)

O broker do projeto é o **Eclipse Mosquitto 2.1.2**, rodando em Docker
no servidor. Sem `.env` configurado, o firmware usa o broker **público**
`broker.hivemq.com`, sem autenticação, só para testes sem RFID.

> **Pré-requisito:** este modo exige **ESP32 físico** na mesma rede Wi-Fi
> do servidor. O ESP32 simulado no Wokwi alcança só a internet pública e
> **não** enxerga a sua rede local — para continuar no Wokwi, mantenha o
> broker público.

O que o broker faz (`broker/`):

- **Usuário e senha obrigatórios** (`allow_anonymous false`). São três
  usuários, definidos no `.env`:

  | Usuário (padrão) | Quem usa | Pode |
  |---|---|---|
  | `dispositivo` | ESP32 | publicar estado, RFID, telemetria, `ack` e `status` **só no próprio** `garagem/v1/<deviceId>/`; ler o próprio `cmd` |
  | `backend` | backend (N3) | ler `garagem/v1/#`; único que publica `cmd` |
  | `monitor` | equipe (testes) | só ler `garagem/#` |

- **ACL** em `broker/acl`: cada ESP32 só mexe no próprio prefixo (o
  firmware conecta com o `deviceId` como client id).
- **Persistência:** mensagens retidas (estado das vagas, `status`)
  sobrevivem ao reinício do broker (volume `mosquitto-dados`).
- **Portas:** 1883 (MQTT) e 9001 (WebSocket, para MQTTX Web).

### 1. Instalar o Docker

- **Windows/macOS:** Docker Desktop
- **Linux:** Docker Engine com o plugin `docker compose`

Para os testes dos passos 4 e 7, instale também os clientes do Mosquitto
(`mosquitto_pub` / `mosquitto_sub`): `sudo apt install mosquitto-clients`
no Linux, `brew install mosquitto` no macOS ou o instalador de
[mosquitto.org/download](https://mosquitto.org/download/) no Windows.

> No Linux, se o pacote `mosquitto` (o broker) estiver instalado como
> serviço, ele ocupa a porta 1883. Pare antes de subir o container:
> `sudo systemctl disable --now mosquitto`.

### 2. Configurar as senhas e subir o broker

Na raiz do repositório:

```bash
cp .env.example .env     # troque as três senhas no .env
docker compose up -d
docker compose logs -f mosquitto   # Ctrl+C para sair dos logs
```

O `.env` não vai para o git. As senhas viram hash no arquivo de senhas
do broker a cada subida; para trocar uma senha, edite o `.env` e rode
`docker compose up -d` de novo.

### 3. Descobrir o IP do servidor e liberar as portas

O ESP32 precisa do **IP da máquina** do broker na LAN — não use
`localhost` nem `127.0.0.1` (para o ESP32, isso apontaria para ele
mesmo).

- **Windows:** `ipconfig` → campo **Endereço IPv4** (ex.: `192.168.1.100`)
- **Linux/macOS:** `ip addr` (ou `ifconfig`) → endereço da interface Wi-Fi

Reserve esse IP no roteador (reserva DHCP): se ele mudar, o ESP32
precisa ser regravado. Libere as portas **1883** e **9001** no firewall:

- **Windows:** Firewall do Windows → Regras de Entrada → Nova regra →
  Porta → TCP 1883, 9001 → Permitir
- **Linux (ufw):** `sudo ufw allow 1883/tcp && sudo ufw allow 9001/tcp`

### 4. Testar o broker na rede (antes de mexer no ESP32)

De **outro** computador na mesma rede (troque o IP e as senhas):

```bash
# terminal 1 — assina como monitor
mosquitto_sub -h 192.168.1.100 -u monitor -P SENHA_MONITOR -t "garagem/#" -v

# terminal 2 — publica como um dispositivo de teste
mosquitto_pub -h 192.168.1.100 -u dispositivo -P SENHA_DISPOSITIVO \
  -i esp32-garagem-teste -t "garagem/v1/esp32-garagem-teste/rfid" -m "teste"
```

Se a mensagem aparecer no terminal 1, o broker está no ar, visível na
rede e com a ACL funcionando. Uma publicação fora do próprio prefixo
(ex.: `-t garagem/v1/outro/rfid`) **não** deve aparecer.

### 5. Apontar o sketch para o broker local

No `secrets.h`, use a rede real, o IP do servidor e o usuário
`dispositivo` do `.env`:

```cpp
#define WIFI_SSID     "SUA_REDE"
#define WIFI_PASSWORD "SUA_SENHA"
#define MQTT_HOST     "192.168.1.100"      // IP do servidor (passo 3)
#define MQTT_USER     "dispositivo"
#define MQTT_PASSWORD "SENHA_DISPOSITIVO"  // a mesma do .env
```

### 6. Compilar e carregar no ESP32 físico

**Ferramentas → Porta** correta → **Upload**. Abra o monitor serial
(115200 baud). Ele deve mostrar `Conectando ao broker MQTT... conectado.`

### 7. Verificar a comunicação

- `docker compose logs -f mosquitto` mostra o `New client connected` do
  ESP32 com o `deviceId` e o usuário `dispositivo`.
- Assine como monitor
  (`mosquitto_sub -h <IP> -u monitor -P <senha> -t "garagem/v1/#" -v`)
  para ver os eventos; ao ocupar/liberar a vaga, o LED reage e o estado
  aparece em `garagem/v1/<deviceId>/vaga/N/estado`, e ao passar o carro
  com tag sobre o leitor o UID aparece em `garagem/v1/<deviceId>/rfid`.
- **Teste de queda (fila de eventos):** `docker compose stop mosquitto`,
  ocupe e libere as vagas e passe tags. Os LEDs continuam funcionando e
  o serial mostra os eventos indo para a fila. `docker compose start
  mosquitto`: em até 5 s o ESP32 reconecta e publica os eventos em
  ordem, cada um com o horário em que aconteceu (`ts`).

### Se não conectar (checklist rápido)

- ESP32 e servidor estão na **mesma** rede Wi-Fi (2,4 GHz)?
- O `MQTT_HOST` no `secrets.h` é o **IP da LAN** (passo 3), não `localhost`?
- `MQTT_USER` / `MQTT_PASSWORD` do `secrets.h` batem com o `.env`? O
  serial mostra `rc 5` (não autorizado) quando não batem.
- `docker compose ps` mostra o `mosquitto` como `healthy`?
- As portas **1883** e **9001** estão liberadas no firewall?
- O teste do passo 4, de outra máquina, funciona?

### Voltar ao broker público

Para rodar de novo no Wokwi, remova `MQTT_HOST`, `MQTT_USER` e
`MQTT_PASSWORD` do `secrets.h` (volta ao `broker.hivemq.com`) e use o
Wi-Fi `Wokwi-GUEST` (senha vazia).

## Painel de configuração e monitoramento

> **Painel da N1.** Ele ainda lê os tópicos antigos (`garagem/vaga/N`,
> texto puro) e não mostra os dados do firmware v1. Para acompanhar o
> firmware atual, use `mosquitto_sub` ou o MQTTX. O painel da N3 será
> servido pelo backend.

O diretório `app/` contém o painel web (um único `index.html`, sem
build), que roda no mesmo servidor do broker e mostra em tempo real
quais vagas estão livres. Ele se conecta ao broker via MQTT sobre
WebSocket, assina `garagem/vaga/#` e, graças ao flag *retained*, já
exibe o último estado de cada vaga ao abrir. A exibição das tags RFID
(`garagem/rfid/#`) ainda será adicionada.

**Como abrir**

- No servidor, sirva a pasta: `python3 -m http.server -d app 8080` e
  acesse `http://<IP-do-servidor>:8080` de qualquer aparelho da rede.
  Também dá para abrir `app/index.html` direto no navegador.
- Por padrão conecta em `wss://broker.hivemq.com:8884/mqtt`.

**Configuração**

Em **Configurações** (no rodapé) dá para trocar o broker e o tópico
base; os valores ficam salvos no navegador. Também é possível passar
pela URL:

```
index.html?broker=ws://192.168.1.100:9001&base=garagem
```

**Vagas sem dados:** se o ESP32 perde a conexão, o broker publica
`offline` no status dele (Last Will) e as vagas aparecem em cinza como
"Sem dados", fora da contagem de livres. Os LEDs apagam ao mesmo tempo,
porque o ESP32 sem broker não mostra estado. O broker detecta a queda
pelo *keepalive* do MQTT, o que leva cerca de 20 segundos.

**Com Mosquitto local:** use `ws://<IP-do-servidor>:9001` como broker.
O broker local exige usuário e senha, que o painel da N1 não envia.
Se a página for servida por `https`, o navegador exige `wss://`.

## App do motorista

App web que roda no celular de cada motorista: mostra as vagas livres
e destaca a vaga do motorista quando a tag dele é lida. **Ainda não implementado** — ver
[ADR 0001](docs/adr/0001-rfid-e-app-motorista.md). Na demonstração, o
celular precisa estar no mesmo Wi-Fi do servidor e alcançar a porta
9001.

## Adicionar vagas

Cada vaga tem um **número único na garagem**, definido explicitamente
no sketch (a ordem no array não importa). Para uma nova vaga,
acrescente uma linha em `vagas[]` com o número e os pinos do HC-SR04,
do RC522 (SS) e do LED RGB:

```cpp
Vaga vagas[] = {
  {1, 25, 34, 5, 32, 33},  // {numero, trig, echo, rfidSS, ledVermelho, ledVerde}
  {2, 26, 35, 4, 27, 13},
};
```

O painel não precisa de alteração; a vaga aparece sozinha.

**Remover uma vaga:** tire-a do sketch e apague o estado retido no
broker:
`mosquitto_pub -h <broker> -t garagem/v1/<deviceId>/vaga/<n>/estado -n -r`.

**Limites por ESP32:** cada vaga usa 5 GPIOs (Trig, Echo, SS, R, G) além
dos 4 do SPI compartilhado. Com o mapa atual sobram poucos pinos livres
(21, 36, 39, 16, 17); uma terceira vaga caberia só com ajustes. Acima
disso, use mais ESP32 — cada um com números de vaga **diferentes** — ou
LEDs endereçáveis (WS2812).

## Simulação no Wokwi

O projeto pode ser executado sem hardware físico no simulador
[Wokwi](https://wokwi.com):

- Use a rede `Wokwi-GUEST` (senha vazia) e um broker público. No editor
  do Wokwi, crie uma aba `secrets.h` (botão **+** ao lado das abas →
  *New file*) com `#define WIFI_SSID "Wokwi-GUEST"` e
  `#define WIFI_PASSWORD ""`.
- Cada HC-SR04 possui um controle deslizante que simula a distância
  medida — arraste-o para abaixo de 25 cm para simular um veículo
  estacionando (o estado muda após ~3 s de leituras confirmadas). Para
  simular falha no sensor, desconecte o fio do Echo: após ~5 s a vaga
  vira `erro`.
- Observe os LEDs alternarem entre verde e vermelho conforme o sensor
  confirma cada estado.
- O Wokwi não simula o RC522: o monitor serial mostra
  `LEITOR NAO RESPONDE` e o resto do firmware segue funcionando.
- Acompanhe o resultado no monitor serial e em um cliente MQTT.

## Estrutura do repositório

```
.
├── app/
│   └── index.html               # Painel de configuração e monitoramento (MQTT via WebSocket)
├── docs/
│   ├── adr/
│   │   ├── 0001-rfid-e-app-motorista.md  # Decisão: RFID + ultrassônico, ESP32 único, app do motorista
│   │   ├── 0002-troca-sensor-de-peso-por-garagem.md
│   │   └── 0003-firmware-v1-fila-de-eventos.md  # Contrato v1, fila de eventos offline, leitor sob o piso
│   ├── fluxograma-firmware.md   # Fluxogramas do firmware e dos subscribers
│   ├── mapa-de-pinos.md         # Ligações do ESP32
│   └── IOT_24_08.pdf            # Documentação da disciplina
├── src/
│   └── garagem/
│       ├── garagem.ino          # Firmware único (HC-SR04 + RC522 + LED RGB)
│       ├── tipos.h              # Tipos do firmware (estado, evento da fila)
│       └── secrets.example.h    # Modelo do Wi-Fi (copiar para secrets.h)
├── .gitignore                   # Ignora os secrets.h e o .env
├── broker/
│   ├── mosquitto.conf           # Configuração do broker (Mosquitto 2.1)
│   ├── acl                      # Quem pode publicar/assinar o quê
│   └── iniciar.sh               # Gera as senhas a partir do .env e sobe o broker
├── compose.yaml                 # docker compose: broker (backend e banco nas próximas etapas)
├── .env.example                 # Modelo das senhas do broker (copiar para .env)
└── README.md                    # Este arquivo
```

O sketch fica numa pasta com o mesmo nome do arquivo `.ino`, como a
Arduino IDE exige. Para abrir, use **Arquivo → Abrir** e escolha
`src/garagem/garagem.ino`.

## Roadmap

- [x] Indicadores visuais (LED por vaga) como atuadores locais
- [x] Painel web de vagas livres
- [x] Identificação de carros por RFID (firmware)
- [x] Firmware no contrato MQTT v1 (JSON, QoS 1, NTP, fila de eventos offline, LED com regra local)
- [ ] Reserva de vaga, comandos (`cmd` / `ack`) e configuração em NVS
- [ ] Exibir tags RFID no painel
- [ ] App do motorista
- [ ] Definir privilégios dos carros com tag (ADR futuro)
- [x] Autenticação e ACL no Mosquitto (Docker Compose, Mosquitto 2.1)
- [ ] TLS no MQTT (avaliar; CP12, R16)
