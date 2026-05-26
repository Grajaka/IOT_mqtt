#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>  // MQTT Client
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include "EmonLib.h"
#include "ESP_Mail_Client.h"

// ============================================
// CONFIGURACIÓN DE RED
// ============================================
const char* ssid = "POCO X7 Pro";
const char* password = "12345678.";

// ============================================
// CONFIGURACIÓN MQTT
// ============================================
const char* mqtt_server = "iotadvancedprgm.onrender.com";  // Tu servidor
const int mqtt_port = 1883;
const char* mqtt_user = "esp32_client";     // Usuario MQTT
const char* mqtt_password = "iot2025secure"; // Password MQTT
const char* mqtt_client_id = "ESP32_CMS_01"; // ID único

// Topics MQTT (estructura jerárquica)
const char* topic_temperature = "cms/sensors/temperature";
const char* topic_current = "cms/sensors/current";
const char* topic_status = "cms/device/status";
const char* topic_alerts = "cms/alerts";

// ============================================
// CONFIGURACIÓN EMAIL
// ============================================
#define emailSenderAccount    "iotsensors74@gmail.com"
#define emailSenderPassword   "**************"
#define smtpServer            "smtp.gmail.com"
#define smtpServerPort        465
String inputMessage = "karojg24@gmail.com";

// ============================================
// UMBRALES DE ALERTA
// ============================================
const float TEMP_THRESHOLD = 25.0;
const float CURRENT_THRESHOLD = 0.3;

// ============================================
// PINES Y CALIBRACIÓN
// ============================================
#define ONE_WIRE_BUS 4
#define CURRENT_SENSOR_PIN 17

const float CALIBRATION_FACTOR = 5.51;
const float NOISE_THRESHOLD = 0.10;
float adcOffset = 0.0;

// ============================================
// INSTANCIAS
// ============================================
WiFiClient espClient;
PubSubClient mqttClient(espClient);  // Cliente MQTT

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
EnergyMonitor emon1;

// ============================================
// VARIABLES COMPARTIDAS
// ============================================
float currentTemp = 0.0;
float currentCurrent = 0.0;
bool emailSent = false;

SemaphoreHandle_t xMutex;
QueueHandle_t emailQueue;

struct EmailAlert {
  char sensor[20];
  float value;
  char unit[5];
  float threshold;
};

// ============================================
// PROTOTIPOS DE FUNCIONES
// ============================================
void setupWiFi();
void reconnectMQTT();
void mqttCallback(char* topic, byte* payload, unsigned int length);
void publishSensorData(const char* topic, const char* sensor_name, float value, const char* unit);
void publishStatus();
float calibrateADCOffset(int samples = 1000);
float readCurrent();
void sendAlertEmail(const char* sensor, float value, const char* unit, float threshold);
void smtpCallback(SMTP_Status status);

// ============================================
// SETUP WIFI
// ============================================
void setupWiFi() {
  delay(10);
  Serial.println("\n[WiFi] Conectando a " + String(ssid));
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WiFi] ✓ Conectado!");
    Serial.print("[WiFi] IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WiFi] ✗ Fallo en conexión");
  }
}

// ============================================
// MQTT CALLBACK (mensajes entrantes)
// ============================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  Serial.print("[MQTT] Mensaje recibido en topic: ");
  Serial.println(topic);
  
  // Convertir payload a string
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  
  Serial.print("[MQTT] Contenido: ");
  Serial.println(message);
  
  // Aquí puedes procesar comandos remotos si los necesitas
  // Ejemplo: cambiar umbrales dinámicamente
}

