#include <WiFi.h>
#include <SPI.h>
#include <MFRC522.h>
#include <MQTT.h>         // arduino-mqtt (Joel Gaehwiler): publica com QoS 1
#include <ArduinoJson.h>  // v7
#include <time.h>
#include "tipos.h"     // Estado, Momento, Evento

// Firmware único: um ESP32 opera as duas vagas, no contrato MQTT v1
// (CONTRATOS.md). Decisões em docs/adr/0001 e docs/adr/0003.
//   - HC-SR04 de cada vaga -> estado da vaga (livre / ocupada / erro)
//   - RC522 de cada vaga   -> UID da tag do carro
//   - LED RGB de cada vaga <- segue o estado local do sensor (sem rede)
// Mudanças de estado e tags lidas entram numa fila única, publicada em
// ordem quando o broker está disponível: nada se perde em quedas curtas.
// Mapa de pinos em docs/mapa-de-pinos.md.

// ---------- Credenciais (secrets.h, fora do git) ----------
// Copie secrets.example.h para secrets.h nesta mesma pasta e preencha.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Falta secrets.h: copie secrets.example.h para secrets.h e preencha WIFI_SSID e WIFI_PASSWORD"
#endif

// Broker: pode ser definido no secrets.h; senão, usa estes valores
#ifndef MQTT_HOST
#define MQTT_HOST "broker.hivemq.com"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef MQTT_USER
#define MQTT_USER ""  // vazio = conexão anônima
#endif
#ifndef MQTT_PASSWORD
#define MQTT_PASSWORD ""
#endif

const char* FIRMWARE_VERSAO = "0.9.0";
const int   CONTRATO_VERSAO = 1;

// Servidores de hora (UTC)
const char* NTP_1 = "pool.ntp.org";
const char* NTP_2 = "time.google.com";

// ---------- Identidade e tópicos ----------
//   garagem/v1/<deviceId>/vaga/<n>/estado  JSON, QoS 1, retained
//   garagem/v1/<deviceId>/rfid             JSON, QoS 1
//   garagem/v1/<deviceId>/telemetria       JSON, QoS 0, a cada 10 s
//   garagem/v1/<deviceId>/status           JSON, QoS 1, retained (LWT)
char deviceId[32];       // "esp32-garagem-<mac>"
char topicoBase[64];     // "garagem/v1/<deviceId>"
char topicoStatus[80];
char lwtPayload[96];     // status "offline", publicado pelo broker se o ESP32 cair

WiFiClient rede;
MQTTClient mqtt(1024);   // buffer de leitura/escrita (bytes)

// ---------- Pinos compartilhados ----------
// SPI (VSPI) e reset comuns aos dois RC522; cada leitor tem o seu SS
const int PIN_SPI_SCK  = 18;
const int PIN_SPI_MISO = 19;
const int PIN_SPI_MOSI = 23;
const int PIN_RFID_RST = 22;

// ---------- Estados de uma vaga (enum Estado em tipos.h) ----------
const char* NOMES_ESTADO[] = {"desconhecido", "livre", "ocupada", "erro"};

// ---------- Definição das vagas ----------
// Cada vaga: {numero, trig, echo, rfidSS, ledVermelho, ledVerde}
// LED RGB catodo comum: HIGH acende. Os demais campos são controle
// interno e começam zerados.
struct Vaga {
  int numero;
  int trig;
  int echo;
  int rfidSS;
  int ledVermelho;
  int ledVerde;

  // Ultrassônico (fonte do estado da vaga)
  Estado estado;          // estado confirmado
  Estado candidato;       // estado novo aguardando confirmação
  int confirmacoes;       // leituras seguidas que concordam com o candidato
  int falhas;             // leituras inválidas seguidas
  float distancia;        // última leitura válida (cm); NAN se inválida
  bool republicar;        // republicar o estado atual após (re)conectar

  // RFID (só identifica; nunca altera o estado da vaga)
  char ultimoUid[21];       // última tag lida neste leitor
  unsigned long ultimaTag;  // millis() da última leitura
  bool leitorOk;            // RC522 respondeu na última verificação
};

