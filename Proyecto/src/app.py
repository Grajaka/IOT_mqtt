import os
from flask import Flask, render_template, jsonify, request
from flask_pymongo import PyMongo
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import logging

# Importar el manejador MQTT
from mqtt_handler import MQTTHandler

# ============================
# CONFIGURACIÓN
# ============================

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

# Configuración MongoDB
mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    mongo_uri = "mongodb+srv://kjaramillo1_db_user:nL8dP3yzNhJXpRhC@cluster0.7wetitv.mongodb.net/IOTAdvanced?appName=Cluster0"

# Configuración MQTT
mqtt_config = {
    'MQTT_BROKER': os.environ.get('MQTT_BROKER', 'localhost'),
    'MQTT_PORT': int(os.environ.get('MQTT_PORT', 1883)),
    'MQTT_USERNAME': os.environ.get('MQTT_USERNAME', 'esp32_client'),
    'MQTT_PASSWORD': os.environ.get('MQTT_PASSWORD', 'iot2025secure'),
    'MQTT_CLIENT_ID': 'flask_backend_01'
}

grafana_embed_url = os.environ.get(
    'GRAFANA_EMBED_URL',
    'http://localhost:3000/public-dashboards/65fe92bf244e40dbb7d0e1efd4e4142b?orgId=1&kiosk'
)

# ============================
# INICIALIZAR FLASK
# ============================

app = Flask(__name__)
app.config["MONGO_URI"] = mongo_uri
CORS(app)

# ============================
# INICIALIZAR MONGODB
# ============================

try:
    mongo = PyMongo(app)
    SensorsReaders_collection = mongo.db.SensorsReaders
    SensorsReaders_collection.find_one()
    logger.info("✓ Conectado a MongoDB")
except Exception as e:
    logger.error(f"✗ Error conectando a MongoDB: {e}")
    SensorsReaders_collection = None

# ============================
# INICIALIZAR MQTT HANDLER
# ============================

mqtt_handler = None

if SensorsReaders_collection is not None:
    try:
        mqtt_handler = MQTTHandler(mongo, mqtt_config)
        mqtt_handler.connect()
        logger.info("✓ MQTT Handler inicializado")
    except Exception as e:
        logger.error(f"✗ Error inicializando MQTT Handler: {e}")


# ============================
# FUNCIONES AUXILIARES
# ============================

def parse_grafana_time(value):
    """
    Convierte timestamps de Grafana:
    - ISO8601 → datetime
    - now     → utcnow()
    - now-6h  → utcnow() - 6h
    """
    if not value:
        return None

    # Intentar parsear ISO8601
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except:
        pass

    now = datetime.now(timezone.utc)

    if value == "now":
        return now

    # Manejar now-6h, now-12h, etc.
    if value.startswith("now-") and value.endswith("h"):
        try:
            hours = int(value.replace("now-", "").replace("h", ""))
            return now - timedelta(hours=hours)
        except:
            pass

    return None


# ============================
# RUTAS BÁSICAS
# ============================

