# Fluxograma de firmware

Um único ESP32 opera as duas vagas: lê os 2 HC-SR04 e os 2 RC522,
publica no broker e aciona os 2 LEDs RGB a partir do que assina do broker
(ver [ADR 0001](adr/0001-rfid-e-app-motorista.md)).

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

## Visão geral (pub/sub)

```mermaid
flowchart LR
    CARRO([Carro na vaga]) -->|eco| HC["HC-SR04 ×2"]
    TAG([Tag RFID<br/>opcional]) -->|aproxima| RC522["RC522 ×2"]

    subgraph ESP["ESP32 único (vagas 1 e 2)"]
        US[Módulo ultrassônico]
        RF[Módulo RFID]
        LED[Módulo LEDs]
    end

    HC -->|Trig/Echo| US
    RC522 -->|SPI| RF
    LED --> LEDS["LED RGB ×2<br/>canais R e G"]

    subgraph SRV[Servidor]
        BROKER[(Mosquitto)]
        PAINEL[Painel de configs<br/>HTML]
    end

    US -->|"garagem/vaga/N<br/>livre | ocupada | erro (retained)"| BROKER
    RF -->|"garagem/rfid/N<br/>UID da tag (evento)"| BROKER
    ESP -.->|"garagem/no/ID/status<br/>online | offline (LWT)"| BROKER

    BROKER -->|"garagem/vaga/#"| LED
    BROKER -->|"garagem/vaga/#<br/>garagem/rfid/#"| PAINEL
    BROKER -->|"garagem/vaga/#<br/>garagem/rfid/#"| APP[App do motorista<br/>celular]
```

## Firmware do ESP32

Loop único e não bloqueante. Os três módulos rodam em sequência a cada
volta; o RFID nunca lê nem altera o estado da vaga, e os LEDs só mudam
pelo que chega do broker.

```mermaid
flowchart TD
    A([Liga ESP32]) --> B["Configura pinos:<br/>Trig/Echo ×2, LED RGB ×2 (R e G, apagados),<br/>SPI + RC522 ×2"]
    B --> C[Gera ID do nó a partir do MAC]
    C --> D[Conecta Wi-Fi]
    D --> E{Conectado ao broker?}
    E -- não --> E1[Apaga todos os LEDs<br/>estado desconhecido]
    E1 --> F["connect() com Last Will<br/>garagem/no/ID/status = offline"]
    F --> G{Sucesso?}
    G -- não --> H[Espera 2 s] --> E
    G -- sim --> I["Publica status = online<br/>e vagas = 1,2 (retained)"]
    I --> I1["Assina garagem/vaga/#"]
    I1 --> I2[Marca as 2 vagas como pendentes] --> E
    E -- sim --> K["client.loop()<br/>(entrega mensagens → callback dos LEDs)"]
    K --> US0[[Ultrassônico]]
    US0 --> RF0[[RFID]]
    RF0 --> E
```

### Módulo ultrassônico (a cada 1 s, vaga 1 e depois vaga 2)

Mesma lógica do antigo `sensor.ino`; os dois sensores disparam um de
cada vez para não interferirem.

```mermaid
flowchart TD
    A{Passou 1 s?} -- não --> Z([Volta ao loop])
    A -- sim --> B[Para cada vaga N = 1, 2]
    B --> M[Mede distância do HC-SR04 da vaga N]
    M --> N{Leitura válida?}
    N -- não --> N1{5 falhas seguidas?}
    N1 -- sim --> N2[Estado = erro<br/>pendente = true] --> T
    N1 -- não --> T
    N -- sim --> O["Histerese 25/35 cm<br/>+ 3 leituras iguais"]
    O --> O1{Estado mudou?}
    O1 -- sim --> P[pendente = true] --> T
    O1 -- não --> T
    T{pendente?}
    T -- sim --> U["publish garagem/vaga/N (retained)<br/>sucesso → pendente = false"] --> X
    T -- não --> X
    X{Última vaga?}
    X -- não --> B
    X -- sim --> Z
```

### Módulo RFID (a cada volta, RC522 da vaga 1 e depois da vaga 2)

Publica toda leitura; não sabe nem decide o estado da vaga.

```mermaid
flowchart TD
    A[Para cada leitor N = 1, 2] --> B[Seleciona RC522 N<br/>pino SS]
    B --> L{Tag presente?}
    L -- não --> X
    L -- sim --> M[Lê UID]
    M --> N{Mesma tag lida neste<br/>leitor há menos de X s?}
    N -- sim --> X
    N -- não --> O["publish garagem/rfid/N<br/>UID (não retained)"]
    O --> P{Publicou?}
    P -- sim --> X
    P -- não --> Q[Guarda leitura e tenta<br/>de novo na próxima volta] --> X
    X{Último leitor?}
    X -- não --> A
    X -- sim --> Z([Volta ao loop])
```

### Módulo LEDs (callback MQTT)

Chamado pelo `client.loop()` quando chega mensagem em `garagem/vaga/#`,
inclusive as publicadas pelo próprio ESP32. Ignora `garagem/rfid/#`.

```mermaid
flowchart TD
    A([Mensagem em garagem/vaga/N]) --> B{Vaga N é<br/>deste ESP32?}
    B -- não --> Z([Fim])
    B -- sim --> C{Payload}
    C -- livre --> C1[RGB: G ON, R OFF<br/>verde] --> Z
    C -- ocupada --> C2[RGB: R ON, G OFF<br/>vermelho] --> Z
    C -- "erro / vazio" --> C3[RGB apagado] --> Z
```

## Subscribers web (painel no servidor e app no celular)

Correlação entre tag e ocupação acontece aqui, não no firmware.

```mermaid
flowchart TD
    A([Abre página]) --> B[Lê broker/tópico base<br/>de localStorage ou URL]
    B --> C["Conecta via WebSocket<br/>(ws://IP-do-servidor:9001 ou wss://)"]
    C --> D{Conectou?}
    D -- não --> E[Mostra desconectado<br/>tenta de novo] --> C
    D -- sim --> F["Assina garagem/vaga/#,<br/>garagem/no/# e garagem/rfid/#"]
    F --> G[Recebe retained:<br/>estado atual de cada vaga]
    G --> H{Nova mensagem}
    H -- "vaga/N" --> I[Atualiza vaga livre/ocupada/sem dados]
    H -- "no/ID/status offline" --> K[Vagas 1 e 2 = sem dados]
    H -- "rfid/N" --> J{Vaga N ocupada<br/>ou fica ocupada em X s?}
    J -- sim --> J1[App: destaca vaga do motorista<br/>Painel: mostra UID na vaga]
    J -- não --> J2[Painel: registra leitura<br/>sem carro confirmado]
    I --> H
    K --> H
    J1 --> H
    J2 --> H
```