Vaga vagas[] = {
  {1, 25, 34, 5, 32, 33},
  {2, 26, 35, 4, 27, 13},
};
const int NUM_VAGAS = sizeof(vagas) / sizeof(vagas[0]);

// Um RC522 por vaga, mesma ordem de vagas[]
MFRC522 leitores[NUM_VAGAS];

// ---------- Limiares de detecção (com histerese) ----------
// Abaixo de LIMIAR_OCUPA_CM a vaga passa a ocupada; acima de
// LIMIAR_LIBERA_CM, a livre. Entre os dois, mantém o estado atual.
// Valores para carro real; na maquete, medir e ajustar.
const float LIMIAR_OCUPA_CM  = 25.0;
const float LIMIAR_LIBERA_CM = 35.0;
const int   LEITURAS_CONFIRMACAO = 3;  // leituras seguidas para trocar o estado
const int   FALHAS_PARA_ERRO     = 5;  // leituras inválidas seguidas até "erro"
const float DIST_MIN_CM = 2.0;         // faixa válida do HC-SR04
const float DIST_MAX_CM = 400.0;

// ---------- Tempos (ms) ----------
const unsigned long INTERVALO_ULTRASSOM   = 1000;
const unsigned long INTERVALO_RFID        = 100;
const unsigned long RFID_REPETICAO        = 3000;   // mesma tag no mesmo leitor: ignora
const unsigned long INTERVALO_CHECA_RFID  = 5000;   // confere se os RC522 respondem
const unsigned long INTERVALO_TELEMETRIA  = 10000;
const unsigned long INTERVALO_RECONEXAO   = 5000;   // entre tentativas de conectar ao broker
const unsigned long PISCA_MS              = 500;    // LED vermelho piscando (sensor em falha)
const int           MQTT_TIMEOUT_MS       = 500;    // espera pelo PUBACK (QoS 1)
const int           PUBLICACOES_POR_VOLTA = 5;      // eventos da fila publicados por volta do loop

unsigned long ultimaLeitura = 0;
unsigned long ultimaConsultaRfid = 0;
unsigned long ultimaChecagemRfid = 0;
unsigned long ultimaTelemetria = 0;
unsigned long ultimaTentativaMqtt = 0;
bool tentouMqtt = false;
bool wifiEstavaOk = false;
bool mqttEstavaOk = false;
bool ntpIniciado = false;
bool statusPendente = false;

// ======================================================================
// Relógio
// ======================================================================

// Antes disso (nov/2023) o relógio do ESP32 ainda não foi sincronizado
const time_t EPOCH_VALIDO = 1700000000;

bool relogioSincronizado() {
  return time(nullptr) > EPOCH_VALIDO;
}

Momento agora() {
  Momento m;
  m.uptimeMs = millis();
  m.epoch = relogioSincronizado() ? time(nullptr) : 0;
  return m;
}

// Preenche ts conforme o contrato. Evento registrado antes da primeira
// sincronização: se o relógio já sincronizou na hora de publicar, o
// horário real é calculado pelo uptime (mesmo boot).
void preencherTs(JsonDocument& doc, const Momento& m) {
  time_t epoch = m.epoch;
  if (epoch == 0 && relogioSincronizado()) {
    epoch = time(nullptr) - (time_t)((uint32_t)(millis() - m.uptimeMs) / 1000);
  }
  if (epoch > 0) {
    struct tm t;
    gmtime_r(&epoch, &t);
    char ts[25];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &t);
    doc["ts"] = ts;
  } else {
    doc["ts"] = nullptr;
    doc["ts_sincronizado"] = false;
    doc["uptime_ms"] = m.uptimeMs;
  }
}

// ======================================================================
// Fila de eventos (mudanças de estado e tags lidas, em ordem)
// ======================================================================

const int FILA_MAX = 64;
Evento fila[FILA_MAX];
int filaInicio = 0;
int filaTamanho = 0;
unsigned long eventosDescartados = 0;

// Fila cheia: descarta o evento mais antigo
void enfileirar(const Evento& e) {
  if (filaTamanho == FILA_MAX) {
    filaInicio = (filaInicio + 1) % FILA_MAX;
    filaTamanho--;
    eventosDescartados++;
    Serial.printf("Fila cheia: evento mais antigo descartado (total %lu)\n", eventosDescartados);
  }
  fila[(filaInicio + filaTamanho) % FILA_MAX] = e;
  filaTamanho++;
}

