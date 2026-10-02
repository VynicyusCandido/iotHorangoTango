#include <WiFi.h>
#include <PubSubClient.h>

// ---------- Configurações Wi-Fi ----------
const char* ssid = "taques";
const char* password = "ABCDEFGH";

// ---------- Configurações MQTT ----------
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;

// Raiz dos tópicos — deve bater EXATAMENTE com o nó sensor e o app
//   <base>/vaga/<numero>       -> "livre" / "ocupada"
//   <base>/no/<id>/status      -> "online" / "offline" (Last Will do sensor)
//   <base>/no/<id>/vagas       -> vagas monitoradas pelo nó, ex.: "1,2,3"
const char* topico_base = "garagem";

char topico_vagas[48];   // "<base>/vaga/#"
char topico_nos[48];     // "<base>/no/#"
char prefixo_vaga[48];   // "<base>/vaga/"
char prefixo_no[48];     // "<base>/no/"

// Client ID gerado a partir do MAC em setup() — único por placa
char mqtt_client_id[32]; // "esp32-atuador-<mac>"

WiFiClient espClient;
PubSubClient client(espClient);

// ---------- Definição das vagas ----------
// Cada vaga: {numero, ledVerde, ledVermelho}
// numero = número da vaga na garagem (o mesmo configurado no nó sensor);
//          a ordem no array não importa
struct Vaga {
  int numero;
  int ledVerde;
  int ledVermelho;
  bool conhecida;  // já recebeu algum estado do broker
  bool ocupada;    // último estado recebido
  uint32_t donos;  // bits dos nós sensores que monitoram esta vaga (índices em nos[])
};

Vaga vagas[] = {
  {1, 22, 23},
};

const int NUM_VAGAS = sizeof(vagas) / sizeof(vagas[0]);

// ---------- Nós sensores conhecidos ----------
// Preenchido pelos tópicos <base>/no/<id>/... (até 32 nós, um bit cada em Vaga::donos)
struct No {
  bool usado;
  char id[16];
  bool online;
};

const int MAX_NOS = 32;
No nos[MAX_NOS];

// ---------- Procura a vaga pelo número (-1 se não for deste atuador) ----------
int indiceDaVaga(int numero) {
  for (int i = 0; i < NUM_VAGAS; i++) {
    if (vagas[i].numero == numero) return i;
  }
  return -1;
}

// ---------- Procura (ou cria) o nó pelo id; -1 se a tabela estiver cheia ----------
int indiceDoNo(const char* id, bool criar) {
  int livre = -1;
  for (int i = 0; i < MAX_NOS; i++) {
    if (nos[i].usado && strcmp(nos[i].id, id) == 0) return i;
    if (!nos[i].usado && livre < 0) livre = i;
  }
  if (!criar || livre < 0) return -1;
  nos[livre].usado = true;
  strncpy(nos[livre].id, id, sizeof(nos[livre].id) - 1);
  nos[livre].id[sizeof(nos[livre].id) - 1] = '\0';
  nos[livre].online = true;
  return livre;
}

// Sem dados: todos os nós que monitoram a vaga estão offline.
// Vagas que nenhum nó declara (firmware antigo) seguem o último estado.
bool semDados(int indice) {
  uint32_t donos = vagas[indice].donos;
  if (donos == 0) return false;
  for (int i = 0; i < MAX_NOS; i++) {
    if ((donos & (1UL << i)) && nos[i].online) return false;
  }
  return true;
}

// ---------- Atualiza os LEDs de uma vaga ----------
// Livre: verde. Ocupada: vermelho. Sem dados ou estado desconhecido: apagados.
void atualizarLeds(int indice) {
  Vaga& v = vagas[indice];
  bool apagar = !v.conhecida || semDados(indice);
  digitalWrite(v.ledVerde,    !apagar && !v.ocupada ? HIGH : LOW);
  digitalWrite(v.ledVermelho, !apagar &&  v.ocupada ? HIGH : LOW);
}

void atualizarTodosLeds() {
  for (int i = 0; i < NUM_VAGAS; i++) atualizarLeds(i);
}

