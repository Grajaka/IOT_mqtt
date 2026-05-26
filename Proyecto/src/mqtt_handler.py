"""
MQTT Handler for IoT Sensor Data
Receives data from ESP32 via MQTT and stores in MongoDB
"""

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import paho.mqtt.client as mqtt
from flask_pymongo import PyMongo

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MQTTHandler:
    """
    Clase para manejar conexión MQTT y almacenamiento en MongoDB
    """
    
    def __init__(self, mongo_instance: PyMongo, config: dict):
        """
        Inicializar el manejador MQTT
        
        Args:
            mongo_instance: Instancia de PyMongo
            config: Diccionario con configuración MQTT
        """
        self.mongo = mongo_instance
        self.collection = mongo_instance.db.SensorsReaders
        
        # Configuración MQTT
        self.broker = config.get('MQTT_BROKER', 'localhost')
        self.port = config.get('MQTT_PORT', 1883)
        self.username = config.get('MQTT_USERNAME', None)
        self.password = config.get('MQTT_PASSWORD', None)
        self.client_id = config.get('MQTT_CLIENT_ID', 'flask_backend')
        
        # Crear cliente MQTT
        self.client = mqtt.Client(client_id=self.client_id)
        
        # Configurar callbacks
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        
        # Credenciales
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)
        
        # Estadísticas
        self.stats = {
            'messages_received': 0,
            'messages_stored': 0,
            'errors': 0,
            'last_message_time': None
        }
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback cuando se conecta al broker MQTT"""
        if rc == 0:
            logger.info(f"✓ Conectado al broker MQTT: {self.broker}:{self.port}")
            
            # Suscribirse a todos los topics de sensores
            topics = [
                ("cms/sensors/#", 1),      # Todos los sensores con QoS 1
                ("cms/device/status", 1),  # Estado de dispositivos
                ("cms/alerts", 1)          # Alertas
            ]
            
            for topic, qos in topics:
                client.subscribe(topic, qos)
                logger.info(f"  Suscrito a: {topic} (QoS {qos})")
                
        else:
            logger.error(f"✗ Error conectando al broker MQTT. Código: {rc}")
            error_messages = {
                1: "Protocolo incorrecto",
                2: "Client ID inválido",
                3: "Servidor no disponible",
                4: "Usuario/password incorrectos",
                5: "No autorizado"
            }
            logger.error(f"  Razón: {error_messages.get(rc, 'Desconocida')}")
    
    def on_disconnect(self, client, userdata, rc):
        """Callback cuando se desconecta del broker"""
        if rc != 0:
            logger.warning(f"Desconexión inesperada del broker MQTT. Código: {rc}")
        else:
            logger.info("Desconectado del broker MQTT")
    
    def on_message(self, client, userdata, msg):
        """
        Callback cuando llega un mensaje MQTT
        
        Args:
            client: Cliente MQTT
            userdata: Datos de usuario
            msg: Mensaje recibido
        """
        try:
            self.stats['messages_received'] += 1
            self.stats['last_message_time'] = datetime.now(timezone.utc)
            
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.info(f"[MQTT] Topic: {topic}")
            logger.debug(f"[MQTT] Payload: {payload}")
            
            # Parsear JSON
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as e:
                logger.error(f"Error parseando JSON: {e}")
                self.stats['errors'] += 1
                return
            
            # Procesar según el topic
            if topic.startswith("cms/sensors/"):
                self._process_sensor_data(topic, data)
            elif topic == "cms/device/status":
                self._process_device_status(data)
            elif topic == "cms/alerts":
                self._process_alert(data)
            else:
                logger.warning(f"Topic desconocido: {topic}")
        
        except Exception as e:
            logger.error(f"Error procesando mensaje MQTT: {e}")
            self.stats['errors'] += 1
    
    def _process_sensor_data(self, topic: str, data: dict):
        """
        Procesa y almacena datos de sensores
        
        Args:
            topic: Topic MQTT del mensaje
            data: Diccionario con datos del sensor
        """
        try:
            sensor_name = data.get('sensor')
            value = data.get('value')
            unit = data.get('unit', 'N/A')
            
            if sensor_name is None or value is None:
                logger.warning(f"Datos incompletos: {data}")
                return
            
            # Convertir a float
            try:
                numeric_value = float(value)
            except (ValueError, TypeError):
                logger.error(f"Valor no numérico: {value}")
                return
            
            # Preparar documento para MongoDB
            doc = {
                "sensor": sensor_name,
                "value": numeric_value,
                "unit": unit,
                "topic": topic,
                "timestamp": datetime.now(ZoneInfo("America/Bogota")).astimezone(timezone.utc)
            }
            
            # Insertar en MongoDB
            result = self.collection.insert_one(doc)
            self.stats['messages_stored'] += 1
            
            logger.info(
                f"✓ Almacenado: {sensor_name} = {numeric_value:.2f} {unit} "
                f"(ID: {result.inserted_id})"
            )
            
        except Exception as e:
            logger.error(f"Error almacenando datos de sensor: {e}")
            self.stats['errors'] += 1
    
    def _process_device_status(self, data: dict):
        """
        Procesa mensajes de estado de dispositivos
        
        Args:
            data: Diccionario con datos de estado
        """
        device_id = data.get('device', 'unknown')
        status = data.get('status', 'unknown')
        uptime = data.get('uptime', 0)
        rssi = data.get('rssi', 0)
        
        logger.info(
            f"[ESTADO] Dispositivo: {device_id} | "
            f"Status: {status} | Uptime: {uptime}s | RSSI: {rssi}dBm"
        )
        
        # Opcional: almacenar en colección separada de estados
        # self.mongo.db.DeviceStatus.insert_one(data)
    
    def _process_alert(self, data: dict):
        """
        Procesa alertas de sensores
        
        Args:
            data: Diccionario con datos de alerta
        """
        sensor = data.get('sensor', 'unknown')
        value = data.get('value', 0)
        threshold = data.get('threshold', 0)
        unit = data.get('unit', '')
        
        logger.warning(
            f"⚠ [ALERTA] {sensor}: {value:.2f}{unit} "
            f"(umbral: {threshold:.2f}{unit})"
        )
        
        # Opcional: almacenar alertas en colección separada
        # alert_doc = {**data, "timestamp": datetime.now(timezone.utc)}
        # self.mongo.db.Alerts.insert_one(alert_doc)
    
    def connect(self):
        """Conectar al broker MQTT"""
        try:
            logger.info(f"Conectando a broker MQTT: {self.broker}:{self.port}")
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()  # Iniciar loop en thread separado
            logger.info("✓ Cliente MQTT iniciado")
        except Exception as e:
            logger.error(f"✗ Error conectando al broker MQTT: {e}")
            raise
    
    def disconnect(self):
        """Desconectar del broker MQTT"""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("Cliente MQTT desconectado")
    
    def get_stats(self):
        """Obtener estadísticas del manejador"""
        return {
            **self.stats,
            'broker': self.broker,
            'port': self.port,
            'connected': self.client.is_connected()
        }
    
    def publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False):
        """
        Publicar un mensaje MQTT
        
        Args:
            topic: Topic MQTT
            payload: Diccionario con datos
            qos: Quality of Service (0, 1 o 2)
            retain: Si el mensaje debe ser retenido
        """
        try:
            message = json.dumps(payload)
            result = self.client.publish(topic, message, qos=qos, retain=retain)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"✓ Publicado en {topic}: {message}")
            else:
                logger.error(f"✗ Error publicando en {topic}")
                
        except Exception as e:
            logger.error(f"Error en publish: {e}")