# iotHorangoTango

Sistema IoT de monitoramento de vagas de estacionamento, usando
sensores ultrassônicos, ESP32 e comunicação via MQTT.

## Sobre o projeto

Este repositório contém o projeto desenvolvido para a disciplina de
Internet das Coisas (N1) da Universidade Católica de Santa Catarina. O
sistema monitora em tempo real a ocupação das vagas de uma garagem
inteligente: sensores ultrassônicos HC-SR04 detectam a presença de
veículos em cada vaga, um ESP32 processa as leituras e publica o estado
(livre/ocupada) em um broker MQTT. Um segundo ESP32 assina esses estados
e aciona indicadores visuais (LEDs verde/vermelho) em cada vaga. Dessa
forma, motoristas podem consultar previamente quais vagas estão
disponíveis antes de entrar no estacionamento, e os LEDs orientam quem
já está circulando pela garagem.

**Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
Angelico, Vynicyus Candido.

## Como funciona

O sistema é dividido em dois nós ESP32 que se comunicam apenas pelo
broker MQTT, sem se conhecerem diretamente:

**Nó sensor (publisher)**

1. **Leitura dos sensores** — o ESP32 dispara cada sensor ultrassônico
   HC-SR04 e mede a distância até o objeto à frente (o chão da vaga, ou
   um veículo estacionado).
2. **Processamento local** — a distância medida é comparada com um
   limiar. Abaixo do limiar, há um veículo ocupando a vaga; acima, a
   vaga está livre.
3. **Detecção de mudança de estado** — o ESP32 só age quando o estado
   de uma vaga muda (livre → ocupada ou vice-versa), evitando tráfego
   desnecessário na rede.
4. **Publicação MQTT** — a cada mudança, o novo estado (`livre` /
   `ocupada`) é publicado no tópico da vaga (ex.: `garagem/vaga/1`) com
   o flag *retained*, para que qualquer assinante receba o último estado
   assim que conectar.

**Nó atuador (subscriber)**

5. **Assinatura MQTT** — o ESP32 atuador assina `garagem/vaga/#` e, ao
   conectar, já recebe o estado retido de cada vaga.
6. **Acionamento dos LEDs** — para cada mensagem, extrai o número da
   vaga do tópico e acende o LED verde (livre) ou vermelho (ocupada)
   correspondente.

Outros assinantes (aplicativo dos motoristas, painel de monitoramento)
podem consumir os mesmos tópicos em paralelo. Durante os testes, todas
as leituras e acionamentos também podem ser acompanhados pelo monitor
serial de cada ESP32.

## Arquitetura

```
HC-SR04 (vaga 1) ──┐
HC-SR04 (vaga 2) ──┼──► ESP32 sensor ──► Broker MQTT ──┬──► ESP32 atuador (LEDs verde/vermelho)
HC-SR04 (vaga N) ──┘    (publisher)                    └──► App / Painel (subscribers)
```

- **Sensores:** sensores ultrassônicos HC-SR04 (um por vaga)
- **Nó sensor (publisher):** ESP32 — publica em `garagem/vaga/N`
- **Broker:** HiveMQ (público) ou Mosquitto (local)
- **Nó atuador (subscriber):** ESP32 — assina `garagem/vaga/#` e aciona
  os LEDs
- **Outros subscribers:** aplicativo dos motoristas, painel de
  monitoramento

> Em um broker público como o HiveMQ, o tópico `garagem/vaga/#` é
> compartilhado com qualquer pessoa. Para evitar colisões com outros
> projetos, considere usar um prefixo único (ex.: `horangotango/garagem/vaga/#`)
> nos dois sketches — o prefixo precisa bater **exatamente** entre sensor
> e atuador.

## Hardware necessário

**Nó sensor**

- 1× ESP32 (DevKit v1 ou equivalente)
- N× sensores ultrassônicos HC-SR04 (um por vaga monitorada)
- Para hardware físico: divisor de tensão (resistores 1 kΩ + 2 kΩ) em
  cada pino Echo, pois o HC-SR04 opera em 5 V e o GPIO do ESP32 tolera
  apenas 3,3 V

**Nó atuador**

- 1× ESP32 (DevKit v1 ou equivalente)
- N× LED verde + N× LED vermelho (um par por vaga)
- N× resistor ~220–330 Ω (um por LED)

**Comum**

- Protoboard e jumpers

## Software necessário

- Arduino IDE (ou PlatformIO)
- Suporte à placa ESP32 instalado no gerenciador de placas
- Bibliotecas:
  - `PubSubClient` (comunicação MQTT)
  - `WiFi` (nativa do ESP32)

## Como executar

### 1. Clonar o repositório

```bash
git clone https://github.com/<usuario>/iotHorangoTango.git
cd iotHorangoTango
```