// ======================================================================
// Publicação (JSON do contrato v1)
// ======================================================================

double umaCasa(float x) {
  return roundf(x * 10.0f) / 10.0;
}

void cabecalho(JsonDocument& doc, const Momento& m) {
  doc["v"] = CONTRATO_VERSAO;
  doc["deviceId"] = deviceId;
  preencherTs(doc, m);
}

void preencherDistancia(JsonObject destino, float distancia) {
  if (isnan(distancia)) {
    destino["distancia"] = nullptr;
  } else {
    JsonObject d = destino["distancia"].to<JsonObject>();
    d["valor"] = umaCasa(distancia);
    d["unidade"] = "cm";
  }
}

bool publicarJson(const char* topico, JsonDocument& doc, bool retained, int qos) {
  char payload[512];
  size_t n = serializeJson(doc, payload, sizeof(payload));
  if (n == 0 || n >= sizeof(payload) - 1) {
    Serial.printf("JSON grande demais para %s\n", topico);
    return false;
  }
  return mqtt.publish(topico, payload, (int)n, retained, qos);
}

bool publicarEvento(const Evento& e) {
  JsonDocument doc;
  cabecalho(doc, e.quando);
  doc["vaga"] = e.vaga;

  char topico[112];
  if (e.tipo == EV_ESTADO) {
    doc["estado"] = NOMES_ESTADO[e.estado];
    preencherDistancia(doc.as<JsonObject>(), e.distancia);
    snprintf(topico, sizeof(topico), "%s/vaga/%d/estado", topicoBase, e.vaga);
    return publicarJson(topico, doc, true, 1);
  }

  doc["uid"] = e.uid;
  snprintf(topico, sizeof(topico), "%s/rfid", topicoBase);
  return publicarJson(topico, doc, false, 1);
}

// Publica a fila em ordem; cada evento só sai depois de confirmado (PUBACK)
void drenarFila() {
  for (int i = 0; i < PUBLICACOES_POR_VOLTA && filaTamanho > 0 && mqtt.connected(); i++) {
    const Evento& e = fila[filaInicio];
    if (!publicarEvento(e)) {
      Serial.printf("Falha ao publicar evento (erro %d); tenta de novo\n", mqtt.lastError());
      return;
    }
    // Estado da vaga já foi ao broker: não precisa republicar
    if (e.tipo == EV_ESTADO) {
      for (int j = 0; j < NUM_VAGAS; j++) {
        if (vagas[j].numero == e.vaga) vagas[j].republicar = false;
      }
    }
    filaInicio = (filaInicio + 1) % FILA_MAX;
    filaTamanho--;
  }
}

// Após (re)conectar e esvaziar a fila: republica o estado atual de cada
// vaga (retained), caso o broker tenha reiniciado e perdido o retido
void republicarEstados() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    Vaga& v = vagas[i];
    if (!v.republicar || v.estado == DESCONHECIDO) continue;
    Evento e = {};
    e.tipo = EV_ESTADO;
    e.vaga = v.numero;
    e.estado = v.estado;
    e.distancia = (v.estado == ERRO) ? NAN : v.distancia;
    e.quando = agora();
    if (publicarEvento(e)) v.republicar = false;
  }
}

void publicarStatusOnline() {
  JsonDocument doc;
  cabecalho(doc, agora());
  doc["status"] = "online";
  doc["firmware"] = FIRMWARE_VERSAO;
  JsonArray lista = doc["vagas"].to<JsonArray>();
  for (int i = 0; i < NUM_VAGAS; i++) lista.add(vagas[i].numero);
  if (publicarJson(topicoStatus, doc, true, 1)) statusPendente = false;
}

