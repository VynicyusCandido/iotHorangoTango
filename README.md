# iotHorangoTango

Sistema IoT de identificação de encomendas por medição de peso, usando
células de carga, ESP32 e comunicação via MQTT.

## Sobre o projeto

Este repositório contém o projeto desenvolvido para a disciplina de
Internet das Coisas (N1) da Universidade Católica de Santa Catarina. O
sistema identifica encomendas comparando o peso medido de um objeto com
um valor de referência: três células de carga acopladas a uma
plataforma medem o peso total, um potenciômetro permite ajustar o
limite (ou calibrar o offset) e um ESP32 processa as leituras e se
comunica com um broker MQTT. LEDs e um buzzer fornecem feedback
imediato sobre o resultado da pesagem.

**Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
Angelico, Vynicyus Candido.

## Como funciona

1. **Leitura dos sensores** — o ESP32 lê os três canais do módulo
   HX711 (um por célula de carga) e calcula o peso total.
2. **Leitura do potenciômetro** — o valor analógico é convertido em um
   peso limite (em gramas) ou usado como fator de calibração.
3. **Processamento local** — o peso total é comparado com o limite
   definido (potenciômetro ou valor recebido via MQTT).
4. **Atuação local** — LEDs e buzzer sinalizam o resultado:
   - Peso dentro do limite → LED verde + bipe curto (encomenda
     liberada).
   - Peso acima do limite → LED vermelho + bipe longo (encomenda
     recusada / erro).
5. **Publicação MQTT** — os dados de peso e a decisão são enviados para
   um broker MQTT.

Durante os testes, todas as leituras também podem ser acompanhadas pelo
monitor serial do ESP32.

## Estrutura do repositório

```
.
├── docs/          # Documentação, diagramas e especificações do projeto
└── README.md      # Este arquivo
```

