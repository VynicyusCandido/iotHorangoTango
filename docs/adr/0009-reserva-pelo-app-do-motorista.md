# ADR 0009 — Reserva de vaga pelo app do motorista, identificada pela tag

- **Status:** Aceito
- **Data:** 2026-11-24
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

Até aqui só o administrador reservava vaga, pelo painel (`POST
/api/comandos`, ADR 0004). O motorista apenas consultava as vagas livres
e onde estava o carro dele (ADR 0001). Na demonstração da maquete, o
fluxo natural é o motorista escolher a vaga no celular e ver o LED ficar
azul.

O app do motorista é público e sem login (ADR 0005). O serviço
`motorista` só assina o MQTT e não usa o banco. O `operador` é o único
que publica em `cmd` (ACL do Mosquitto).

## Decisão

1. **A tag cadastrada identifica o motorista.** O app manda `{"uid",
   "vaga"}` e o operador só aceita UID cadastrado pelo administrador
   (`POST /api/tags`); sem cadastro → 403. Não há conta nem senha para o
   motorista.
2. **O operador continua sendo o único publicador de comandos.** O
   `motorista` expõe `POST /api/reservas` e `GET /api/reservas/{commandId}`
   e repassa por HTTP ao operador (`OPERADOR_URL`), em
   `POST /api/motorista/reservas`. O comando sai pelo mesmo
   `enviar_comando` do painel, com `origem = tag:<uid>`. A ACL do broker
   e o fluxo de `ack` não mudam.
3. **Regras checadas no operador antes de publicar:**
   - vaga existente (com mais de um ESP32 com a mesma vaga, `device_id`
     obrigatório);
   - dispositivo online e vaga `livre`;
   - **uma reserva ativa por tag**: um comando `enviado`/`aceito`, ou uma
     vaga que ainda está `reservada` por essa tag (última hora), bloqueia
     outra (409). Recusado pelo firmware, a tag pode tentar de novo.
4. **Duração = a configurada no ESP32** (`duracao_reserva_min`): o
   motorista não escolhe o prazo. O firmware continua sendo quem
   confirma (LED azul) ou recusa (`VAGA_OCUPADA` etc.).
5. **Consulta só de reservas do motorista:** `GET` devolve apenas
   comandos com origem `tag:`; o painel continua vendo todos.

## Consequências

- O motorista reserva pelo celular sem login; o admin controla quem pode
  reservar cadastrando ou removendo tags.
- O UID não é segredo (está impresso em alguns cartões e qualquer leitor
  o lê): quem souber um UID cadastrado consegue reservar em nome dele.
  Aceitável na maquete; em produção, exigiria vincular a tag a uma
  conta ou um código enviado ao motorista.
- O `motorista` passa a depender do `operador` para reservar: sem ele,
  responde 503; a consulta de vagas segue funcionando só com o MQTT.
- A rota `/api/motorista/reservas` também fica aberta na porta 8000 do
  operador, com as mesmas regras.
- Cadastro de tag ainda só pela API (`/docs` do operador); o painel não
  tem formulário.

## Alternativas descartadas

- **`motorista` publicar `cmd` direto:** quebraria a regra de publicador
  único (ADR 0005), exigiria ACL nova e regras duplicadas fora do banco.
- **Login do motorista:** custo alto (cadastro, senha, sessão) para a
  maquete; a tag já é o identificador físico do carro.
- **Motorista escolher a duração:** permitiria segurar vagas por muito
  tempo; o prazo fica com o administrador.
