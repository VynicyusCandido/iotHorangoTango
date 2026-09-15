# iotHorangoTango

Sistema IoT de monitoramento de vagas de estacionamento, usando
sensores ultrassônicos, ESP32 e comunicação via MQTT.

## Sobre o projeto

Este repositório contém o projeto desenvolvido para a disciplina de
Internet das Coisas (N1) da Universidade Católica de Santa Catarina. O
sistema monitora em tempo real a ocupação das vagas de uma garagem
inteligente: sensores ultrassônicos HC-SR04 detectam a presença de
veículos em cada vaga, um ESP32 processa as leituras e publica o estado
(livre/ocupada) em um broker MQTT. Dessa forma, motoristas podem
consultar previamente quais vagas estão disponíveis antes de entrar no
estacionamento, e indicadores visuais orientam quem já está circulando
pela garagem.

**Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
Angelico, Vynicyus Candido.

## Como funciona

1. **Leitura dos sensores** — o ESP32 dispara cada sensor ultrassônico
   HC-SR04 e mede a distância até o objeto à frente (o chão da vaga, ou
   um veículo estacionado).
2. **Processamento local** — a distância medida é comparada com um
   limiar. Abaixo do limiar, há um veículo ocupando a vaga; acima, a
   vaga está livre.
3. **Detecção de mudança de estado** — o ESP32 só age quando o estado
   de uma vaga muda (livre → ocupada ou vice-versa), evitando tráfego
   desnecessário na rede.
4. **Publicação MQTT** — a cada mudança, o novo estado da vaga é
   publicado em um tópico próprio no broker MQTT.
5. **Consumo dos dados** — clientes assinantes (aplicativo dos
   motoristas, painel de monitoramento) recebem as atualizações em
   tempo real e exibem quais vagas estão disponíveis.

Durante os testes, todas as leituras também podem ser acompanhadas pelo
monitor serial do ESP32.

## Arquitetura

HC-SR04 (vaga 1) ──┐
HC-SR04 (vaga 2) ──┼──► ESP32 (processamento) ──► Broker MQTT ──► App / Painel
HC-SR04 (vaga N) ──┘ (publisher) (subscribers)


- **Sensores:** sensores ultrassônicos HC-SR04 (um por vaga)
- **Nó de processamento (publisher):** ESP32
- **Broker:** HiveMQ (público) / Mosquitto
- **Subscribers:** aplicativo dos motoristas, painel de monitoramento

## Hardware necessário

- 1× ESP32 (DevKit v1 ou equivalente)
- N× sensores ultrassônicos HC-SR04 (um por vaga monitorada)
- Protoboard e jumpers
- Para hardware físico: divisor de tensão (resistores 1 kΩ + 2 kΩ) em
  cada pino Echo, pois o HC-SR04 opera em 5 V e o GPIO do ESP32 tolera
  apenas 3,3 V

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

### 2. Montar o circuito

Conecte cada HC-SR04 ao ESP32 (VCC em 5 V, GND, e os pinos Trig e Echo
nos GPIOs definidos no código). Em hardware físico, insira o divisor de
tensão no pino Echo de cada sensor. Na simulação, a ligação é direta.

### 3. Instalar as dependências

Na Arduino IDE, abra o gerenciador de bibliotecas e instale
`PubSubClient` (Nick O'Leary). Confirme que a placa ESP32 está
selecionada em **Ferramentas → Placa**.

### 4. Configurar credenciais e broker

No início do sketch, ajuste os parâmetros de rede e MQTT:

```cpp
const char* ssid        = "SUA_REDE";
const char* password    = "SUA_SENHA";
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;
```

Para simulação no Wokwi, use `ssid = "Wokwi-GUEST"` e `password = ""`.

### 5. Ajustar o limiar de detecção

Defina no código a distância-limite que separa "livre" de "ocupada",
conforme a altura de montagem do sensor sobre a vaga:

```cpp
const float LIMIAR_CM = 30.0;
```

### 6. Compilar e carregar

Conecte o ESP32 via USB, selecione a porta correta em **Ferramentas →
Porta** e clique em **Upload**. Abra o monitor serial (115200 baud)
para acompanhar as leituras.

### 7. Monitorar via MQTT

Conecte-se ao mesmo broker com um cliente MQTT para acompanhar as
publicações:

- **Cliente web:** MQTTX Web (https://mqttx.app/web) ou HiveMQ Web
  Client
- **Host:** `broker.hivemq.com` — **Porta:** `8884` (WebSocket + SSL,
  para páginas https) ou `8000` (WebSocket sem SSL, para páginas http)
- Assine o tópico das vagas (ex.: `fnd/garagem/vaga/#`) para ver os
  estados chegando em tempo real.

## Simulação no Wokwi

O projeto pode ser executado sem hardware físico no simulador
[Wokwi](https://wokwi.com):

- Use a rede `Wokwi-GUEST` (senha vazia) e um broker público.
- Cada HC-SR04 possui um controle deslizante que simula a distância
  medida — arraste-o para abaixo do limiar para simular um veículo
  estacionando.
- Acompanhe o resultado no monitor serial e no cliente MQTT.

## Estrutura do repositório

.
├── docs/ # Documentação, diagramas e especificações do projeto
├── src/ # Código-fonte do ESP32 (sketch .ino)
└── README.md # Este arquivo


## Roadmap

- [ ] Indicadores visuais (LEDs verde/vermelho) por vaga como atuadores
      locais
- [ ] Aplicativo para os motoristas consultarem as vagas disponíveis
- [ ] Painel de monitoramento consolidado da garagem
