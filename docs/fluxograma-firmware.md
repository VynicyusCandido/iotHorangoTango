# Fluxograma de firmware

Um único ESP32 opera as duas vagas: lê os 2 HC-SR04 e os 2 RC522,
acende os 2 LEDs RGB pela regra local e publica no broker seguindo o
contrato MQTT v1 ([CONTRATOS.md](../CONTRATOS.md)). Recebe comandos do
backend (reserva e configuração). Mudanças de estado, tags lidas e
respostas a comandos passam por uma fila, que guarda o que acontece com
o sistema fora do ar (ver [ADR 0001](adr/0001-rfid-e-app-motorista.md),
[ADR 0003](adr/0003-firmware-v1-fila-de-eventos.md) e
[ADR 0004](adr/0004-comandos-reserva-e-nvs.md)).

```
            ┌──────────────── ESP32 (vagas 1 e 2) ─────────────────┐
HC-SR04 ×2 ─┤ ultrassônico ─┐                                       │
RC522   ×2 ─┤ RFID ─────────┼─► fila de eventos ─► publica (QoS 1) ─┼──┐
LED RGB ×2 ◄┤ LEDs ◄────────┘  (regra local, sem rede)              │  │
            └───────────────────────────────────────────────────────┘  │
                                                                       ▼
                                                  Servidor: Mosquitto ──► backend (N3)
```

## Visão geral

`{id}` = `deviceId` (ex.: `esp32-garagem-240ac4000110`, derivado do MAC).

```mermaid
flowchart LR
    CARRO([Carro na vaga]) -->|eco| HC["HC-SR04 ×2"]
    TAG([Tag RFID embaixo do carro<br/>opcional]) -->|passa sobre| RC522["RC522 ×2<br/>sob o piso"]

    subgraph ESP["ESP32 único (vagas 1 e 2)"]
        US[Módulo ultrassônico]
        RF[Módulo RFID]
        LED[Módulo LEDs<br/>regra local]
        CMD[Módulo comandos<br/>reserva · configurar]
        NVS[(NVS<br/>limiares · reserva)]
        FILA[(Fila de eventos<br/>até 64)]
        PUB[Publicação]
    end

    HC -->|Trig/Echo| US
    RC522 -->|SPI| RF
    US -->|estado| LED
    LED --> LEDS["LED RGB ×2<br/>R · G · B"]
    CMD -->|reservada| LED
    CMD <--> NVS
    CMD -->|ack + estado| FILA
    US -->|mudança de estado| FILA
    RF -->|tag lida| FILA
    FILA --> PUB

    subgraph SRV[Servidor]
        BROKER[(Mosquitto)]
    end

    PUB -->|"garagem/v1/{id}/vaga/N/estado<br/>JSON, QoS 1, retained"| BROKER
    PUB -->|"garagem/v1/{id}/rfid<br/>JSON, QoS 1"| BROKER
    PUB -->|"garagem/v1/{id}/telemetria<br/>JSON, QoS 0, a cada 10 s"| BROKER
    PUB -->|"garagem/v1/{id}/ack<br/>JSON, QoS 1"| BROKER
    PUB -.->|"garagem/v1/{id}/status<br/>online | offline (LWT)"| BROKER
    BROKER -->|"garagem/v1/{id}/cmd<br/>QoS 1"| CMD
    BE[Backend<br/>N3] -->|cmd| BROKER
    BROKER --> BE
```

## Firmware do ESP32

Laço único e sem bloqueio: sensores e LEDs rodam a cada volta, com ou
sem rede. A publicação só acontece com o broker conectado; antes disso,
os eventos esperam na fila.