### 2. Montar os circuitos

**Sensor:** conecte cada HC-SR04 ao ESP32 (VCC em 5 V, GND, e os pinos
Trig e Echo nos GPIOs definidos no código — vaga 1 usa Trig 5 e Echo
18). Em hardware físico, insira o divisor de tensão no pino Echo de cada
sensor. Na simulação, a ligação é direta.

**Atuador:** conecte, para cada vaga, o LED verde e o LED vermelho aos
GPIOs definidos no código (vaga 1 usa 22 para o verde e 23 para o
vermelho), cada um com seu resistor em série para o GND.

### 3. Instalar as dependências

Na Arduino IDE, abra o gerenciador de bibliotecas e instale
`PubSubClient` (Nick O'Leary). Confirme que a placa ESP32 está
selecionada em **Ferramentas → Placa**.

### 4. Configurar credenciais e broker

No início de cada sketch, ajuste os parâmetros de rede e MQTT:

```cpp
const char* ssid        = "SUA_REDE";
const char* password    = "SUA_SENHA";
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;
```

Cada nó precisa de um `mqtt_client_id` **único** no broker (ex.:
`esp32-garagem-...` no sensor e `esp32-atuador-...` no atuador). O
prefixo de tópicos (`garagem/vaga/#`) deve ser **idêntico** nos dois
sketches. Para simulação no Wokwi, use `ssid = "Wokwi-GUEST"` e
`password = ""`.

### 5. Ajustar o limiar de detecção (nó sensor)

Defina no código a distância-limite que separa "livre" de "ocupada",
conforme a altura de montagem do sensor sobre a vaga:

```cpp
const float LIMIAR_CM = 30.0;
```

### 6. Compilar e carregar

Carregue o sketch do sensor em um ESP32 e o do atuador em outro
(**Ferramentas → Porta** correta para cada um) e clique em **Upload**.
Abra o monitor serial (115200 baud) para acompanhar leituras e
acionamentos.

### 7. Monitorar via MQTT

Conecte-se ao mesmo broker com um cliente MQTT para acompanhar as
publicações:

- **Cliente web:** MQTTX Web (https://mqttx.app/web) ou HiveMQ Web
  Client
- **Host:** `broker.hivemq.com` — **Porta:** `8884` (WebSocket + SSL,
  para páginas https) ou `8000` (WebSocket sem SSL, para páginas http)
- Assine `garagem/vaga/#` para ver os estados chegando em tempo real.

## Broker local com Mosquitto (passo a passo)

Por padrão os sketches publicam/assinam no broker **público**
`broker.hivemq.com`. Para usar um broker **próprio** rodando na sua
máquina (Mosquitto), siga os passos abaixo.

> **Pré-requisito:** este modo exige **ESP32 físico** na mesma rede Wi-Fi
> do computador que roda o Mosquitto. O ESP32 simulado no Wokwi alcança
> só a internet pública, **não** enxerga o `localhost` nem a sua rede
> local — para continuar no Wokwi, mantenha o broker público.

### 1. Instalar o Mosquitto

- **Windows:** instalador oficial em
  [mosquitto.org/download](https://mosquitto.org/download/)
- **Linux (Debian/Ubuntu):** `sudo apt install mosquitto mosquitto-clients`
- **macOS:** `brew install mosquitto`

Os pacotes `-clients` / `mosquitto-clients` trazem os utilitários
`mosquitto_pub` e `mosquitto_sub`, usados nos testes dos passos 5 e 9.

### 2. Descobrir o IP do computador na rede local

O ESP32 precisa do **IP da máquina** do broker na LAN — não use
`localhost` nem `127.0.0.1` (para o ESP32, isso apontaria para ele
mesmo).

- **Windows:** `ipconfig` → campo **Endereço IPv4** (ex.: `192.168.1.100`)
- **Linux/macOS:** `ip addr` (ou `ifconfig`) → endereço da interface Wi-Fi

Anote esse IP; ele será usado nos passos 5, 6 e 9.

### 3. Subir o broker

Na pasta do repositório (onde está o `mosquitto.conf`), execute:

```bash
mosquitto -c mosquitto.conf -v
```

O `mosquitto.conf` incluído já deixa o broker pronto para a rede:

```conf
listener 1883 0.0.0.0     # escuta em todas as interfaces (não só localhost)
allow_anonymous true      # aceita conexão sem usuário/senha (testes/faculdade)
log_type all              # log detalhado...
log_dest stdout           # ...impresso no terminal
```

O modo verboso (`-v`) imprime cada `CONNECT`, `SUBSCRIBE` e `PUBLISH` no
terminal — deixe essa janela aberta; é onde você vê a comunicação
acontecendo. Sem esse `.conf`, o Mosquitto 2.x só escuta em `localhost`
e recusa conexões anônimas, e o ESP32 não conseguiria conectar.

### 4. Liberar a porta 1883 no firewall

O firewall do sistema costuma bloquear conexões de entrada. Libere a
porta **1883**:

- **Windows:** Firewall do Windows → Regras de Entrada → Nova regra →
  Porta → TCP 1883 → Permitir
- **Linux (ufw):** `sudo ufw allow 1883/tcp`

### 5. Testar o broker na rede (antes de mexer no ESP32)

De **outro** computador na mesma rede, confirme que o broker está
acessível (troque o IP pelo do passo 2). Em dois terminais:

```bash
# terminal 1 — assina
mosquitto_sub -h 192.168.1.100 -t "garagem/vaga/#" -v

# terminal 2 — publica
mosquitto_pub -h 192.168.1.100 -t "garagem/vaga/1" -m "ocupada" -r
```

Se a mensagem aparecer no terminal 1, o broker está no ar e visível na
rede — o ESP32 também vai conseguir conectar. Se não aparecer, revise os
passos 2 a 4 (IP, `mosquitto.conf`, firewall) antes de seguir.

### 6. Apontar os dois sketches para o broker local

Altere o `mqtt_server` **nos dois sketches** (sensor **e** atuador — eles
precisam usar o mesmo broker). A porta continua `1883`:

```cpp
const char* mqtt_server = "192.168.1.100"; // IP do PC com Mosquitto (passo 2)
const int   mqtt_port   = 1883;
```

### 7. Ajustar o Wi-Fi nos dois sketches

O ESP32 e o computador do broker têm que estar na **mesma rede**. Troque
a rede do Wokwi pela sua rede real, nos dois sketches:

```cpp
const char* ssid     = "SUA_REDE";
const char* password = "SUA_SENHA";
```

### 8. Compilar e carregar nos ESP32 físicos

Carregue o sketch do sensor em um ESP32 e o do atuador em outro
(**Ferramentas → Porta** correta para cada um → **Upload**). Abra o
monitor serial (115200 baud) de cada um.

### 9. Verificar a comunicação

- Na janela do Mosquitto (passo 3), você deve ver `CONNECT` dos dois
  ESP32, o `SUBSCRIBE` do atuador em `garagem/vaga/#` e os `PUBLISH` do
  sensor a cada mudança de estado.
- Assine `mosquitto_sub -h <IP> -t "garagem/vaga/#" -v` para ver os
  estados; ao ocupar/liberar a vaga, o LED do atuador deve reagir.
- **Prova de que é IoT (passa pela rede):** com o sensor ainda medindo,
  derrube o broker (feche o Mosquitto) — o LED do atuador **para de
  atualizar**. Isso demonstra que o comando trafega pela rede/MQTT, e não
  por ligação direta entre os dispositivos.

### Se não conectar (checklist rápido)

- ESP32 e PC estão na **mesma** rede Wi-Fi?
- O `mqtt_server` é o **IP da LAN** (passo 2), não `localhost`?
- O Mosquitto está rodando **com** o `mosquitto.conf` (listener `0.0.0.0`,
  `allow_anonymous true`)?
- A porta **1883** está liberada no firewall?
- O teste do passo 5, de outra máquina, funciona?

### Voltar ao broker público

Para rodar de novo no Wokwi, reverta o `mqtt_server` para
`broker.hivemq.com` e o Wi-Fi para `Wokwi-GUEST` (senha vazia) nos dois
sketches.

## Simulação no Wokwi

O projeto pode ser executado sem hardware físico no simulador
[Wokwi](https://wokwi.com):

- Use a rede `Wokwi-GUEST` (senha vazia) e um broker público.
- No nó sensor, cada HC-SR04 possui um controle deslizante que simula a
  distância medida — arraste-o para abaixo do limiar para simular um
  veículo estacionando.
- No nó atuador, observe os LEDs alternarem entre verde e vermelho
  conforme os estados chegam.
- Acompanhe o resultado no monitor serial e em um cliente MQTT.

## Estrutura do repositório

```
.
├── docs/               # Documentação, diagramas e especificações do projeto
├── src/
│   ├── sensor/         # Sketch do nó sensor (publisher)
│   └── atuador/        # Sketch do nó atuador (LEDs, subscriber)
├── mosquitto.conf      # Configuração do broker local (opcional)
└── README.md           # Este arquivo
```

## Roadmap

- [x] Indicadores visuais (LEDs verde/vermelho) por vaga como atuadores
      locais
- [ ] Aplicativo para os motoristas consultarem as vagas disponíveis
- [ ] Painel de monitoramento consolidado da garagem
