# Mapa de pinos do ESP32

ESP32 DevKit de 30 pinos (módulo ESP32-WROOM-32, USB-C, conversor
USB-serial CP2102) operando as duas vagas: 2× HC-SR04, 2× RC522 e 2×
LED RGB catodo comum (ver [ADR 0001](adr/0001-rfid-e-app-motorista.md)).

## Placa

Vista de cima, antena para cima, USB-C embaixo. Rótulos como impressos
na placa; `←` marca os pinos usados neste projeto.

```
                 ┌─────── antena ───────┐
           EN  ●─┤                      ├─● D23  ← SPI MOSI
  (36)     VP  ●─┤                      ├─● D22  ← RST RC522
  (39)     VN  ●─┤                      ├─● TX0     (serial, não usar)
Echo 1 →  D34  ●─┤                      ├─● RX0     (serial, não usar)
Echo 2 →  D35  ●─┤                      ├─● D21     (reserva)
 R  v1 →  D32  ●─┤       ESP-32         ├─● D19  ← SPI MISO
 G  v1 →  D33  ●─┤                      ├─● D18  ← SPI SCK
Trig 1 →  D25  ●─┤                      ├─● D5   ← SS RC522 vaga 1
Trig 2 →  D26  ●─┤                      ├─● TX2  ← (17) B v2 opcional
 R  v2 →  D27  ●─┤                      ├─● RX2  ← (16) B v1 opcional
          D14  ●─┤                      ├─● D4   ← SS RC522 vaga 2
          D12  ●─┤                      ├─● D2      (LED azul da placa)
 G  v2 →  D13  ●─┤                      ├─● D15
          GND  ●─┤                      ├─● GND
          VIN  ●─┤        USB-C         ├─● 3V3  ← RC522 ×2
                 └──────────────────────┘
```

- Há **um** pino 3V3, **um** VIN e dois GND: distribua pelas trilhas
  de alimentação da protoboard (3V3, 5 V e GND em trilhas separadas).
- `TX2`/`RX2` são os GPIO 17/16; `VP`/`VN` são os GPIO 36/39.
- A placa ocupa quase toda a largura de uma protoboard de 830 furos,
  sobrando uma fileira de cada lado. Use duas protoboards lado a lado
  (uma fileira de pinos em cada) ou jumpers macho-fêmea direto nos
  pinos.
- No Windows, se a porta COM não aparecer, instale o driver CP210x da
  Silicon Labs.

## Resumo

| GPIO | Função              | Vaga | Componente / pino   | Direção |
|-----:|---------------------|:----:|---------------------|---------|
| 18   | SPI SCK             | 1, 2 | RC522 `SCK`         | saída   |
| 19   | SPI MISO            | 1, 2 | RC522 `MISO`        | entrada |
| 23   | SPI MOSI            | 1, 2 | RC522 `MOSI`        | saída   |
| 22   | Reset RFID          | 1, 2 | RC522 `RST`         | saída   |
| 5    | Seleção leitor 1    | 1    | RC522 `SDA` (SS)    | saída   |
| 4    | Seleção leitor 2    | 2    | RC522 `SDA` (SS)    | saída   |
| 25   | Trig                | 1    | HC-SR04 `Trig`      | saída   |
| 34   | Echo (via divisor)  | 1    | HC-SR04 `Echo`      | entrada |
| 26   | Trig                | 2    | HC-SR04 `Trig`      | saída   |
| 35   | Echo (via divisor)  | 2    | HC-SR04 `Echo`      | entrada |
| 32   | LED vermelho        | 1    | RGB `R`             | saída   |
| 33   | LED verde           | 1    | RGB `G`             | saída   |
| 27   | LED vermelho        | 2    | RGB `R`             | saída   |
| 13   | LED verde           | 2    | RGB `G`             | saída   |
| 16   | LED azul (opcional) | 1    | RGB `B`             | saída   |
| 17   | LED azul (opcional) | 2    | RGB `B`             | saída   |

Total: 14 GPIOs (16 com o azul).

## Ligações por componente

### RC522 (×2) — barramento SPI compartilhado

Os dois leitores dividem `SCK`, `MISO`, `MOSI` e `RST`; cada um tem o
seu `SDA` (SS). O firmware seleciona um leitor por vez.

