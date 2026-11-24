#include <WiFi.h>
#include <MQTT.h>          // arduino-mqtt (Joel Gaehwiler)
#include <ArduinoJson.h>   // v7
#include <time.h>

// ESP32 atuador: acende o LED RGB de cada vaga conforme o estado publicado
// pelo ESP32 dos sensores (src/garagem), no contrato MQTT v1 (CONTRATOS.md).
// Decisão em docs/adr/0008.
//   - Assina garagem/v1/<sensor>/vaga/+/estado (retido): cor de cada vaga
//   - Assina garagem/v1/<sensor>/status (retido, LWT): sensor vivo ou não
//   - Publica o próprio status (online/offline, LWT) em garagem/v1/<este>/status
// Sem ligação com o sensor (Wi-Fi, broker ou sensor offline): todas as vagas
// em vermelho piscando — nunca verde sem certeza (CP12, R2, estado seguro).
// Mapa de pinos em docs/mapa-de-pinos.md.

// ---------- Credenciais (secrets.h, fora do git) ----------
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Falta secrets.h: copie secrets.example.h para secrets.h e preencha"
#endif

#ifndef MQTT_HOST
#error "Falta MQTT_HOST no secrets.h (veja secrets.example.h)"
#endif
#ifndef SENSOR_DEVICE_ID
#error "Falta SENSOR_DEVICE_ID no secrets.h: deviceId do ESP32 dos sensores (aparece no monitor serial dele)"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef MQTT_USER
#define MQTT_USER ""
#endif
#ifndef MQTT_PASSWORD
#define MQTT_PASSWORD ""
#endif

const char* FIRMWARE_VERSAO = "0.1.0";
const int   CONTRATO_VERSAO = 1;

const char* NTP_1 = "pool.ntp.org";
const char* NTP_2 = "time.google.com";

// ---------- Identidade e tópicos ----------
char deviceId[32];          // "esp32-atuador-<mac>"
char topicoStatus[80];      // garagem/v1/<este>/status
char lwtPayload[96];
char topicoSensorBase[96];  // garagem/v1/<sensor>
char topicoSensorEstado[112];
char topicoSensorStatus[112];

WiFiClient rede;
MQTTClient mqtt(1024);

// ---------- Vagas ----------
// Estado recebido do sensor (contrato v1); DESCONHECIDO = ainda sem mensagem
enum Estado { DESCONHECIDO, LIVRE, OCUPADA, ERRO, RESERVADA };

// Cada vaga: {numero, ledVermelho, ledVerde, ledAzul}. LED RGB catodo
// comum: HIGH acende. Mesmos pinos do firmware único anterior.
struct Vaga {
  int numero;
  int ledVermelho;
  int ledVerde;
  int ledAzul;
  Estado estado;
};

Vaga vagas[] = {
  {1, 32, 33, 16},
  {2, 27, 13, 17},
};
const int NUM_VAGAS = sizeof(vagas) / sizeof(vagas[0]);

// ---------- Tempos (ms) ----------
const unsigned long INTERVALO_RECONEXAO = 5000;
const unsigned long PISCA_MS            = 500;
const int           MQTT_TIMEOUT_MS     = 1000;

unsigned long ultimaTentativaMqtt = 0;
bool tentouMqtt = false;
bool wifiEstavaOk = false;
bool mqttEstavaOk = false;
bool ntpIniciado = false;
bool statusPendente = false;
bool sensorOnline = false;  // status retido do sensor; falso até receber "online"
bool ligacaoEstavaOk = false;

// ======================================================================
// Relógio e status (mesmo formato do firmware dos sensores)
// ======================================================================

const time_t EPOCH_VALIDO = 1700000000;

void preencherTs(JsonDocument& doc) {
  time_t epoch = time(nullptr);
  if (epoch > EPOCH_VALIDO) {
    struct tm t;
    gmtime_r(&epoch, &t);
    char ts[25];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &t);
    doc["ts"] = ts;
  } else {
    doc["ts"] = nullptr;
    doc["ts_sincronizado"] = false;
    doc["uptime_ms"] = (uint32_t)millis();
  }
}

