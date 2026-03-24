"""Evey MQTT — singleton client that survives plugin reimports.

Uses sys module attribute to persist the MQTT client across hermes
session reloads. Each session reimports plugins fresh, but sys persists.
"""
import json
import logging
import os
import sys
import threading
import time

try:
    import paho.mqtt.client as paho_mqtt
except ImportError:
    paho_mqtt = None

logger = logging.getLogger("evey.mqtt")

MQTT_HOST = os.environ.get("MQTT_HOST", "hermes-mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

SUBSCRIPTIONS = [
    "evey/bridge/#",
    "evey/events/#",
    "evey/health/#",
    "evey/mother/#",
]

# ---- Process-level singleton via sys attribute ----
_KEY = '_evey_mqtt_singleton'

def _get_state():
    if not hasattr(sys, _KEY):
        setattr(sys, _KEY, {
            'client': None,
            'messages': [],
            'lock': threading.Lock(),
            'connected': False,
        })
    return getattr(sys, _KEY)


def _on_connect(client, userdata, flags, reason_code, properties=None):
    state = _get_state()
    if not state['connected']:
        logger.info(f"MQTT connected to {MQTT_HOST}:{MQTT_PORT}")
    state['connected'] = True
    for topic in SUBSCRIPTIONS:
        client.subscribe(topic, qos=1)


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    state = _get_state()
    if state['connected']:
        logger.info("MQTT disconnected, will auto-reconnect")
    state['connected'] = False


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode()) if msg.payload else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {"raw": msg.payload.decode(errors="replace")}

    state = _get_state()
    with state['lock']:
        state['messages'].append({
            "topic": msg.topic,
            "payload": payload,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        while len(state['messages']) > 100:
            state['messages'].pop(0)


def _connect():
    state = _get_state()
    # Already connected? Don't create another client.
    if state['client'] is not None and state['connected']:
        return state['client']
    # Client exists but disconnected? Let paho handle reconnect.
    if state['client'] is not None:
        return state['client']

    if paho_mqtt is None:
        return None

    try:
        cid = f"evey-{os.getpid()}"
        client = paho_mqtt.Client(
            paho_mqtt.CallbackAPIVersion.VERSION2,
            client_id=cid,
            clean_session=True,
        )
        client.on_connect = _on_connect
        client.on_disconnect = _on_disconnect
        client.on_message = _on_message
        client.reconnect_delay_set(min_delay=5, max_delay=60)
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        state['client'] = client
        return client
    except Exception as e:
        logger.error(f"MQTT connection failed: {e}")
        return None


# ---- Tool schemas ----

SUBSCRIBE_SCHEMA = {
    "name": "mqtt_subscribe",
    "description": "Check buffered MQTT notifications. Auto-connects on startup.",
    "parameters": {"type": "object", "properties": {
        "topic_filter": {"type": "string", "description": "Optional filter"},
        "keep": {"type": "boolean", "description": "Don't clear after reading"},
    }},
}

PUBLISH_SCHEMA = {
    "name": "mqtt_publish_event",
    "description": "Publish an event to MQTT.",
    "parameters": {"type": "object", "properties": {
        "topic": {"type": "string", "description": "Topic suffix"},
        "message": {"type": "string", "description": "Event content"},
    }, "required": ["topic", "message"]},
}

STATUS_SCHEMA = {
    "name": "mqtt_status",
    "description": "Check MQTT connection status.",
    "parameters": {"type": "object", "properties": {}},
}


def handle_subscribe(args, **kwargs):
    state = _get_state()
    topic_filter = args.get("topic_filter", "")
    keep = args.get("keep", False)
    with state['lock']:
        if topic_filter:
            filtered = [m for m in state['messages'] if topic_filter in m["topic"]]
        else:
            filtered = list(state['messages'])
        if not keep:
            if topic_filter:
                for m in filtered:
                    if m in state['messages']:
                        state['messages'].remove(m)
            else:
                state['messages'].clear()
    if not filtered:
        return json.dumps({"status": "empty", "connected": state['connected'], "message": "No new MQTT notifications."})
    return json.dumps({"status": "ok", "connected": state['connected'], "notifications": filtered, "count": len(filtered)})


def handle_publish(args, **kwargs):
    client = _connect()
    if not client:
        return json.dumps({"error": "MQTT not available"})
    topic = args.get("topic", "general")
    message = args.get("message", "")
    try:
        client.publish(f"evey/events/{topic}", json.dumps({
            "from": "evey", "message": message,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }), qos=1)
        return json.dumps({"status": "published", "topic": f"evey/events/{topic}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_status(args, **kwargs):
    state = _get_state()
    with state['lock']:
        buffered = len(state['messages'])
    return json.dumps({
        "connected": state['connected'],
        "host": MQTT_HOST,
        "port": MQTT_PORT,
        "buffered_messages": buffered,
        "client_id": f"evey-{os.getpid()}",
    })


def register(ctx):
    _connect()
    ctx.register_tool(name="mqtt_subscribe", toolset="evey_mqtt", schema=SUBSCRIBE_SCHEMA, handler=handle_subscribe)
    ctx.register_tool(name="mqtt_publish_event", toolset="evey_mqtt", schema=PUBLISH_SCHEMA, handler=handle_publish)
    ctx.register_tool(name="mqtt_status", toolset="evey_mqtt", schema=STATUS_SCHEMA, handler=handle_status)