| RC522      | Leitor vaga 1 | Leitor vaga 2 |
|------------|---------------|---------------|
| `3.3V`     | 3V3           | 3V3           |
| `GND`      | GND           | GND           |
| `SDA` (SS) | GPIO 5        | GPIO 4        |
| `SCK`      | GPIO 18       | GPIO 18       |
| `MOSI`     | GPIO 23       | GPIO 23       |
| `MISO`     | GPIO 19       | GPIO 19       |
| `RST`      | GPIO 22       | GPIO 22       |
| `IRQ`      | não ligado    | não ligado    |

> **Somente 3,3 V.** Ligar o RC522 em 5 V queima o leitor.

### HC-SR04 (×2)

| HC-SR04 | Sensor vaga 1                 | Sensor vaga 2                 |
|---------|-------------------------------|-------------------------------|
| `VCC`   | VIN (5 V do USB)              | VIN (5 V do USB)              |
| `GND`   | GND                           | GND                           |
| `Trig`  | GPIO 25                       | GPIO 26                       |
| `Echo`  | divisor → GPIO 34             | divisor → GPIO 35             |

Divisor de tensão em cada `Echo` (5 V → 3,3 V):

```
Echo ──[ 1 kΩ ]──┬──► GPIO 34 (ou 35)
                 │
              [ 2 kΩ ]
                 │
                GND
```

Com HC-SR04P (versão 3,3 V), alimente em 3V3 e ligue o `Echo` direto,
sem divisor.

### LED RGB catodo comum (×2)

| LED RGB          | LED vaga 1               | LED vaga 2               |
|------------------|--------------------------|--------------------------|
| Pino comum (`−`) | GND                      | GND                      |
| `R`              | 220 Ω → GPIO 32          | 220 Ω → GPIO 27          |
| `G`              | 68–100 Ω → GPIO 33       | 68–100 Ω → GPIO 13       |
| `B` (opcional)   | 68–100 Ω → GPIO 16       | 68–100 Ω → GPIO 17       |

Lógica: `HIGH` acende. Livre = `G`; ocupada = `R`; erro/sem dados =
apagado (ou `B`, se o azul for usado).

> **Resistor do verde menor que o do vermelho.** O verde e o azul de um
> LED RGB precisam de ~3,0 V para acender; com os 3,3 V do GPIO e
> 220 Ω, ficam quase apagados. Comece com 68–100 Ω e ajuste o brilho na
> bancada. Módulos RGB que já vêm com resistores dispensam esta tabela.
>
> **Anodo comum:** pino comum no 3V3 e lógica invertida (`LOW` acende).

### Alimentação

| Origem        | Alimenta                        |
|---------------|---------------------------------|
| USB → VIN 5 V | ESP32, 2× HC-SR04               |
| 3V3 do ESP32  | 2× RC522                        |
| GND           | comum a **todos** os componentes |

Consumo estimado: ~250 mA no total (ESP32 com Wi-Fi + 2× RC522 + 2×
HC-SR04 + LEDs), dentro de uma fonte USB 5 V / 1 A.

## Pinos evitados

| GPIO        | Motivo                                                        |
|-------------|---------------------------------------------------------------|
| 6–11        | ligados à memória flash; usar trava o ESP32                   |
| 1, 3        | Serial (USB) — usados pelo upload e pelo monitor serial       |
| 0, 2, 12, 15| strapping: definem o modo de boot; o 12 em nível alto no boot impede o ESP32 de iniciar |
| 14          | emite sinal ao ligar (LED piscaria no boot)                   |
| 36, 39      | só entrada, sem uso necessário (reserva para outro Echo)      |
| 21          | livre (reserva)                                               |

O GPIO 5 também é de strapping, mas é o SS padrão do SPI e tem pull-up
interno, que mantém o leitor desselecionado no boot — uso seguro.

## Constantes para o firmware

```cpp
// SPI (VSPI) compartilhado pelos dois RC522
const int PIN_SPI_SCK  = 18;
const int PIN_SPI_MISO = 19;
const int PIN_SPI_MOSI = 23;
const int PIN_RFID_RST = 22;  // comum aos dois leitores

// Cada vaga: {numero, trig, echo, rfidSS, ledVermelho, ledVerde}
//   {1, 25, 34, 5, 32, 33},
//   {2, 26, 35, 4, 27, 13},
```
