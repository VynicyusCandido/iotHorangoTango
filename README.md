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

Um ESP32 cuida das duas vagas (`src/garagem/garagem.ino`). O firmware
tem três partes, que só se comunicam pelo broker MQTT:

**Ultrassônico (publisher) — estado da vaga**

1. **Leitura dos sensores** — a cada 1 s o ESP32 dispara o HC-SR04 de
   cada vaga, um de cada vez (para um não captar o eco do outro), e
   mede a distância até o objeto à frente (o chão da vaga, ou um
   veículo estacionado).
2. **Processamento local** — a distância medida é comparada com dois
   limiares (histerese): abaixo de 25 cm a vaga passa a ocupada, acima
   de 35 cm passa a livre, e entre os dois mantém o estado atual. Isso
   evita que uma leitura oscilando perto do limite fique trocando o
   estado.
3. **Confirmação da mudança** — o estado só muda depois de 3 leituras
   seguidas concordando (cerca de 3 s), o que filtra ruído e alguém
   passando na frente do sensor. O ESP32 só publica quando o estado
   confirmado muda, evitando tráfego desnecessário na rede.
   **Falha no sensor:** leituras sem eco ou fora da faixa do HC-SR04
   (2–400 cm) são descartadas, nunca contam como "livre". Após 5
   seguidas, a vaga é publicada como `erro`; o painel a mostra como
   "Sem dados" (falha no sensor) e o LED apaga.
4. **Publicação MQTT** — a cada mudança, o novo estado (`livre` /
   `ocupada` / `erro`) é publicado no tópico da vaga (ex.:
   `garagem/vaga/1`) com o flag *retained*, para que qualquer assinante
   receba o último estado assim que conectar. O estado também é
   republicado ao ligar e a cada reconexão ao broker.
5. **Status do nó** — ao conectar, o ESP32 publica `online` em
   `garagem/no/<id>/status` e a lista das vagas que monitora (`1,2`) em
   `garagem/no/<id>/vagas`. Se ele cair, o broker publica `offline`
   sozinho (Last Will), e o painel passa a mostrar as vagas como
   "sem dados".

**RFID (publisher) — identificação do carro**

6. **Leitura das tags** — a cada 100 ms o ESP32 consulta o RC522 de
   cada vaga. Cada tag lida tem o UID publicado em `garagem/rfid/N`
   (ex.: `04a1b2c3`), sem *retained* — é um evento. A mesma tag lida de
   novo no mesmo leitor em menos de 3 s é ignorada.
7. **Independente da vaga** — o RFID não lê nem altera o estado da
   vaga. Cruzar a tag com a vaga ocupada (ex.: tag lida antes de o
   ultrassônico confirmar o carro) é papel do painel e do app do
   motorista.
8. **Leitor travado** — a cada 5 s o ESP32 confere se os RC522 respondem
   e, se algum parou, reinicia os dois (o pino de reset é comum).

**LEDs (subscriber) — indicação na vaga**

9. **Assinatura MQTT** — o ESP32 assina `garagem/vaga/#`, inclusive o
   que ele mesmo publica, e ao conectar já recebe o estado retido de
   cada vaga. Assim o LED mostra exatamente o que o broker distribuiu
   para todos.
10. **Acionamento** — LED RGB verde (livre) ou vermelho (ocupada).
    Estado `erro`, desconhecido ou **sem conexão com o broker**: LED
    apagado, em vez de mostrar um estado que pode estar errado.

Outros assinantes (painel de configurações e monitoramento, app do
motorista) consomem os mesmos tópicos em paralelo. Durante os testes,
todas as leituras e acionamentos também podem ser acompanhados pelo
monitor serial do ESP32.

## Arquitetura

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

| Tópico                   | Conteúdo                                 | Retained | Quem publica | Quem assina           |
|--------------------------|------------------------------------------|:--------:|--------------|-----------------------|
| `garagem/vaga/N`         | `livre` / `ocupada` / `erro`             | sim      | ESP32        | LEDs, painel, app     |
| `garagem/rfid/N`         | UID da tag, ex.: `04a1b2c3`              | não      | ESP32        | painel, app           |
| `garagem/no/<id>/status` | `online` / `offline` (Last Will)         | sim      | ESP32/broker | painel                |
| `garagem/no/<id>/vagas`  | vagas do nó, ex.: `1,2`                  | sim      | ESP32        | painel                |

- **Broker:** Mosquitto no servidor (ou HiveMQ público para testes sem
  RFID)
- **Painel de configurações e monitoramento:** `app/index.html`, servido
  pelo mesmo servidor do broker
- **App do motorista:** roda no celular de cada motorista (em
  desenvolvimento)

Fluxogramas do firmware em
[docs/fluxograma-firmware.md](docs/fluxograma-firmware.md).