@app.route('/', methods=['GET'])
def root():
    """Health check endpoint"""
    status = {
        "status": "online",
        "mongodb": "connected" if SensorsReaders_collection is not None else "disconnected",
        "mqtt": "connected" if mqtt_handler and mqtt_handler.client.is_connected() else "disconnected",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return jsonify(status), 200


@app.route('/health', methods=['GET'])
def health():
    """Endpoint de salud detallado"""
    health_status = {
        "status": "healthy",
        "components": {
            "mongodb": {
                "status": "up" if SensorsReaders_collection is not None else "down"
            },
            "mqtt": {
                "status": "up" if mqtt_handler and mqtt_handler.client.is_connected() else "down",
                "stats": mqtt_handler.get_stats() if mqtt_handler else None
            }
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return jsonify(health_status), 200


# ============================
# ENDPOINT PARA RECIBIR DATOS (HTTP - Retrocompatibilidad)
# ============================

@app.route('/receive_sensor_data', methods=['POST'])
def receive_sensor_data():
    """
    Endpoint HTTP para recibir datos de sensores (retrocompatibilidad).
    RECOMENDADO: Usar MQTT en su lugar.
    """
    if SensorsReaders_collection is None:
        return jsonify({"error": "Database connection is not established."}), 503

    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON payload provided"}), 400
        
        sensor_type = data.get('sensor_type') or data.get('sensor')
        value = data.get('value')
        unit = data.get('unit', 'N/A')

        if sensor_type is None or value is None:
            return jsonify({"error": "Missing required fields"}), 400

        try:
            numeric_value = float(value)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid value type"}), 400
        
        doc_to_insert = {
            "sensor": sensor_type,
            "value": numeric_value,
            "unit": unit,
            "source": "http",  # Marcar fuente
            "timestamp": datetime.now(ZoneInfo("America/Bogota")).astimezone(timezone.utc)
        }
        
        result = SensorsReaders_collection.insert_one(doc_to_insert)

        response_doc = dict(doc_to_insert)
        response_doc['timestamp'] = response_doc['timestamp'].isoformat()
        
        logger.info(f"[HTTP] Datos recibidos: {sensor_type} = {numeric_value} {unit}")
        
        return jsonify({
            "status": "success",
            "message": "Data received via HTTP (consider migrating to MQTT)",
            "mongo_id": str(result.inserted_id),
            "data_received": response_doc
        }), 201
        
    except Exception as e:
        logger.error(f"Error processing HTTP sensor data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================
# GRAFANA - SEARCH
# ============================

@app.route('/search', methods=['GET', 'POST'])
def search():
    """Buscar sensores disponibles"""
    try:
        sensores = SensorsReaders_collection.distinct("sensor")
        return jsonify(sensores), 200
    except Exception as e:
        logger.error(f"Error en /search: {e}")
        return jsonify({"error": str(e)}), 500


# ============================
# GRAFANA - QUERY (Datos para gráficos)
# ============================

@app.route('/query', methods=['POST'])
def query():
    """Endpoint principal para Grafana SimpleJSON"""
    if SensorsReaders_collection is None:
        return jsonify([]), 200

    req = request.get_json(silent=True)

    if not req:
        logger.warning("Empty request in /query")
        return jsonify([]), 200

    # Parsear rango de tiempo
    range_raw = req.get("range", {})
    start_raw = range_raw.get("from")
    end_raw = range_raw.get("to")

    start_dt = parse_grafana_time(start_raw)
    end_dt = parse_grafana_time(end_raw)

    logger.debug(f"Query range: {start_dt} to {end_dt}")

    final_response = []
    targets = req.get("targets", [])

    for t in targets:
        name = t.get("target")
        if not name:
            continue

        query_filter = {"sensor": name}

        # Agregar filtro de tiempo
        if start_dt and end_dt:
            query_filter["timestamp"] = {"$gte": start_dt, "$lte": end_dt}

        cursor = SensorsReaders_collection.find(query_filter).sort("timestamp", 1).limit(5000)

        datapoints = []
        for d in cursor:
            value = d.get("value")
            ts = d.get("timestamp")

            if value is None or ts is None:
                continue

            datapoints.append([
                float(value),
                int(ts.timestamp() * 1000)
            ])

        logger.info(f"{name} → {len(datapoints)} datapoints")

        final_response.append({
            "target": name,
            "datapoints": datapoints
        })

    return jsonify(final_response), 200


# ============================
# GRAFANA - ANNOTATIONS
# ============================

@app.route('/annotations', methods=['GET', 'POST'])
def annotations():
    """Endpoint de anotaciones (requerido por Grafana)"""
    return jsonify([]), 200


# ============================
# GRAFANA INFINITY - QUERY
# ============================

@app.route('/infinity_query', methods=['GET'])
def infinity_query():
    """Endpoint para Grafana Infinity Plugin"""
    if SensorsReaders_collection is None:
        return jsonify({"error": "Database not connected"}), 503

    try:
        data_cursor = SensorsReaders_collection.find().sort("timestamp", -1).limit(1000)
        
        infinity_data = []
        for document in data_cursor:
            value = document.get("value")
            timestamp_dt = document.get("timestamp")
            sensor_name = document.get("sensor", "Unknown")

            if value is not None and timestamp_dt is not None:
                try:
                    numeric_value = float(value)
                    
                    if isinstance(timestamp_dt, datetime):
                        timestamp_iso = timestamp_dt.isoformat().replace('+00:00', 'Z')
                        
                        infinity_data.append({
                            "time": timestamp_iso,
                            "value": numeric_value,
                            "sensor": sensor_name
                        })
                        
                except (ValueError, TypeError) as e:
                    logger.warning(f"Conversion error: {e}")
        
        return jsonify(infinity_data), 200

    except Exception as e:
        logger.error(f"Error in infinity_query: {e}")
        return jsonify([]), 500


# ============================
# DASHBOARD
# ============================

@app.route('/dashboard')
def dashboard():
    """Muestra el dashboard embebido de Grafana"""
    return render_template('dashboard.html', grafana_embed_url=grafana_embed_url)


# ============================
# DEBUG ENDPOINTS
# ============================

@app.route('/debug/last')
def debug_last():
    """Ver últimos 20 documentos"""
    docs = list(SensorsReaders_collection.find().sort("timestamp", -1).limit(20))
    for d in docs:
        d["_id"] = str(d["_id"])
        d["timestamp"] = d["timestamp"].isoformat()
    return jsonify(docs)


@app.route('/debug/mqtt/stats')
def debug_mqtt_stats():
    """Estadísticas del manejador MQTT"""
    if mqtt_handler:
        return jsonify(mqtt_handler.get_stats())
    return jsonify({"error": "MQTT handler not initialized"}), 503


# ============================
# MQTT CONTROL ENDPOINTS
# ============================

@app.route('/mqtt/publish', methods=['POST'])
def mqtt_publish():
    """Publicar un mensaje MQTT desde el backend"""
    if not mqtt_handler:
        return jsonify({"error": "MQTT not initialized"}), 503
    
    data = request.get_json()
    topic = data.get('topic')
    payload = data.get('payload')
    qos = data.get('qos', 1)
    
    if not topic or not payload:
        return jsonify({"error": "Missing topic or payload"}), 400
    
    try:
        mqtt_handler.publish(topic, payload, qos=qos)
        return jsonify({"status": "success", "topic": topic}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================
# CLEANUP AL CERRAR
# ============================

import atexit

def cleanup():
    """Limpieza al cerrar la aplicación"""
    if mqtt_handler:
        logger.info("Cerrando conexión MQTT...")
        mqtt_handler.disconnect()

atexit.register(cleanup)


# ============================
# MAIN
# ============================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)