// ---------- Mensagem em <base>/vaga/<numero> ----------
void tratarVaga(const char* resto, const char* msg) {
  int numVaga = atoi(resto);
  int indice = indiceDaVaga(numVaga);
  if (indice < 0) return; // vaga de outro atuador

  if (msg[0] == '\0') {
    vagas[indice].conhecida = false; // retained apagado: vaga removida
  } else {
    vagas[indice].conhecida = true;
    vagas[indice].ocupada = (strcmp(msg, "ocupada") == 0);
  }
  atualizarLeds(indice);

  Serial.printf("Vaga %d -> %s\n", numVaga,
                !vagas[indice].conhecida ? "SEM ESTADO (apagado)" :
                semDados(indice)         ? "SEM DADOS (apagado)"  :
                vagas[indice].ocupada    ? "OCUPADA (vermelho)"   : "LIVRE (verde)");
}

// ---------- Mensagem em <base>/no/<id>/<campo> ----------
void tratarNo(const char* resto, char* msg) {
  const char* barra = strchr(resto, '/');
  if (!barra) return;

  char id[16];
  size_t len = barra - resto;
  if (len == 0 || len >= sizeof(id)) return;
  memcpy(id, resto, len);
  id[len] = '\0';
  const char* campo = barra + 1;

  if (strcmp(campo, "status") == 0) {
    if (msg[0] == '\0') {
      // retained apagado: nó removido
      int n = indiceDoNo(id, false);
      if (n < 0) return;
      nos[n].usado = false;
      for (int i = 0; i < NUM_VAGAS; i++) vagas[i].donos &= ~(1UL << n);
    } else {
      int n = indiceDoNo(id, true);
      if (n < 0) return;
      nos[n].online = (strcmp(msg, "offline") != 0);
      Serial.printf("No %s -> %s\n", id, nos[n].online ? "online" : "OFFLINE");
    }
  } else if (strcmp(campo, "vagas") == 0) {
    int n = indiceDoNo(id, true);
    if (n < 0) return;
    // Refaz a lista de vagas deste nó, ex.: "1,2,3"
    for (int i = 0; i < NUM_VAGAS; i++) vagas[i].donos &= ~(1UL << n);
    for (char* tok = strtok(msg, ","); tok; tok = strtok(NULL, ",")) {
      int indice = indiceDaVaga(atoi(tok));
      if (indice >= 0) vagas[indice].donos |= (1UL << n);
    }
  } else {
    return;
  }
  atualizarTodosLeds();
}

// ---------- Callback: chamado a cada mensagem recebida ----------
void callback(char* topic, byte* payload, unsigned int length) {
  // Monta a mensagem como string (cabe a lista de vagas de um nó)
  char msg[128];
  unsigned int n = length < sizeof(msg) - 1 ? length : sizeof(msg) - 1;
  memcpy(msg, payload, n);
  msg[n] = '\0';

  if (strncmp(topic, prefixo_vaga, strlen(prefixo_vaga)) == 0) {
    tratarVaga(topic + strlen(prefixo_vaga), msg);
  } else if (strncmp(topic, prefixo_no, strlen(prefixo_no)) == 0) {
    tratarNo(topic + strlen(prefixo_no), msg);
  }
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

// ---------- Reconexão MQTT ----------
void conectarMQTT() {
  while (!client.connected()) {
    Serial.print("Conectando ao broker MQTT...");
    if (client.connect(mqtt_client_id)) {
      Serial.println("conectado.");
      // Assina vagas e status dos nós ao (re)conectar; os retained chegam em seguida
      client.subscribe(topico_vagas);
      client.subscribe(topico_nos);
      Serial.printf("Assinado: %s e %s\n", topico_vagas, topico_nos);
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

  uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(mqtt_client_id, sizeof(mqtt_client_id), "esp32-atuador-%012llx", mac);
  snprintf(topico_vagas, sizeof(topico_vagas), "%s/vaga/#", topico_base);
  snprintf(topico_nos,   sizeof(topico_nos),   "%s/no/#",   topico_base);
  snprintf(prefixo_vaga, sizeof(prefixo_vaga), "%s/vaga/",  topico_base);
  snprintf(prefixo_no,   sizeof(prefixo_no),   "%s/no/",    topico_base);

  // Configura os pinos dos LEDs; ficam apagados até chegar o estado do broker
  for (int i = 0; i < NUM_VAGAS; i++) {
    pinMode(vagas[i].ledVerde, OUTPUT);
    pinMode(vagas[i].ledVermelho, OUTPUT);
    atualizarLeds(i);
  }

  conectarWiFi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) conectarMQTT();
  client.loop();
}