void publicarTelemetria() {
  JsonDocument doc;
  cabecalho(doc, agora());
  JsonArray lista = doc["vagas"].to<JsonArray>();
  for (int i = 0; i < NUM_VAGAS; i++) {
    JsonObject o = lista.add<JsonObject>();
    o["vaga"] = vagas[i].numero;
    preencherDistancia(o, vagas[i].distancia);
  }
  JsonObject rssi = doc["rssi"].to<JsonObject>();
  rssi["valor"] = WiFi.RSSI();
  rssi["unidade"] = "dBm";
  JsonObject uptime = doc["uptime"].to<JsonObject>();
  uptime["valor"] = (uint32_t)millis();
  uptime["unidade"] = "ms";

  char topico[96];
  snprintf(topico, sizeof(topico), "%s/telemetria", topicoBase);
  publicarJson(topico, doc, false, 0);  // periódica: perder uma amostra não importa
}

// ======================================================================
// LEDs (regra local: seguem o sensor, com ou sem rede)
// ======================================================================

// Livre: verde. Ocupada: vermelho. Erro: vermelho piscando (nunca verde
// sem certeza). Ainda sem leitura: apagado.
void atualizarLed(const Vaga& v) {
  bool verde = false;
  bool vermelho = false;
  switch (v.estado) {
    case LIVRE:   verde = true; break;
    case OCUPADA: vermelho = true; break;
    case ERRO:    vermelho = (millis() / PISCA_MS) % 2 == 0; break;
    default:      break;
  }
  digitalWrite(v.ledVerde, verde ? HIGH : LOW);
  digitalWrite(v.ledVermelho, vermelho ? HIGH : LOW);
}

void atualizarTodosLeds() {
  for (int i = 0; i < NUM_VAGAS; i++) atualizarLed(vagas[i]);
}

// ======================================================================
// Ultrassônico
// ======================================================================

float medirDistancia(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  // timeout de 30 ms (~5 m) evita travar se não houver eco
  long duracao = pulseIn(echo, HIGH, 30000);
  if (duracao == 0) return -1;  // sem leitura válida

  return duracao * 0.0343 / 2.0;  // cm
}

bool leituraValida(float dist) {
  return dist >= DIST_MIN_CM && dist <= DIST_MAX_CM;
}

// Processa uma leitura; retorna true se o estado confirmado mudou
bool processarLeitura(Vaga& v, float dist) {
  // Leitura inválida: não diz nada sobre a vaga. Só vira "erro" se persistir.
  if (!leituraValida(dist)) {
    v.confirmacoes = 0;
    if (v.falhas < FALHAS_PARA_ERRO) v.falhas++;
    if (v.falhas >= FALHAS_PARA_ERRO && v.estado != ERRO) {
      v.estado = ERRO;
      return true;
    }
    return false;
  }
  v.falhas = 0;

  // Classifica com histerese; na faixa intermediária mantém o estado atual
  // (ou, sem estado definido ainda, usa o meio da faixa)
  Estado leitura;
  if (dist < LIMIAR_OCUPA_CM) {
    leitura = OCUPADA;
  } else if (dist > LIMIAR_LIBERA_CM) {
    leitura = LIVRE;
  } else if (v.estado == LIVRE || v.estado == OCUPADA) {
    leitura = v.estado;
  } else {
    leitura = dist < (LIMIAR_OCUPA_CM + LIMIAR_LIBERA_CM) / 2 ? OCUPADA : LIVRE;
  }

  // Confirmação: só troca após LEITURAS_CONFIRMACAO leituras seguidas iguais
  if (leitura == v.estado) {
    v.confirmacoes = 0;
    return false;
  }
  if (leitura == v.candidato && v.confirmacoes > 0) {
    v.confirmacoes++;
  } else {
    v.candidato = leitura;
    v.confirmacoes = 1;
  }
  if (v.confirmacoes >= LEITURAS_CONFIRMACAO) {
    v.estado = leitura;
    v.confirmacoes = 0;
    return true;
  }
  return false;
}

// Lê as vagas uma de cada vez (um sensor não capta o eco do outro)
void lerUltrassonicos() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    Vaga& v = vagas[i];
    float dist = medirDistancia(v.trig, v.echo);
    v.distancia = leituraValida(dist) ? dist : NAN;

    if (processarLeitura(v, dist)) {
      Evento e = {};
      e.tipo = EV_ESTADO;
      e.vaga = v.numero;
      e.estado = v.estado;
      e.distancia = (v.estado == ERRO) ? NAN : dist;
      e.quando = agora();
      enfileirar(e);
      Serial.printf("Vaga %d: %s (%.1f cm)\n", v.numero, NOMES_ESTADO[v.estado], dist);
    }
  }
}

