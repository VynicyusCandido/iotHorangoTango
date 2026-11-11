// Tipos do firmware. Ficam num header porque a Arduino IDE gera os
// protótipos das funções do .ino antes das structs declaradas nele.
#pragma once

#include <Arduino.h>
#include <time.h>

// Estado de uma vaga. O sensor produz DESCONHECIDO, LIVRE, OCUPADA e
// ERRO (HC-SR04 sem resposta); RESERVADA vem de um comando "reservar".
enum Estado { DESCONHECIDO, LIVRE, OCUPADA, ERRO, RESERVADA };

// Momento de um evento: horário real (0 se o relógio não sincronizou) e
// uptime, registrados quando o evento acontece, não quando é publicado
struct Momento {
  time_t epoch;
  uint32_t uptimeMs;
};

enum TipoEvento : uint8_t { EV_ESTADO, EV_RFID, EV_ACK };

// Resultado de um comando no tópico ack (contrato v1)
enum ResultadoAck : uint8_t { ACK_ACEITO, ACK_REJEITADO, ACK_EXECUTADO };

// Tamanho máximo do commandId, com o '\0'
const int TAM_COMMAND_ID = 41;

// Evento da fila: mudança de estado de uma vaga, tag lida ou resposta a
// um comando
struct Evento {
  TipoEvento tipo;
  int vaga;                       // EV_ESTADO, EV_RFID
  Estado estado;                  // EV_ESTADO
  float distancia;                // EV_ESTADO; NAN se inválida (estado "erro")
  char uid[21];                   // EV_RFID
  char commandId[TAM_COMMAND_ID]; // EV_ACK; "" = commandId null
  ResultadoAck resultado;         // EV_ACK
  const char* erro;               // EV_ACK; código de erro, nullptr se não rejeitado
  Momento quando;
};
