#include <WiFi.h>
#include <PubSubClient.h>

// ---------- Configurações Wi-Fi ----------
// Rede e senha ficam em secrets.h, que NÃO vai para o git (.gitignore).
// Copie secrets.example.h para secrets.h nesta mesma pasta e preencha.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Falta secrets.h: copie secrets.example.h para secrets.h e preencha WIFI_SSID e WIFI_PASSWORD"
#endif

const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// ---------- Configurações MQTT ----------
//const char* mqtt_server = "192.168.1.100"; // IP do broker Mosquitto
//const int   mqtt_port   = 1883;
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;

// Raiz dos tópicos — deve bater EXATAMENTE com o atuador e o app
//   <base>/vaga/<numero>       -> "livre" / "ocupada" (retained)
//   <base>/no/<id>/status      -> "online" / "offline" (retained, LWT)
//   <base>/no/<id>/vagas       -> lista das vagas deste nó, ex.: "1,2,3"
const char* topico_base = "garagem";

// ID do nó, gerado a partir do MAC em setup() — único por placa, então o
// mesmo sketch pode ser gravado em vários ESP32 sem conflito no broker
char no_id[13];          // MAC em hex, ex.: "240ac4000110"
char mqtt_client_id[32]; // "esp32-sensor-<no_id>"
char topico_status[64];  // "<base>/no/<no_id>/status"


WiFiClient espClient;
PubSubClient client(espClient);

// ---------- Definição das vagas ----------
// Cada vaga: {numero, trigPin, echoPin, ocupada, pendente}
// numero   = número da vaga na garagem (único entre TODOS os nós sensores;
//            é o mesmo número usado no atuador)
// pendente = estado ainda não publicado no broker (no boot, após
//            reconexão ou se o publish falhar)
struct Vaga {
  int numero;
  int trig;
  int echo;
  bool ocupada;
  bool pendente;
};

Vaga vagas[] = {
  {1, 5, 18, false, true},
};
const int NUM_VAGAS = sizeof(vagas) / sizeof(vagas[0]);

// Distância (cm) abaixo da qual consideramos a vaga ocupada
const float LIMIAR_CM = 30.0;

// Intervalo entre leituras (ms)
const unsigned long INTERVALO = 1000;
unsigned long ultimaLeitura = 0;

// ---------- Mede distância de um sensor ----------
float medirDistancia(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  // timeout de 30ms (~5m) evita travar se não houver eco
  long duracao = pulseIn(echo, HIGH, 30000);
  if (duracao == 0) return -1; // sem leitura válida

  return duracao * 0.0343 / 2.0; // cm
}

// ---------- Conexão Wi-Fi ----------
void conectarWiFi() {
  WiFi.begin(ssid, password);
  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi conectado. IP: " + WiFi.localIP().toString());
}

// ---------- Identidade do nó ----------
void gerarIdentidade() {
  uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(no_id, sizeof(no_id), "%012llx", mac);
  snprintf(mqtt_client_id, sizeof(mqtt_client_id), "esp32-sensor-%s", no_id);
  snprintf(topico_status, sizeof(topico_status), "%s/no/%s/status", topico_base, no_id);
  Serial.printf("No sensor: %s\n", no_id);
}

// Publica (retained) quais vagas este nó monitora
void publicarListaVagas() {
  char topico[64];
  snprintf(topico, sizeof(topico), "%s/no/%s/vagas", topico_base, no_id);

  char lista[128] = "";
  for (int i = 0; i < NUM_VAGAS; i++) {
    size_t len = strlen(lista);
    snprintf(lista + len, sizeof(lista) - len, "%s%d", i ? "," : "", vagas[i].numero);
  }
  client.publish(topico, lista, true);
}

// ---------- Reconexão MQTT ----------
void conectarMQTT() {
  while (!client.connected()) {
    Serial.print("Conectando ao broker MQTT...");
    // Last Will: se o nó cair sem desconectar, o broker publica "offline"
    // (retained) no tópico de status — o app marca as vagas como sem dados
    if (client.connect(mqtt_client_id, topico_status, 1, true, "offline")) {
      Serial.println("conectado.");
      client.publish(topico_status, "online", true);
      publicarListaVagas();
      // Republica o estado de todas as vagas: mudanças ocorridas enquanto
      // estava desconectado não chegaram ao broker
      for (int i = 0; i < NUM_VAGAS; i++) vagas[i].pendente = true;
    } else {
      Serial.print("falhou, rc=");
      Serial.print(client.state());
      Serial.println(" tentando de novo em 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < NUM_VAGAS; i++) {
    pinMode(vagas[i].trig, OUTPUT);
    pinMode(vagas[i].echo, INPUT);
    digitalWrite(vagas[i].trig, LOW);
  }

  gerarIdentidade();
  conectarWiFi();
  client.setServer(mqtt_server, mqtt_port);
}

void loop() {
  if (!client.connected()) conectarMQTT();
  client.loop();

  if (millis() - ultimaLeitura >= INTERVALO) {
    ultimaLeitura = millis();

    for (int i = 0; i < NUM_VAGAS; i++) {
      float dist = medirDistancia(vagas[i].trig, vagas[i].echo);
      bool ocupadaAgora = (dist > 0 && dist < LIMIAR_CM);

      // Publica só quando o estado muda (evita tráfego desnecessário)
      // ou quando há estado pendente (boot / reconexão / falha anterior)
      if (ocupadaAgora != vagas[i].ocupada || vagas[i].pendente) {
        vagas[i].ocupada = ocupadaAgora;

        char topico[64];
        snprintf(topico, sizeof(topico), "%s/vaga/%d", topico_base, vagas[i].numero);
        const char* estado = ocupadaAgora ? "ocupada" : "livre";

        // retained; se falhar, tenta de novo na próxima leitura
        vagas[i].pendente = !client.publish(topico, estado, true);
        Serial.printf("Vaga %d: %s (%.1f cm)\n", vagas[i].numero, estado, dist);
      }
    }
  }
}
