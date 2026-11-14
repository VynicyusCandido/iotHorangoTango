# ADR 0005 — Backend em três assinantes: persistência, operador e motorista

- **Status:** Aceito
- **Data:** 2026-11-12
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

O CP12 descreve o backend como **um** serviço FastAPI que assina
`garagem/v1/#`, grava no PostgreSQL e serve a API usada pelo painel do
operador e pelo app do motorista (CP12, seção 2, e R4–R7, R12).

A equipe definiu que o sistema terá **três assinantes** do broker, um
para cada uso dos dados: o banco de dados, o app do operador e o app do
cliente (motorista). Tudo o que roda no servidor continua sendo
considerado o **backend**.

Os três usos têm necessidades diferentes:

- **Banco:** precisa de tudo, inclusive do que aconteceu com o sistema
  fora do ar (fila de eventos do firmware, ADR 0003), e não pode perder
  mensagem nem gravar duplicado.
- **Operador:** precisa de login, perfis (R9, R16), histórico, alertas e
  enviar comandos (R7).
- **Motorista:** só precisa do agora (quais vagas estão livres e onde está
  o próprio carro), é público e roda no celular, portanto é a parte mais
  exposta.

## Decisão

1. **O backend tem três assinantes MQTT**, cada um em um serviço do
   Docker Compose, todos na mesma imagem (`backend/`) e com o mesmo
   módulo de contrato (`backend/comum/contrato.py`):

   | Serviço | Assina | Usuário MQTT | Publica | Banco |
   |---|---|---|---|---|
   | `persistencia` | `garagem/v1/#` | `backend` | nada | escreve tudo que vem do broker |
   | `operador` (porta 8000) | `garagem/v1/#` | `backend` | `cmd` (único) | lê; escreve `comandos` e `usuarios` |
   | `motorista` (porta 8001) | `vaga/+/estado`, `status`, `rfid` | `motorista` (só leitura) | nada | não usa |

2. **Os apps continuam falando só com a API** do seu assinante (REST +
   WebSocket), como no CP12. Nenhum navegador conecta no broker, e as
   senhas do MQTT ficam no servidor.
3. **Só a persistência grava o que vem do broker.** O operador grava
   apenas o que nasce nele: o comando enviado (antes de publicar) e os
   usuários. O andamento do comando (aceito/rejeitado → executado →
   confirmado) é gravado pela persistência ao receber `ack` e `estado`.
4. **O motorista não usa o banco.** O estado vem das mensagens retidas
   (estado das vagas e `status`), que chegam assim que ele assina. Não
   expõe distâncias, e só responde "onde está meu carro" para quem
   informa o UID da própria tag.
5. **Validação igual nos três.** Mensagem fora do contrato é descartada
   pelos três; só a persistência a registra (`mensagens_invalidas`, com
   o motivo).
6. **Duplicatas:** o firmware pode republicar um evento (PUBACK perdido)
   e o estado retido é reentregue a cada reconexão. A persistência ignora
   evento repetido (mesmo dispositivo, vaga e `ts`) e estado igual ao
   último da vaga; um `COMANDO_DUPLICADO` de reentrega não apaga o
   resultado do comando original.
7. **`"ts": null`** (ESP32 sem NTP) é estimado pela telemetria, que sai na
   hora: horário de referência menos a diferença de `uptime`. A linha fica
   com `ts_estimado = true`.

## Consequências

**Positivas**

- Cada assinante tem só o que precisa: o motorista, exposto ao público,
  não pode publicar comandos nem ler o banco.
- Uma falha em um não derruba os outros: se a API do operador cair, a
  persistência continua gravando, e o app do motorista continua no ar.
- No broker (`docker compose logs mosquitto`) aparecem os três
  assinantes, um por uso dos dados.

**Negativas / riscos**

- **O compose passa de 3 para 6 serviços** (`mosquitto`, `postgres`,
  `migracoes`, `persistencia`, `operador`, `motorista`). Continua um único
  `docker compose up -d` (R12).
- **Duas APIs:** o painel (V1) usa a do operador e o app do motorista (V3)
  usa a do motorista, em portas diferentes.
- **Novo usuário no broker** (`motorista`): `.env`, `iniciar.sh` e `acl`
  mudaram. Quem já tem `.env` precisa acrescentar `MQTT_MOTORISTA_*`,
  `POSTGRES_*`, `JWT_SEGREDO` e `ADMIN_*` (ver `.env.example`).
- O `backend` é compartilhado por persistência e operador; a persistência
  poderia publicar `cmd`. Separar em dois usuários fica para depois, se
  necessário.
- **ACL por client id:** as regras `pattern` do broker valem para todos
  os usuários. Qualquer usuário que conecte com o client id de um ESP32
  publica em nome dele. A correção (usuário por dispositivo, `%u`) é
  independente deste ADR.
- **"Onde está meu carro" pelo UID:** quem souber o UID de uma tag
  descobre a vaga do carro. Aceitável enquanto o app não tem login de
  motorista; o UID não é secreto (ADR 0001).

## Alternativas consideradas

- **Um backend com um assinante (CP12):** mais simples de subir, mas não
  separa os três usos dos dados como a equipe definiu.
- **Um backend com três assinantes internos** (uma conexão MQTT
  repassada a três módulos): mantém o compose do CP12, mas para o broker
  continua sendo um assinante só, e uma falha derruba os três.
- **Apps assinando o broker direto pelo WebSocket** (como no ADR 0001):
  o mais simples, mas as senhas do MQTT iriam para o navegador e os
  comandos deixariam de passar pelo login e pelos perfis (R9, R16).
