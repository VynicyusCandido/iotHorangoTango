# ADR 0007 — Estado do leitor RFID na telemetria (campo opcional no v1)

- **Status:** Aceito
- **Data:** 2026-11-23
- **Autores:** Gustavo Vinicius Taques, Luis Fernando Pereira, João Pedro
  Angelico, Vynicyus Candido

## Contexto

O firmware verifica a cada 5 s se o RC522 de cada vaga responde
(`checarLeitores`, ADR 0003) e mostra o resultado só no monitor serial
(`leitor OK` / `LEITOR NAO RESPONDE`). O servidor não recebe essa
informação. Na bancada, um leitor com mau contato alternava entre
responder e não responder, e o painel mostrava apenas que nenhuma tag
nova chegava — sem como distinguir "nenhum carro passou" de "leitor
fora do ar".

O administrador precisa ver no painel se o leitor de cada vaga está
funcionando. Para isso o ESP32 tem de publicar o estado do leitor, o que
muda o contrato MQTT. O CONTRATOS.md diz que o v1 está congelado desde
27/10/2026 e que qualquer mudança gera uma nova versão.

## Decisão

1. **Campo opcional `leitor_rfid` em cada vaga da telemetria**, com
   valor `ok` ou `falha` (resultado da última verificação do RC522).
   Ausente = firmware anterior, estado desconhecido.
2. **Exceção aditiva ao congelamento do v1**, sem v2: nenhum campo
   existente muda, o payload do firmware 0.10.0 continua válido e o
   backend aceita os dois. Um v2 exigiria trocar o prefixo dos tópicos
   (`garagem/v2/`), a ACL, o simulador e manter dois contratos no
   backend — custo alto para um campo informativo. A exceção fica
   registrada no CONTRATOS.md, junto do campo.
3. **Telemetria, não `status` nem tópico novo:** a telemetria já sai a
   cada 10 s com dados por vaga, então o painel fica atualizado em até
   10 s sem mensagem nova. O `status` é retido e só sai na conexão.
4. **Persistência guarda só o estado atual**, em `dispositivos.leitores_rfid`
   (JSON por vaga: `estado`, `desde`, `atualizado_em`; migração 0003).
   `desde` só muda quando o estado muda. Telemetria retida é ignorada,
   como no `ultimo_contato`.
5. **Painel:** cada cartão de vaga mostra "Leitor RFID: ✓ ok" ou
   "✗ falha desde hh:mm" (em vermelho). Com o dispositivo offline, o
   último estado não é mostrado.

## Consequências

- Firmware 0.11.0 publica o campo; o backend novo aceita firmware
  antigo e novo. Backend antigo **recusa** a telemetria do firmware novo
  (`extra="forbid"`): atualizar o backend antes ou junto do firmware.
- Vaga sem leitor montado aparece como "falha" — é o estado real.
- Não há histórico das falhas do leitor nem alerta automático; se for
  preciso, a regra segue o padrão de `sensor_erro` em `operador/regras.py`.

## Alternativas descartadas

- **Contrato v2:** ver decisão 2.
- **Sem mudar o contrato ("última tag lida há X min"):** não diferencia
  leitor quebrado de vaga sem movimento.
- **Gravar como medição (`medicoes`, grandeza `leitor_rfid`):** misturaria
  um estado discreto com grandezas numéricas e entraria nos gráficos.
