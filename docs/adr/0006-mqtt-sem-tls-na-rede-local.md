# ADR 0006 — MQTT sem TLS na rede local da demonstração

- **Status:** Aceito
- **Data:** 2026-11-14
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

O CP12 (R16) pede que o TLS no MQTT seja avaliado e que, se não for
implementado, a justificativa fique registrada. Hoje o ESP32 e os
assinantes do backend falam com o Mosquitto em texto claro (porta 1883),
com usuário e senha (R9) e ACL por usuário.

Sem TLS, quem estiver na mesma rede e capturar o tráfego vê as senhas do
MQTT, os UIDs das tags e os comandos, e pode tentar se passar pelo
broker.

## Decisão

**Não usar TLS no MQTT na N3.** O sistema roda numa rede local isolada
(roteador da maquete, sem acesso de fora), e as proteções ficam assim:

1. **Rede:** broker, banco e ESP32 só na LAN da demonstração. O
   PostgreSQL é publicado só em `127.0.0.1`; o broker e as APIs, só na
   LAN. Nada é exposto à internet.
2. **Broker:** sem conexão anônima; um usuário por papel, com o mínimo de
   permissão (`dispositivo`, `backend`, `persistencia`, `motorista`,
   `monitor`). Só o operador (`backend`) publica `cmd`.
3. **Comandos:** só saem do backend, depois de login (JWT) e com perfil
   de administrador (R9, R16). A API recusa segredos JWT fracos e
   bloqueia força bruta no login.
4. **Dados:** o UID da tag só identifica o carro; não dá acesso nem
   privilégio (ADR 0001). Por isso não é tratado como segredo.

Motivos para não ativar o TLS agora:

- **ESP32:** TLS exige o certificado da CA no firmware, relógio certo para
  validar a validade do certificado (antes do NTP a conexão falharia) e
  ~40 KB de RAM a mais por conexão, num firmware que já guarda a fila de
  eventos na RAM.
- **Certificados:** seria preciso uma CA própria, renovação e distribuir o
  certificado para cada ESP32 e para a equipe; numa rede local sem nome
  de domínio, o certificado precisaria valer pelo IP.
- **Prazo:** o esforço sai dos requisitos obrigatórios (R1–R11) a duas
  semanas da entrega, para proteger contra um ataque (escuta na LAN da
  maquete) que não existe no cenário da demonstração.

## Consequências

**Positivas**

- Firmware e broker mais simples; conexão do ESP32 não depende do NTP.
- O risco fica documentado e com as mitigações acima.

**Negativas / riscos**

- Na LAN, quem capturar o tráfego vê as senhas do MQTT e pode publicar
  como o ESP32 (agravado pela ACL por client id; ver ADR 0005).
- **Não serve para produção.** Fora de uma rede isolada (estacionamento
  real, broker na internet), TLS passa a ser obrigatório: listener 8883
  com certificado, `WiFiClientSecure` no ESP32 e senhas novas.

## Como ativar depois

1. Gerar CA e certificado do servidor; listener `8883` com `cafile`,
   `certfile` e `keyfile` no `mosquitto.conf`.
2. Firmware: `WiFiClientSecure` com o certificado da CA e esperar o NTP
   antes de conectar.
3. Backend: `tls_params` no `aiomqtt.Client` e `MQTT_PORT=8883`.