// "vagas": [] — o atuador não mede vagas; o contrato exige o campo no
// status online, e a lista vazia evita cartões de vaga duplicados no painel
void publicarStatusOnline() {
  JsonDocument doc;
  doc["v"] = CONTRATO_VERSAO;
  doc["deviceId"] = deviceId;
  preencherTs(doc);
  doc["status"] = "online";
  doc["firmware"] = FIRMWARE_VERSAO;
  doc["vagas"].to<JsonArray>();
  char payload[256];
  size_t n = serializeJson(doc, payload, sizeof(payload));
  if (mqtt.publish(topicoStatus, payload, (int)n, true, 1)) statusPendente = false;
}

// ======================================================================
// Mensagens do sensor
// ======================================================================

Estado estadoPorNome(const char* nome) {
  if (strcmp(nome, "livre") == 0) return LIVRE;
  if (strcmp(nome, "ocupada") == 0) return OCUPADA;
  if (strcmp(nome, "reservada") == 0) return RESERVADA;
  if (strcmp(nome, "erro") == 0) return ERRO;
  return DESCONHECIDO;
}

Vaga* vagaPorNumero(int numero) {
  for (int i = 0; i < NUM_VAGAS; i++) {
    if (vagas[i].numero == numero) return &vagas[i];
  }
  return nullptr;
}

// Tópico garagem/v1/<sensor>/vaga/<n>/estado -> n (0 se não for desse formato)
int vagaDoTopico(const char* topico) {
  size_t base = strlen(topicoSensorBase);
  if (strncmp(topico, topicoSensorBase, base) != 0) return 0;
  int numero = 0;
  char resto[16];
  if (sscanf(topico + base, "/vaga/%d/%15s", &numero, resto) != 2) return 0;
  return strcmp(resto, "estado") == 0 ? numero : 0;
}

void aoReceberMensagem(MQTTClient* cliente, char topico[], char bytes[], int tamanho) {
  JsonDocument doc;
  if (deserializeJson(doc, bytes, tamanho) || !doc.is<JsonObject>()) {
    Serial.printf("Mensagem ilegível em %s\n", topico);
    return;
  }

  if (strcmp(topico, topicoSensorStatus) == 0) {
    bool online = strcmp(doc["status"] | "", "online") == 0;
    if (online != sensorOnline) {
      Serial.printf("Sensor %s: %s\n", SENSOR_DEVICE_ID, online ? "online" : "OFFLINE");
    }
    sensorOnline = online;
    return;
  }

  int numero = vagaDoTopico(topico);
  Vaga* v = vagaPorNumero(numero);
  if (v == nullptr) return;  // vaga que este atuador não tem
  Estado e = estadoPorNome(doc["estado"] | "");
  if (e != v->estado) Serial.printf("Vaga %d: %s\n", v->numero, doc["estado"] | "?");
  v->estado = e;
}

// ======================================================================
// LEDs
// ======================================================================

bool ligacaoOk() {
  return mqtt.connected() && sensorOnline;
}

void acender(const Vaga& v, bool vermelho, bool verde, bool azul) {
  digitalWrite(v.ledVermelho, vermelho ? HIGH : LOW);
  digitalWrite(v.ledVerde, verde ? HIGH : LOW);
  digitalWrite(v.ledAzul, azul ? HIGH : LOW);
}