```mermaid
flowchart TD
    A([Liga ESP32]) --> B["Configura pinos:<br/>Trig/Echo ×2, LED RGB ×2 (apagados),<br/>SPI + RC522 ×2 (ganho máximo)"]
    B --> B1["Carrega configuração da NVS<br/>(limiares, duração da reserva)"]
    B1 --> C[Gera deviceId e tópicos a partir do MAC]
    C --> D["Inicia Wi-Fi em segundo plano<br/>(reconexão automática)"]
    D --> D1["Prepara MQTT com Last Will<br/>status = offline (retained, QoS 1)"]
    D1 --> L([Loop])

    L --> R[["Rede<br/>(comandos chegam no mqtt.loop)"]]
    R --> US[[Ultrassônico — a cada 1 s]]
    US --> CK[[Checa RC522 — a cada 5 s]]
    CK --> RF[[RFID — a cada 100 ms]]
    RF --> RS[[Reservas: expirou?]]
    RS --> LD[[LEDs]]
    LD --> M{Broker conectado?}
    M -- não --> L
    M -- sim --> P[[Publicação]]
    P --> L
```

### Rede (sem bloqueio)

```mermaid
flowchart TD
    A{Wi-Fi conectado?} -- não --> Z([Volta ao loop])
    A -- sim --> N{"NTP já iniciado?"}
    N -- não --> N1["configTime (UTC)<br/>sincroniza em segundo plano"] --> B
    N -- sim --> B
    B{Broker conectado?} -- sim --> Z
    B -- não --> C{"Passaram 5 s desde<br/>a última tentativa?"}
    C -- não --> Z
    C -- sim --> D["connect() com o deviceId<br/>(usuário/senha se definidos)"]
    D --> E{Conectou?}
    E -- não --> Z
    E -- sim --> F["Assina garagem/v1/{id}/cmd (QoS 1)<br/>status pendente = true<br/>republicar estado das vagas = true"] --> Z
```

### Módulo ultrassônico (a cada 1 s, vaga 1 e depois vaga 2)

Os dois sensores disparam um de cada vez para não interferirem.

```mermaid
flowchart TD
    A[Para cada vaga N = 1, 2] --> M[Mede distância do HC-SR04 da vaga N]
    M --> V{Leitura válida?<br/>2–400 cm}
    V -- não --> V1{5 falhas seguidas?}
    V1 -- sim --> E1["Estado = erro<br/>(encerra reserva)"] --> EV
    V1 -- não --> X
    V -- sim --> O["Histerese 25/35 cm<br/>+ 3 leituras iguais"]
    O --> O1{Estado mudou?}
    O1 -- não --> X
    O1 -- sim --> O2["Ocupada: encerra reserva<br/>(carro chegou)"]
    O2 --> EV["Enfileira estado se o publicado mudou<br/>(vaga, estado, distância, momento)"]
    EV --> X{Última vaga?}
    X -- não --> A
    X -- sim --> Z([Volta ao loop])
```

### Módulo RFID (a cada 100 ms, RC522 da vaga 1 e depois da vaga 2)

Enfileira toda leitura; não sabe nem decide o estado da vaga.

```mermaid
flowchart TD
    A[Para cada leitor N = 1, 2] --> K{Leitor OK?}
    K -- não --> X
    K -- sim --> L{Tag presente?}
    L -- não --> X
    L -- sim --> M["Lê UID<br/>(hex maiúsculo)"]
    M --> N{Mesma tag lida neste<br/>leitor há menos de 3 s?}
    N -- sim --> X
    N -- não --> O["Enfileira evento RFID<br/>(vaga, UID, momento)"]
    O --> X{Último leitor?}
    X -- não --> A
    X -- sim --> Z([Volta ao loop])
```

A cada 5 s o firmware confere se os RC522 respondem. Se algum parou,
reinicia os dois (o pino de reset é comum).

### Módulo LEDs (regra local, a cada volta)

Segue o estado da vaga (sensor + reserva) no próprio ESP32, com ou sem
rede. Falha no sensor vence a reserva.

```mermaid
flowchart TD
    A([Estado da vaga N]) --> C{Estado}
    C -- livre --> C1[Verde] --> Z([Fim])
    C -- ocupada --> C2[Vermelho] --> Z
    C -- reservada --> C5[Azul] --> Z
    C -- erro --> C3["Vermelho piscando<br/>(nunca verde sem certeza)"] --> Z
    C -- "ainda sem leitura" --> C4[Apagado] --> Z
```

### Módulo comandos (callback do `mqtt.loop()`)

Não publica nada: as respostas entram na fila, na ordem
`aceito` → estado → `executado`.