// ======================================================================
// RFID
// ======================================================================

// VersionReg 0x00 ou 0xFF = leitor desligado ou fio solto
bool leitorResponde(MFRC522& leitor) {
  byte versao = leitor.PCD_ReadRegister(MFRC522::VersionReg);
  return versao != 0x00 && versao != 0xFF;
}

// Leitor sob o piso da maquete (ADR 0003): ganho máximo da antena para
// ler a tag embaixo do carro através do piso
void iniciarLeitores() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    leitores[i].PCD_Init(vagas[i].rfidSS, PIN_RFID_RST);
    leitores[i].PCD_SetAntennaGain(MFRC522::RxGain_max);
  }
}

void checarLeitores() {
  bool todosOk = true;
  for (int i = 0; i < NUM_VAGAS; i++) {
    if (!leitorResponde(leitores[i])) todosOk = false;
  }
  // Algum leitor parou (RC522 às vezes trava após ruído na alimentação):
  // reinicia TODOS, porque o reset é comum e pode ter zerado os outros
  if (!todosOk) iniciarLeitores();

  for (int i = 0; i < NUM_VAGAS; i++) {
    bool ok = leitorResponde(leitores[i]);
    if (ok != vagas[i].leitorOk) {
      Serial.printf("RFID vaga %d: %s\n", vagas[i].numero,
                    ok ? "leitor OK" : "LEITOR NAO RESPONDE");
    }
    vagas[i].leitorOk = ok;
  }
}

// Cada tag lida vira um evento na fila, sem olhar o estado da vaga:
// cruzar tag com vaga ocupada é papel do backend
void lerRfids() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    Vaga& v = vagas[i];
    MFRC522& leitor = leitores[i];
    if (!v.leitorOk) continue;
    if (!leitor.PICC_IsNewCardPresent() || !leitor.PICC_ReadCardSerial()) continue;

    // UID em hexadecimal maiúsculo, sem separadores (contrato v1)
    char uid[sizeof(v.ultimoUid)] = "";
    for (byte b = 0; b < leitor.uid.size && b < 10; b++) {
      snprintf(uid + 2 * b, sizeof(uid) - 2 * b, "%02X", leitor.uid.uidByte[b]);
    }
    leitor.PICC_HaltA();
    leitor.PCD_StopCrypto1();

    // Mesma tag no mesmo leitor há pouco tempo: repetição, ignora
    bool repetida = strcmp(uid, v.ultimoUid) == 0 &&
                    millis() - v.ultimaTag < RFID_REPETICAO;
    v.ultimaTag = millis();
    if (repetida) continue;

    strcpy(v.ultimoUid, uid);
    Evento e = {};
    e.tipo = EV_RFID;
    e.vaga = v.numero;
    e.distancia = NAN;
    strcpy(e.uid, uid);
    e.quando = agora();
    enfileirar(e);
    Serial.printf("RFID vaga %d: tag %s\n", v.numero, uid);
  }
}

// ======================================================================
// Rede (sem bloqueio: sensores e LEDs seguem funcionando sem Wi-Fi/broker)
// ======================================================================

void gerarIdentidade() {
  uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(deviceId, sizeof(deviceId), "esp32-garagem-%012llx", mac);
  snprintf(topicoBase, sizeof(topicoBase), "garagem/v%d/%s", CONTRATO_VERSAO, deviceId);
  snprintf(topicoStatus, sizeof(topicoStatus), "%s/status", topicoBase);
  snprintf(lwtPayload, sizeof(lwtPayload),
           "{\"v\":%d,\"deviceId\":\"%s\",\"status\":\"offline\"}", CONTRATO_VERSAO, deviceId);
  Serial.printf("deviceId: %s\n", deviceId);
}

void aoConectarMqtt() {
  Serial.println("conectado.");
  mqttEstavaOk = true;
  statusPendente = true;
  // Republica o estado atual depois que a fila esvaziar
  for (int i = 0; i < NUM_VAGAS; i++) vagas[i].republicar = true;
  if (filaTamanho > 0) Serial.printf("Publicando %d evento(s) da fila\n", filaTamanho);
}

