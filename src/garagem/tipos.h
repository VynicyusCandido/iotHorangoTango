// Tipos do firmware. Ficam num header porque a Arduino IDE gera os
// protótipos das funções do .ino antes das structs declaradas nele.
#pragma once

#include <Arduino.h>
#include <time.h>

// Estado de uma vaga. ERRO = o HC-SR04 não responde (fio solto,
// sensor queimado...).
enum Estado { DESCONHECIDO, LIVRE, OCUPADA, ERRO };

// Momento de um evento: horário real (0 se o relógio não sincronizou) e
// uptime, registrados quando o evento acontece, não quando é publicado
struct Momento {
  time_t epoch;
  uint32_t uptimeMs;
};

enum TipoEvento : uint8_t { EV_ESTADO, EV_RFID };

// Evento da fila: mudança de estado de uma vaga ou tag lida
struct Evento {
  TipoEvento tipo;
  int vaga;
  Estado estado;     // EV_ESTADO
  float distancia;   // EV_ESTADO; NAN se inválida (estado "erro")
  char uid[21];      // EV_RFID
  Momento quando;
};