```mermaid
flowchart TD
    A([Mensagem em garagem/v1/id/cmd]) --> B{"JSON válido<br/>com commandId?"}
    B -- não --> R1["ack rejeitado PAYLOAD_INVALIDO<br/>(commandId null)"] --> Z([Fim])
    B -- sim --> C{"commandId entre<br/>os últimos 16?"}
    C -- sim --> R2[ack rejeitado COMANDO_DUPLICADO] --> Z
    C -- não --> D{"v = 1?"}
    D -- não --> R3[ack rejeitado PAYLOAD_INVALIDO] --> Z
    D -- sim --> E{acao}
    E -- outra --> R4[ack rejeitado ACAO_DESCONHECIDA] --> Z
    E -- reservar --> F{"vaga e duracao_min<br/>válidas?"}
    F -- não --> R5["PARAMETRO_INVALIDO ou<br/>VAGA_INEXISTENTE"] --> Z
    F -- sim --> G{Estado da vaga}
    G -- "erro / sem leitura" --> R6[ack rejeitado VAGA_EM_ERRO] --> Z
    G -- "ocupada / reservada" --> R7[ack rejeitado VAGA_OCUPADA] --> Z
    G -- livre --> H["ack aceito → reserva (LED azul)<br/>→ estado reservada → ack executado"] --> Z
    E -- cancelar_reserva --> I{Vaga reservada?}
    I -- não --> R8[ack rejeitado SEM_RESERVA] --> Z
    I -- sim --> J["ack aceito → encerra reserva<br/>→ estado do sensor → ack executado"] --> Z
    E -- configurar --> K{"3 campos nas faixas?"}
    K -- não --> R9[ack rejeitado PARAMETRO_INVALIDO] --> Z
    K -- sim --> L["ack aceito → aplica e grava na NVS<br/>→ ack executado"] --> Z
```

A reserva expira pelo tempo (`duracao_min` ou o padrão configurado),
contado no ESP32 mesmo sem rede; ao expirar, a vaga volta ao estado do
sensor e o novo estado entra na fila.

### Fila de eventos

```mermaid
flowchart TD
    A([Novo evento:<br/>mudança de estado, tag ou ack]) --> B{Fila cheia?<br/>64 eventos}
    B -- sim --> B1["Descarta o mais antigo<br/>e registra no Serial"] --> C
    B -- não --> C[Adiciona no fim da fila]
    C --> Z([Fim])
```

O momento do evento é registrado quando ele acontece: horário real (se
o NTP já sincronizou) e uptime. Evento registrado antes da
sincronização ganha o horário real na publicação, calculado pelo uptime.
Se o relógio nunca sincronizou, vai `"ts": null` com o `uptime_ms`.

### Publicação (só com o broker conectado)

```mermaid
flowchart TD
    A{status pendente?} -- sim --> A1["publish status = online,<br/>firmware, vagas (retained, QoS 1)"] --> B
    A -- não --> B
    B{Fila vazia?} -- não --> C["Publica o evento mais antigo<br/>(QoS 1, espera o PUBACK)"]
    C --> D{Confirmado?}
    D -- não --> T
    D -- sim --> E["Remove da fila<br/>(até 5 por volta)"] --> B
    B -- sim --> R["Republica o estado atual das vagas<br/>marcadas (retained, QoS 1)"]
    R --> T{Passaram 10 s?}
    T -- sim --> T1["publish telemetria<br/>(QoS 0)"] --> Z([Volta ao loop])
    T -- não --> Z
```

Eventos de estado vão para `garagem/v1/{id}/vaga/N/estado` (retained);
tags para `garagem/v1/{id}/rfid`; respostas a comandos para
`garagem/v1/{id}/ack`. Como a fila sai em ordem, o último
estado publicado de cada vaga é sempre o atual.

## Assinantes

Na N3 quem assina os tópicos do dispositivo é o **backend**: valida cada
mensagem contra o contrato, grava no PostgreSQL e cruza a tag lida com a
vaga ocupada. Painel do operador e app do motorista falam só com a API
do backend.

O painel da N1 (`app/index.html`) ainda assina os tópicos antigos
(`garagem/vaga/#`, texto puro) e não mostra os dados do firmware v1.