// ============================================
// RECONNECT MQTT
// ============================================
void reconnectMQTT() {
  // Loop hasta conectar
  while (!mqttClient.connected()) {
    Serial.print("[MQTT] Intentando conexión...");
    
    // Intento de conexión con credenciales
    if (mqttClient.connect(mqtt_client_id, mqtt_user, mqtt_password)) {
      Serial.println(" ✓ Conectado!");
      
      // Suscribirse a topics de comandos (opcional)
      mqttClient.subscribe("cms/commands/#");
      
      // Publicar estado inicial
      publishStatus();
      
    } else {
      Serial.print(" ✗ Falló, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" Reintentando en 5s...");
      delay(5000);
    }
  }
}

// ============================================
// PUBLICAR DATOS DE SENSOR VÍA MQTT
// ============================================
void publishSensorData(const char* topic, const char* sensor_name, float value, const char* unit) {
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  
  // Crear JSON compacto
  StaticJsonDocument<200> doc;
  doc["sensor"] = sensor_name;
  doc["value"] = value;
  doc["unit"] = unit;
  doc["timestamp"] = millis();
  
  char buffer[256];
  serializeJson(doc, buffer);
  
  // Publicar con QoS 1 (al menos una vez)
  bool published = mqttClient.publish(topic, buffer, true); // retained=true
  
  if (published) {
    Serial.printf("[MQTT] ✓ Publicado en %s: %s\n", topic, buffer);
  } else {
    Serial.printf("[MQTT] ✗ Error publicando en %s\n", topic);
  }
}

// ============================================
// PUBLICAR ESTADO DEL DISPOSITIVO
// ============================================
void publishStatus() {
  StaticJsonDocument<200> doc;
  doc["device"] = mqtt_client_id;
  doc["status"] = "online";
  doc["uptime"] = millis() / 1000;
  doc["rssi"] = WiFi.RSSI();
  
  char buffer[256];
  serializeJson(doc, buffer);
  
  mqttClient.publish(topic_status, buffer, true);
  Serial.println("[MQTT] Estado publicado");
}

// ============================================
// CALIBRACIÓN ADC
// ============================================
float calibrateADCOffset(int samples) {
  Serial.println("\n[CALIBRACIÓN] Midiendo offset del ADC...");
  Serial.println("[CALIBRACIÓN] NO debe haber corriente en el sensor");
  
  delay(2000);
  
  long sum = 0;
  for(int i = 0; i < samples; i++) {
    sum += analogRead(CURRENT_SENSOR_PIN);
    delayMicroseconds(100);
  }
  
  float offset = sum / (float)samples;
  Serial.printf("[CALIBRACIÓN] Offset: %.2f (%.3fV)\n", offset, (offset/4095.0)*3.3);
  
  return offset;
}

// ============================================
// LEER CORRIENTE RMS
// ============================================
float readCurrent() {
  double Irms = emon1.calcIrms(1480);
  
  if (Irms < NOISE_THRESHOLD) {
    return 0.0;
  }
  
  return Irms;
}

// ============================================
// SMTP CALLBACK
// ============================================
void smtpCallback(SMTP_Status status) {
  Serial.println(status.info());
  if (status.success()) {
    Serial.println("[EMAIL] ✓ Enviado exitosamente");
  }
}

// ============================================
// ENVIAR ALERTA POR EMAIL
// ============================================
void sendAlertEmail(const char* sensor, float value, const char* unit, float threshold) {
  Serial.println("\n=== [CORE 0] ENVIANDO EMAIL ===");
  
  SMTPSession smtp;
  ESP_Mail_Session session;
  
  session.server.host_name = smtpServer;
  session.server.port = smtpServerPort;
  session.login.email = emailSenderAccount;
  session.login.password = emailSenderPassword;
  
  session.time.ntp_server = F("pool.ntp.org");
  session.time.gmt_offset = -5;
  session.time.day_light_offset = 0;
  
  smtp.debug(1);
  smtp.callback(smtpCallback);
  
  SMTP_Message message;
  message.sender.name = F("ESP32 IoT Alert");
  message.sender.email = emailSenderAccount;
  message.addRecipient(F("Usuario"), inputMessage);
  
  String subj = "[ALERTA] " + String(sensor) + " superó umbral!";
  message.subject = subj;
  
  String body = "ALERTA DE SENSOR\n\n";
  body += "Sensor: " + String(sensor) + "\n";
  body += "Umbral: " + String(threshold, 2) + " " + String(unit) + "\n";
  body += "Valor actual: " + String(value, 2) + " " + String(unit) + "\n\n";
  body += "Timestamp: " + String(millis()/1000) + "s\n";
  body += "\n--- ESP32 CMS System ---";
  
  message.text.content = body.c_str();
  message.text.charSet = F("utf-8");
  message.text.transfer_encoding = Content_Transfer_Encoding::enc_7bit;
  message.priority = esp_mail_smtp_priority::esp_mail_smtp_priority_high;
  
  if (!smtp.connect(&session)) {
    Serial.println("[EMAIL] ✗ Error conectando SMTP");
    return;
  }
  
  if (!MailClient.sendMail(&smtp, &message)) {
    Serial.println("[EMAIL] ✗ Error enviando");
  } else {
    Serial.println("[EMAIL] ✓ Enviado correctamente");
  }
  
  smtp.closeSession();
}

// ============================================
// TAREA: EMAIL (NÚCLEO 0)
// ============================================
void emailTask(void * parameter) {
  Serial.println("[CORE 0] Tarea Email iniciada");
  
  EmailAlert alert;
  
  for(;;) {
    if(xQueueReceive(emailQueue, &alert, portMAX_DELAY)) {
      Serial.printf("[CORE 0] Alerta recibida: %s = %.2f %s\n", 
                    alert.sensor, alert.value, alert.unit);
      
      // Enviar email
      sendAlertEmail(alert.sensor, alert.value, alert.unit, alert.threshold);
      
      // También publicar alerta vía MQTT
      StaticJsonDocument<200> doc;
      doc["sensor"] = alert.sensor;
      doc["value"] = alert.value;
      doc["unit"] = alert.unit;
      doc["threshold"] = alert.threshold;
      doc["timestamp"] = millis();
      
      char buffer[256];
      serializeJson(doc, buffer);
      mqttClient.publish(topic_alerts, buffer, true);
      
      vTaskDelay(1000 / portTICK_PERIOD_MS);
    }
  }
}

// ============================================
// TAREA: SENSORES Y MQTT (NÚCLEO 1)
// ============================================
void sensorAndMqttTask(void * parameter) {
  Serial.println("[CORE 1] Tarea Sensores y MQTT iniciada");
  
  unsigned long previousMillis = 0;
  unsigned long previousStatusMillis = 0;
  const long interval = 2000;        // Leer cada 2s
  const long statusInterval = 30000; // Estado cada 30s
  
  for(;;) {
    unsigned long currentMillis = millis();
    
    // Mantener conexión MQTT
    if (!mqttClient.connected()) {
      reconnectMQTT();
    }
    mqttClient.loop(); // Procesar mensajes MQTT
    
    // === LECTURA DE SENSORES ===
    if (currentMillis - previousMillis >= interval) {
      previousMillis = currentMillis;
      
      sensors.requestTemperatures();
      float tempC = sensors.getTempCByIndex(0);
      float Irms = readCurrent();
      
      // Debug cada 10 lecturas
      static int debugCounter = 0;
      if(debugCounter++ % 10 == 0) {
        int rawADC = analogRead(CURRENT_SENSOR_PIN);
        Serial.printf("[DEBUG] ADC Raw: %d, Corriente: %.3fA\n", rawADC, Irms);
      }
      
      // Actualizar variables compartidas
      if(xSemaphoreTake(xMutex, portMAX_DELAY)) {
        currentTemp = tempC;
        currentCurrent = Irms;
        xSemaphoreGive(xMutex);
      }
      
      // === VERIFICAR ALERTAS ===
      bool isTempAlert = (tempC != -127.00) && (tempC > TEMP_THRESHOLD);
      bool isCurrentAlert = (Irms > CURRENT_THRESHOLD);
      
      if (isTempAlert || isCurrentAlert) {
        if (!emailSent) {
          EmailAlert alert;
          
          if (isTempAlert) {
            Serial.printf("\n[ALERTA] Temperatura %.2f°C > %.2f°C\n", 
                         tempC, TEMP_THRESHOLD);
            strcpy(alert.sensor, "Temperature_01");
            alert.value = tempC;
            strcpy(alert.unit, "C");
            alert.threshold = TEMP_THRESHOLD;
          } else if (isCurrentAlert) {
            Serial.printf("\n[ALERTA] Corriente %.2fA > %.2fA\n", 
                         Irms, CURRENT_THRESHOLD);
            strcpy(alert.sensor, "Current_01");
            alert.value = Irms;
            strcpy(alert.unit, "A");
            alert.threshold = CURRENT_THRESHOLD;
          }
          
          xQueueSend(emailQueue, &alert, portMAX_DELAY);
          emailSent = true;
        }
      } else {
        emailSent = false;
      }
      
      // === PUBLICAR VÍA MQTT ===
      if(tempC != -127.00) {
        publishSensorData(topic_temperature, "Temperature_01", tempC, "C");
      }
      publishSensorData(topic_current, "Current_01", Irms, "A");
    }
    
    // === PUBLICAR ESTADO PERIÓDICO ===
    if (currentMillis - previousStatusMillis >= statusInterval) {
      previousStatusMillis = currentMillis;
      publishStatus();
    }
    
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// ============================================
// SETUP
// ============================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\n\n=== ESP32 DUAL CORE - MQTT VERSION ===");
  
  // Crear mutex y cola
  xMutex = xSemaphoreCreateMutex();
  emailQueue = xQueueCreate(5, sizeof(EmailAlert));
  
  // Conectar WiFi
  setupWiFi();
  
  // Configurar MQTT
  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512); // Aumentar buffer si es necesario
  
  // Configurar sensores
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  sensors.begin();
  
  adcOffset = calibrateADCOffset(1000);
  emon1.current(CURRENT_SENSOR_PIN, CALIBRATION_FACTOR);
  
  Serial.println("✓ Sensores inicializados");
  
  // Conectar MQTT
  reconnectMQTT();
  
  // === CREAR TAREAS ===
  xTaskCreatePinnedToCore(
    emailTask,
    "EmailTask",
    10000,
    NULL,
    1,
    NULL,
    0  // Núcleo 0
  );
  
  xTaskCreatePinnedToCore(
    sensorAndMqttTask,
    "SensorMqttTask",
    8000,
    NULL,
    1,
    NULL,
    1  // Núcleo 1
  );
  
  Serial.println("✓ Tareas creadas:");
  Serial.println("  - NÚCLEO 0: Email");
  Serial.println("  - NÚCLEO 1: Sensores y MQTT");
  Serial.println("======================\n");
}

// ============================================
// LOOP
// ============================================
void loop() {
  vTaskDelay(1000 / portTICK_PERIOD_MS);
}