void cuidarDaRede() {
  // Wi-Fi: o ESP32 reconecta sozinho; aqui só registra as transições
  bool wifiOk = WiFi.status() == WL_CONNECTED;
  if (wifiOk != wifiEstavaOk) {
    wifiEstavaOk = wifiOk;
    if (wifiOk) {
      Serial.println("Wi-Fi conectado. IP: " + WiFi.localIP().toString());
      if (!ntpIniciado) {
        configTime(0, 0, NTP_1, NTP_2);  // sincroniza em segundo plano
        ntpIniciado = true;
      }
    } else {
      Serial.println("Wi-Fi caiu; sensores e LEDs seguem, eventos vão para a fila");
    }
  }
  if (!wifiOk) return;

  // MQTT: uma tentativa a cada INTERVALO_RECONEXAO, sem prender o loop
  if (mqtt.connected()) return;
  if (mqttEstavaOk) {
    mqttEstavaOk = false;
    Serial.println("Broker desconectado; eventos vão para a fila");
  }
  if (tentouMqtt && millis() - ultimaTentativaMqtt < INTERVALO_RECONEXAO) return;
  tentouMqtt = true;
  ultimaTentativaMqtt = millis();

  Serial.print("Conectando ao broker MQTT... ");
  bool ok = strlen(MQTT_USER) > 0
              ? mqtt.connect(deviceId, MQTT_USER, MQTT_PASSWORD)
              : mqtt.connect(deviceId);
  if (ok) {
    aoConectarMqtt();
  } else {
    Serial.printf("falhou (erro %d, rc %d); nova tentativa em %lu s\n",
                  mqtt.lastError(), mqtt.returnCode(), INTERVALO_RECONEXAO / 1000);
  }
}

// ======================================================================

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < NUM_VAGAS; i++) {
    Vaga& v = vagas[i];
    pinMode(v.trig, OUTPUT);
    pinMode(v.echo, INPUT);
    digitalWrite(v.trig, LOW);
    v.distancia = NAN;

    // LEDs apagados até a primeira leitura confirmada
    pinMode(v.ledVerde, OUTPUT);
    pinMode(v.ledVermelho, OUTPUT);
    atualizarLed(v);

    // Todos os SS em nível alto antes do SPI: nenhum leitor selecionado
    pinMode(v.rfidSS, OUTPUT);
    digitalWrite(v.rfidSS, HIGH);
  }

  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI);
  iniciarLeitores();
  for (int i = 0; i < NUM_VAGAS; i++) {
    vagas[i].leitorOk = leitorResponde(leitores[i]);
    Serial.printf("RFID vaga %d: %s\n", vagas[i].numero,
                  vagas[i].leitorOk ? "leitor OK" : "LEITOR NAO RESPONDE");
  }

  gerarIdentidade();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("Conectando ao Wi-Fi em segundo plano");

  mqtt.begin(MQTT_HOST, MQTT_PORT, rede);
  mqtt.setWill(topicoStatus, lwtPayload, true, 1);
  mqtt.setKeepAlive(10);
  mqtt.setCleanSession(true);
  mqtt.setTimeout(MQTT_TIMEOUT_MS);
}

void loop() {
  cuidarDaRede();
  mqtt.loop();

  if (millis() - ultimaLeitura >= INTERVALO_ULTRASSOM) {
    ultimaLeitura = millis();
    lerUltrassonicos();
  }

  if (millis() - ultimaChecagemRfid >= INTERVALO_CHECA_RFID) {
    ultimaChecagemRfid = millis();
    checarLeitores();
  }

  if (millis() - ultimaConsultaRfid >= INTERVALO_RFID) {
    ultimaConsultaRfid = millis();
    lerRfids();
  }

  atualizarTodosLeds();

  if (mqtt.connected()) {
    if (statusPendente) publicarStatusOnline();
    drenarFila();
    if (filaTamanho == 0) republicarEstados();

    if (millis() - ultimaTelemetria >= INTERVALO_TELEMETRIA) {
      ultimaTelemetria = millis();
      publicarTelemetria();
    }
  }
}