// Livre: verde. Ocupada: vermelho. Reservada: azul. Erro do sensor ou sem
// ligação com o sensor: vermelho piscando. Ligado, mas ainda sem estado: apagado.
void atualizarLeds() {
  bool pisca = (millis() / PISCA_MS) % 2 == 0;
  bool ok = ligacaoOk();
  if (ok != ligacaoEstavaOk) {
    ligacaoEstavaOk = ok;
    Serial.println(ok ? "Ligação com o sensor OK" : "Sem ligação com o sensor: LEDs em vermelho piscando");
  }
  for (int i = 0; i < NUM_VAGAS; i++) {
    const Vaga& v = vagas[i];
    if (!ok) { acender(v, pisca, false, false); continue; }
    switch (v.estado) {
      case LIVRE:     acender(v, false, true, false); break;
      case OCUPADA:   acender(v, true, false, false); break;
      case RESERVADA: acender(v, false, false, true); break;
      case ERRO:      acender(v, pisca, false, false); break;
      default:        acender(v, false, false, false); break;
    }
  }
}

// ======================================================================
// Rede
// ======================================================================

void gerarIdentidade() {
  uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(deviceId, sizeof(deviceId), "esp32-atuador-%012llx", mac);
  snprintf(topicoStatus, sizeof(topicoStatus), "garagem/v%d/%s/status", CONTRATO_VERSAO, deviceId);
  snprintf(lwtPayload, sizeof(lwtPayload),
           "{\"v\":%d,\"deviceId\":\"%s\",\"status\":\"offline\"}", CONTRATO_VERSAO, deviceId);
  snprintf(topicoSensorBase, sizeof(topicoSensorBase), "garagem/v%d/%s", CONTRATO_VERSAO, SENSOR_DEVICE_ID);
  snprintf(topicoSensorEstado, sizeof(topicoSensorEstado), "%s/vaga/+/estado", topicoSensorBase);
  snprintf(topicoSensorStatus, sizeof(topicoSensorStatus), "%s/status", topicoSensorBase);
  Serial.printf("deviceId: %s\nSensor acompanhado: %s\n", deviceId, SENSOR_DEVICE_ID);
}

void assinar(const char* topico) {
  if (mqtt.subscribe(topico, 1)) {
    Serial.printf("Assinado: %s\n", topico);
  } else {
    Serial.printf("Falha ao assinar %s (erro %d)\n", topico, mqtt.lastError());
  }
}

void aoConectarMqtt() {
  Serial.println("conectado.");
  mqttEstavaOk = true;
  statusPendente = true;
  // Sessão limpa: assina de novo; os retidos (status e estados) chegam em seguida
  sensorOnline = false;
  assinar(topicoSensorStatus);
  assinar(topicoSensorEstado);
}

void cuidarDaRede() {
  bool wifiOk = WiFi.status() == WL_CONNECTED;
  if (wifiOk != wifiEstavaOk) {
    wifiEstavaOk = wifiOk;
    if (wifiOk) {
      Serial.println("Wi-Fi conectado. IP: " + WiFi.localIP().toString());
      if (!ntpIniciado) {
        configTime(0, 0, NTP_1, NTP_2);
        ntpIniciado = true;
      }
    } else {
      Serial.println("Wi-Fi caiu");
    }
  }
  if (!wifiOk) return;

  if (mqtt.connected()) return;
  if (mqttEstavaOk) {
    mqttEstavaOk = false;
    Serial.println("Broker desconectado");
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
    pinMode(vagas[i].ledVermelho, OUTPUT);
    pinMode(vagas[i].ledVerde, OUTPUT);
    pinMode(vagas[i].ledAzul, OUTPUT);
    vagas[i].estado = DESCONHECIDO;
  }

  gerarIdentidade();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("Conectando ao Wi-Fi em segundo plano");

  mqtt.begin(MQTT_HOST, MQTT_PORT, rede);
  mqtt.onMessageAdvanced(aoReceberMensagem);
  mqtt.setWill(topicoStatus, lwtPayload, true, 1);
  mqtt.setKeepAlive(10);
  mqtt.setCleanSession(true);
  mqtt.setTimeout(MQTT_TIMEOUT_MS);
}

void loop() {
  cuidarDaRede();
  mqtt.loop();
  if (mqtt.connected() && statusPendente) publicarStatusOnline();
  atualizarLeds();
}