> Em um broker público como o HiveMQ, os tópicos `garagem/#` são
> compartilhados com qualquer pessoa — inclusive os UIDs das tags. Use o
> Mosquitto local sempre que o RFID estiver ligado. Se precisar do
> broker público, use um prefixo único (ex.: `horangotango/garagem`),
> idêntico no firmware e no painel.

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
  - `PubSubClient` (Nick O'Leary) — comunicação MQTT
  - `MFRC522` (GithubCommunity) — leitores RFID
  - `WiFi` e `SPI` (nativas do ESP32)
- No Windows, se a porta COM não aparecer, instale o driver CP210x da
  Silicon Labs

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

Na Arduino IDE, abra o gerenciador de bibliotecas e instale
`PubSubClient` (Nick O'Leary) e `MFRC522` (GithubCommunity). Confirme
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

**MQTT:** no início do sketch, ajuste o broker:

```cpp
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;
```

O `mqtt_client_id` é gerado automaticamente a partir do MAC do ESP32
(`esp32-garagem-<mac>`). O prefixo de tópicos (`garagem`) deve ser
**idêntico** no sketch e no painel. Para simulação no Wokwi, use
`WIFI_SSID "Wokwi-GUEST"` e `WIFI_PASSWORD ""` no `secrets.h`.

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
(`RFID vaga N: leitor OK` ou `LEITOR NAO RESPONDE`), e depois cada
leitura, tag e mudança de LED.

### 7. Monitorar via MQTT

Conecte-se ao mesmo broker com um cliente MQTT para acompanhar as
publicações:

- **Cliente web:** MQTTX Web (https://mqttx.app/web) ou HiveMQ Web
  Client
- **Host:** `broker.hivemq.com` — **Porta:** `8884` (WebSocket + SSL,
  para páginas https) ou `8000` (WebSocket sem SSL, para páginas http)
- Assine `garagem/#` para ver estados das vagas e tags chegando em
  tempo real.

## Broker local com Mosquitto (passo a passo)

Por padrão o sketch publica/assina no broker **público**
`broker.hivemq.com`. Para usar o broker **próprio** rodando no servidor
(Mosquitto), siga os passos abaixo.

> **Pré-requisito:** este modo exige **ESP32 físico** na mesma rede Wi-Fi
> do servidor que roda o Mosquitto. O ESP32 simulado no Wokwi alcança
> só a internet pública, **não** enxerga o `localhost` nem a sua rede
> local — para continuar no Wokwi, mantenha o broker público.

### 1. Instalar o Mosquitto

- **Windows:** instalador oficial em
  [mosquitto.org/download](https://mosquitto.org/download/)
- **Linux (Debian/Ubuntu):** `sudo apt install mosquitto mosquitto-clients`
- **macOS:** `brew install mosquitto`

Os pacotes `-clients` / `mosquitto-clients` trazem os utilitários
`mosquitto_pub` e `mosquitto_sub`, usados nos testes dos passos 5 e 8.

### 2. Descobrir o IP do servidor na rede local

O ESP32 precisa do **IP da máquina** do broker na LAN — não use
`localhost` nem `127.0.0.1` (para o ESP32, isso apontaria para ele
mesmo).

- **Windows:** `ipconfig` → campo **Endereço IPv4** (ex.: `192.168.1.100`)
- **Linux/macOS:** `ip addr` (ou `ifconfig`) → endereço da interface Wi-Fi

Anote esse IP; ele será usado nos passos 5, 6 e 8. Reserve esse IP no
roteador (reserva DHCP): se ele mudar, o ESP32 precisa ser regravado.

### 3. Subir o broker

Na pasta do repositório (onde está o `mosquitto.conf`), execute:

```bash
mosquitto -c mosquitto.conf -v
```

O `mosquitto.conf` incluído já deixa o broker pronto para a rede:

```conf
listener 1883 0.0.0.0     # escuta em todas as interfaces (não só localhost)
listener 9001 0.0.0.0     # WebSocket, usado pelo painel e pelo app
protocol websockets
allow_anonymous true      # aceita conexão sem usuário/senha (testes/faculdade)
log_type all              # log detalhado...
log_dest stdout           # ...impresso no terminal
```

O modo verboso (`-v`) imprime cada `CONNECT`, `SUBSCRIBE` e `PUBLISH` no
terminal — deixe essa janela aberta; é onde você vê a comunicação
acontecendo. Sem esse `.conf`, o Mosquitto 2.x só escuta em `localhost`
e recusa conexões anônimas, e o ESP32 não conseguiria conectar.

> `allow_anonymous true` serve para testes: qualquer aparelho na rede
> pode ler os UIDs e publicar tags falsas. Antes de as tags darem
> privilégios, configure usuário/senha e ACL no Mosquitto (ver ADR 0001).

### 4. Liberar as portas no firewall

O firewall do sistema costuma bloquear conexões de entrada. Libere a
porta **1883** (ESP32) e a **9001** (painel e app):

- **Windows:** Firewall do Windows → Regras de Entrada → Nova regra →
  Porta → TCP 1883, 9001 → Permitir
- **Linux (ufw):** `sudo ufw allow 1883/tcp && sudo ufw allow 9001/tcp`

### 5. Testar o broker na rede (antes de mexer no ESP32)

De **outro** computador na mesma rede, confirme que o broker está
acessível (troque o IP pelo do passo 2). Em dois terminais:

```bash
# terminal 1 — assina
mosquitto_sub -h 192.168.1.100 -t "garagem/#" -v

# terminal 2 — publica
mosquitto_pub -h 192.168.1.100 -t "garagem/vaga/1" -m "ocupada" -r
```

Se a mensagem aparecer no terminal 1, o broker está no ar e visível na
rede — o ESP32 também vai conseguir conectar. Se não aparecer, revise os
passos 2 a 4 (IP, `mosquitto.conf`, firewall) antes de seguir.

### 6. Apontar o sketch para o broker local

Altere o `mqtt_server` em `src/garagem/garagem.ino`. A porta continua
`1883`:

```cpp
const char* mqtt_server = "192.168.1.100"; // IP do servidor com Mosquitto (passo 2)
const int   mqtt_port   = 1883;
```

E troque a rede do Wokwi pela rede real no `secrets.h` — o ESP32 e o
servidor têm que estar na **mesma rede**:

```cpp
#define WIFI_SSID     "SUA_REDE"
#define WIFI_PASSWORD "SUA_SENHA"
```

### 7. Compilar e carregar no ESP32 físico

**Ferramentas → Porta** correta → **Upload**. Abra o monitor serial
(115200 baud).

### 8. Verificar a comunicação

- Na janela do Mosquitto (passo 3), você deve ver o `CONNECT` do ESP32,
  o `SUBSCRIBE` em `garagem/vaga/#` e os `PUBLISH` a cada mudança de
  estado e a cada tag lida.
- Assine `mosquitto_sub -h <IP> -t "garagem/#" -v` para ver estados e
  tags; ao ocupar/liberar a vaga, o LED deve reagir, e ao aproximar uma
  tag o UID aparece em `garagem/rfid/N`.
- **Prova de que é IoT (passa pela rede):** com o sensor ainda medindo,
  derrube o broker (feche o Mosquitto) — o LED **apaga e para de
  atualizar**, mesmo estando na mesma placa que o sensor. Isso demonstra
  que o comando trafega pela rede/MQTT, e não por ligação direta entre
  sensor e LED.

### Se não conectar (checklist rápido)

- ESP32 e servidor estão na **mesma** rede Wi-Fi (2,4 GHz)?
- O `mqtt_server` é o **IP da LAN** (passo 2), não `localhost`?
- O Mosquitto está rodando **com** o `mosquitto.conf` (listener `0.0.0.0`,
  `allow_anonymous true`)?
- As portas **1883** e **9001** estão liberadas no firewall?
- O teste do passo 5, de outra máquina, funciona?

### Voltar ao broker público

Para rodar de novo no Wokwi, reverta o `mqtt_server` para
`broker.hivemq.com` e o Wi-Fi para `Wokwi-GUEST` (senha vazia) no
`secrets.h`.

## Painel de configuração e monitoramento

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
Se a página for servida por `https`, o navegador exige `wss://`.

## App do motorista

App web que roda no celular de cada motorista: mostra as vagas livres
(`garagem/vaga/#`) e destaca a vaga do motorista quando a tag dele é
lida (`garagem/rfid/#`). **Ainda não implementado** — ver
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
broker: `mosquitto_pub -h <broker> -t garagem/vaga/<n> -n -r`.

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
- Observe os LEDs alternarem entre verde e vermelho conforme os estados
  chegam do broker.
- O Wokwi não simula o RC522: o monitor serial mostra
  `LEITOR NAO RESPONDE` e o resto do firmware segue funcionando. Para
  testar o painel com tags, publique na mão:
  `mosquitto_pub -h broker.hivemq.com -t garagem/rfid/1 -m 04a1b2c3`.
- Acompanhe o resultado no monitor serial e em um cliente MQTT.

## Estrutura do repositório

```
.
├── app/
│   └── index.html               # Painel de configuração e monitoramento (MQTT via WebSocket)
├── docs/
│   ├── adr/
│   │   └── 0001-rfid-e-app-motorista.md  # Decisão: RFID + ultrassônico, ESP32 único, app do motorista
│   ├── fluxograma-firmware.md   # Fluxogramas do firmware e dos subscribers
│   ├── mapa-de-pinos.md         # Ligações do ESP32
│   └── IOT_24_08.pdf            # Documentação da disciplina
├── src/
│   └── garagem/
│       ├── garagem.ino          # Firmware único (HC-SR04 + RC522 + LED RGB)
│       └── secrets.example.h    # Modelo do Wi-Fi (copiar para secrets.h)
├── .gitignore                   # Ignora os secrets.h
├── mosquitto.conf               # Configuração do broker local
└── README.md                    # Este arquivo
```

O sketch fica numa pasta com o mesmo nome do arquivo `.ino`, como a
Arduino IDE exige. Para abrir, use **Arquivo → Abrir** e escolha
`src/garagem/garagem.ino`.

## Roadmap

- [x] Indicadores visuais (LED por vaga) como atuadores locais
- [x] Painel web de vagas livres
- [x] Identificação de carros por RFID (firmware)
- [ ] Exibir tags RFID no painel
- [ ] App do motorista
- [ ] Definir privilégios dos carros com tag (ADR futuro)
- [ ] Autenticação e ACL no Mosquitto antes de liberar privilégios
