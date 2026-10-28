# ADR 0002 — Trocar a balança de encomendas (sensor de peso) pela garagem inteligente na N1

- **Status:** Aceito
- **Data da decisão:** 2026-09-14 (primeiro commit da garagem, `17ff467`)
- **Data do registro:** 2026-10-27 (registro retroativo)
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

A proposta inicial da N1 ([IOT_24_08.pdf](../IOT_24_08.pdf), commit
`7fc6dc0`, 26/08/2026) era um **sistema de identificação de encomendas
por peso**:

- 3 células de carga de 5 kg com HX711 sob uma plataforma rígida;
- potenciômetro de 10 kΩ para ajustar o limite de peso ou calibrar o
  offset;
- LEDs verde/vermelho e buzzer: dentro do limite = encomenda liberada,
  acima = recusada;
- ESP32 publicando peso e decisão via MQTT, assinando
  `encomendas/limite` e confirmando em `encomendas/confirmacao`.

Essa proposta não chegou a ter código no repositório: o README e o PDF
foram os únicos artefatos dela.

A partir de 14/09/2026 a equipe passou a desenvolver uma **garagem
inteligente com monitoramento de vagas**: HC-SR04 por vaga, ESP32
publicando `livre` / `ocupada` / `erro` em MQTT (retained), Last Will por
nó, ESP32 atuador com LEDs e app web de vagas livres (commits `17ff467`,
`28aa74c`, `9baba65`, `0c3d2b6`). Foi esse sistema que ficou funcional
e foi apresentado na N1.

## Decisão

Substituir a balança de encomendas pela garagem inteligente como
projeto da N1. A garagem passa a ser o ponto de partida do upgrade
para a N3 (CP12, seção 1, e [ADR 0001](0001-rfid-e-app-motorista.md)).

Motivos:

1. **Recursos disponíveis.** A garagem é construída com o hardware que
   a equipe já possui (ESP32, sensores ultrassônicos HC-SR04, LEDs). A
   balança dependia de componentes que não estavam disponíveis para a
   equipe (células de carga, módulos HX711) e de uma plataforma rígida
   montada para fixá-los.
2. **Crescimento até a N3 dentro dos limites de hardware atuais.** Com
   as mesmas peças, a garagem escala acrescentando vagas: mais pontos
   de medição e, depois, mais dispositivos (R1, R13); a ocupação ao
   longo do dia gera histórico e indicadores agregados (R14); a reserva
   de vaga dá um comando com confirmação e intertravamento (R7, R8).
   Na balança, crescer exigiria mais células de carga e plataformas, e
   o único comando natural era alterar o limite de peso.

Como consequência, a garagem foi o modelo que ficou funcional a tempo
da N1, com sensor, atuador, broker e interface rodando de ponta a
ponta; a balança ficou só no papel.

## Consequências

**Positivas**

- O CP12 parte de um sistema que existe e funciona, não de uma
  proposta.
- Problema e usuário claros: motorista que procura vaga e operador que
  acompanha a ocupação.
- Os requisitos R12–R18 têm aplicação direta (ver CP12, seção 3).

**Negativas / riscos**

- O documento de 26/08 (`docs/IOT_24_08.pdf`) e o início do histórico
  do Git descrevem outro projeto. Quem lê o repositório sem este ADR
  pode achar que o CP12 trocou de projeto sem justificativa.
- Calibração de célula de carga, HX711 e buzzer, que já tinham sido
  estudados, deixaram de ser usados.
- O ponto de partida da N3 precisa ser o projeto efetivamente
  defendido na N1. Como a garagem foi o projeto apresentado, o CP12
  marca **não** no pedido de troca; se o professor considerar a
  proposta de 26/08 como a N1 oficial, o CP12 precisa marcar **sim** e
  justificar a troca com este ADR.

## Alternativas consideradas

- **Manter a balança de encomendas:** projeto válido, mas dependia de
  componentes que a equipe não tinha e, com o hardware disponível,
  oferecia menos caminho para múltiplos dispositivos, histórico
  agregado e comando com intertravamento.
- **Combinar as duas ideias** (pesar o veículo na vaga): células de
  carga para o peso de um carro estão fora do escopo de maquete e de
  orçamento; o ultrassônico resolve a detecção de presença com muito
  menos custo.
